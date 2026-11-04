import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import mysql.connector
import csv
import os
import hashlib
import hmac
import secrets
from decimal import Decimal, InvalidOperation

# ============================================================
# AGRICROP INVENTORY - Python + Tkinter + MySQL/MariaDB
# Products: Seeds, Fertilizer, Feed, Pesticides
# The admin enters a unique code, price, and quantity per product.
#
# 1. python -m pip install mysql-connector-python
# 2. Set DB_USER / DB_PASSWORD below (XAMPP default: root, empty).
# 3. python agricrop_app.py
# 4. Login: admin / admin123
# The database, tables, and admin account are created automatically.
# ============================================================

DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = ""
DB_NAME = "agricrop"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
RESET_ADMIN_PASSWORD = False  # True = reset admin to ADMIN_PASSWORD on every start

CATEGORIES = ("Seeds", "Fertilizer", "Feed", "Pesticides")
CODE_PREFIX = {"Seeds": "SD", "Fertilizer": "FT", "Feed": "FD", "Pesticides": "PS"}
UNITS = ("kg", "g", "L", "mL", "sack", "bag", "pack", "pcs")

# ---------------- COLORS (from the SmartFarm reference) ----------------
GREEN, GREEN_DK, DARK = "#4caf50", "#1e6b3a", "#2d363e"
PAGE, WHITE, INK, MUTED = "#f4f7f4", "#ffffff", "#2b3640", "#8a949c"
TILE, LINE, WARN, RED = "#f1f4f1", "#e3e9e3", "#e08a1e", "#d9534f"
SIDE, SIDE_HOVER, SIDE_ACTIVE = "#0a2b20", "#14412f", "#346545"
FONT, FONT_B, FONT_S = ("Segoe UI", 10), ("Segoe UI", 10, "bold"), ("Segoe UI", 9)

COLUMNS = ("product_id", "product_code", "product_name", "category", "unit",
           "unit_price", "quantity", "reorder_level", "supplier")
HEADINGS = ("ID", "Code", "Product Name", "Category", "Unit",
            "Price (PHP)", "Stock", "Reorder At", "Supplier")
HISTORY_COLUMNS = ("movement_id", "product_code", "product_name", "movement_type",
                   "quantity", "before_stock", "after_stock", "note", "username", "created_at")
HISTORY_HEADINGS = ("ID", "Code", "Product Name", "Type", "Qty",
                    "Before", "After", "Note", "Recorded By", "Date / Time")


# ---------------- DATABASE ----------------
def get_connection():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, connection_timeout=5
    )


def close_database(connection, cursor):
    if cursor is not None:
        cursor.close()
    if connection is not None and connection.is_connected():
        connection.close()


def db(work, parent=None):
    """Run work(cursor) as one transaction. Returns (ok, result).
    work() may raise ValueError("message") to stop with a friendly warning."""
    options = {"parent": parent} if parent else {}
    connection = cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        result = work(cursor)
        connection.commit()
        return True, result
    except ValueError as warning:
        if connection is not None:
            connection.rollback()
        messagebox.showwarning("Cannot continue", str(warning), **options)
    except mysql.connector.Error as error:
        if connection is not None:
            connection.rollback()
        if error.errno == 1062:
            messagebox.showwarning("Duplicate Code", "This product code is already used. Enter a different code.", **options)
        else:
            messagebox.showerror("Database Error", "Check that MySQL is running and the DB settings "
                                 "in the app file are correct.\n\n" + str(error), **options)
    finally:
        close_database(connection, cursor)
    return False, None


def select(sql, params=()):
    return db(lambda cursor: (cursor.execute(sql, params), cursor.fetchall())[1])


PASSWORD_ITERATIONS = 120000


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                               PASSWORD_ITERATIONS).hex()


def password_matches(password, salt, saved_hash):
    try:
        return hmac.compare_digest(hash_password(password, salt), saved_hash)
    except ValueError:
        return False


def setup_database():
    """Create the database, tables, and admin account if they do not exist."""
    connection = cursor = None
    try:
        connection = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                                             password=DB_PASSWORD, connection_timeout=5)
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4")
        cursor.execute(f"USE `{DB_NAME}`")
        cursor.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password_salt VARCHAR(64) NOT NULL,
            password_hash VARCHAR(128) NOT NULL)""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS products (
            product_id INT AUTO_INCREMENT PRIMARY KEY,
            product_code VARCHAR(30) NOT NULL UNIQUE,
            product_name VARCHAR(100) NOT NULL,
            category VARCHAR(50) NOT NULL,
            unit VARCHAR(20) NOT NULL,
            unit_price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
            quantity INT NOT NULL DEFAULT 0,
            reorder_level INT NOT NULL DEFAULT 5,
            supplier VARCHAR(100) NOT NULL DEFAULT '',
            status VARCHAR(10) NOT NULL DEFAULT 'Active')""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS stock_movements (
            movement_id INT AUTO_INCREMENT PRIMARY KEY,
            product_id INT NOT NULL,
            movement_type VARCHAR(10) NOT NULL,
            quantity INT NOT NULL,
            before_stock INT NOT NULL,
            after_stock INT NOT NULL,
            note VARCHAR(255) NOT NULL DEFAULT '',
            user_id INT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(product_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id))""")
        cursor.execute("SELECT user_id FROM users WHERE username = %s", (ADMIN_USERNAME,))
        admin = cursor.fetchone()
        salt = secrets.token_hex(16)
        password_hash = hash_password(ADMIN_PASSWORD, salt)
        if admin is None:
            cursor.execute("INSERT INTO users (username, password_salt, password_hash) VALUES (%s, %s, %s)",
                           (ADMIN_USERNAME, salt, password_hash))
        elif RESET_ADMIN_PASSWORD:
            cursor.execute("UPDATE users SET password_salt = %s, password_hash = %s WHERE user_id = %s",
                           (salt, password_hash, admin[0]))
        connection.commit()
    except mysql.connector.Error as error:
        messagebox.showerror("Database Setup Error", "Could not set up the database. Check that MySQL "
                             "is running and DB_USER / DB_PASSWORD are correct.\n\n" + str(error))
    finally:
        close_database(connection, cursor)


# ---------------- SESSION STATE ----------------
current_user_id = None
selected_product_id = None
current_page = "dashboard"
chart_data = {}


# ---------------- LOGIN / LOGOUT ----------------
def login():
    global current_user_id
    username, password = username_var.get().strip(), password_var.get()
    if not username or not password:
        messagebox.showwarning("Missing Login", "Enter your username and password.")
        return

    def work(cursor):
        cursor.execute("SELECT user_id, username, password_salt, password_hash FROM users WHERE username = %s",
                       (username,))
        return cursor.fetchone()

    ok, user = db(work)
    if not ok:
        return
    if user is None or not password_matches(password, user[2], user[3]):
        password_var.set("")
        messagebox.showwarning("Login Failed", "Incorrect username or password.")
        return
    current_user_id = user[0]
    user_name_var.set(user[1])
    profile_text_var.set("\U0001F464  " + user[1].title() + "   \u25BE")
    password_var.set("")
    show_password_var.set(False)
    toggle_password()
    login_frame.pack_forget()
    main_frame.pack(fill="both", expand=True)
    show_page("dashboard")
    load_products()
    load_history()


def logout():
    global current_user_id
    if not messagebox.askyesno("Logout", "Log out of the system?"):
        return
    current_user_id = None
    clear_form()
    tree.delete(*tree.get_children())
    history_tree.delete(*history_tree.get_children())
    main_frame.pack_forget()
    username_var.set("")
    password_var.set("")
    login_frame.pack(fill="both", expand=True)
    username_entry.focus_set()


def toggle_password():
    password_entry.configure(show="" if show_password_var.get() else "*")


def exit_program():
    if messagebox.askyesno("Exit", "Close AgriCrop Inventory?"):
        root.destroy()


# ---------------- DASHBOARD ----------------
def load_dashboard():
    if current_user_id is None:
        return
    ok, rows = select("""
        SELECT category, COUNT(*), COALESCE(SUM(quantity), 0), COALESCE(SUM(quantity * unit_price), 0),
               COALESCE(SUM(quantity > 0 AND quantity <= reorder_level), 0), COALESCE(SUM(quantity = 0), 0)
        FROM products GROUP BY category""")
    if not ok:
        return
    ok, alerts = select("""SELECT product_code, product_name, quantity, reorder_level FROM products
                           WHERE quantity <= reorder_level ORDER BY quantity ASC LIMIT 5""")
    if not ok:
        return
    by_category = {row[0]: row for row in rows}
    for category in CATEGORIES:
        row = by_category.get(category, (category, 0, 0, 0, 0, 0))
        value_vars[category].set(str(row[1]))
        note_vars[category].set(f"{int(row[2]):,} units in stock")
    count = sum(row[1] for row in rows)
    units = sum(int(row[2]) for row in rows)
    worth = sum(row[3] for row in rows)
    low = sum(int(row[4]) for row in rows)
    out = sum(int(row[5]) for row in rows)
    value_vars["total"].set(str(count))
    note_vars["total"].set("across 4 categories")
    value_vars["units"].set(f"{units:,}")
    note_vars["units"].set("all products")
    value_vars["value"].set(f"{worth:,.2f}")
    note_vars["value"].set("PHP, price x stock")
    value_vars["low"].set(str(low))
    note_vars["low"].set("at or below reorder level")
    value_vars["out"].set(str(out))
    note_vars["out"].set("need restocking now")
    chart_data.update(healthy=count - low - out, low=low + out,
                      cats=[(name, int(by_category[name][2]) if name in by_category else 0) for name in CATEGORIES],
                      alerts=alerts)
    draw_charts()


def draw_donut(canvas):
    canvas.delete("all")
    width, height = canvas.winfo_width(), canvas.winfo_height()
    healthy, low = chart_data["healthy"], chart_data["low"]
    total = healthy + low
    radius = max(min(height // 2 - 16, width // 3), 20)
    cx, cy = radius + 24, height // 2
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    if total == 0:
        canvas.create_oval(*box, fill=LINE, outline="")
    elif low == 0 or healthy == 0:
        canvas.create_oval(*box, fill=GREEN if low == 0 else DARK, outline="")
    else:
        angle = 360 * healthy / total
        canvas.create_arc(*box, start=90, extent=-angle, fill=GREEN, outline="")
        canvas.create_arc(*box, start=90 - angle, extent=-(360 - angle), fill=DARK, outline="")
    hole = radius * 0.5
    canvas.create_oval(cx - hole, cy - hole, cx + hole, cy + hole, fill=WHITE, outline="")
    for index, (text, number, color) in enumerate((("Healthy stock", healthy, GREEN), ("Low or out", low, DARK))):
        y = cy - 30 + index * 62
        canvas.create_text(cx + radius + 28, y, text=f"{round(100 * number / total) if total else 0}%",
                           anchor="w", fill=color, font=("Segoe UI", 20, "bold"))
        canvas.create_text(cx + radius + 28, y + 26, text=text, anchor="w", fill=MUTED, font=FONT_S)


def draw_bars(canvas, rows, empty_text=""):
    canvas.delete("all")
    width = canvas.winfo_width()
    if not rows:
        canvas.create_text(width // 2, canvas.winfo_height() // 2, text=empty_text, fill=MUTED, font=FONT)
        return
    left = 100
    span = max(width - left - 16, 60)
    for index, (name, percent, color, text) in enumerate(rows):
        y = 26 + index * 44
        length = max(span * percent / 100, 4) if percent > 0 else 0
        canvas.create_text(4, y, text=name, anchor="w", fill=MUTED, font=FONT_S)
        canvas.create_rectangle(left, y - 12, left + span, y + 12, fill=PAGE, outline="")
        if length:
            canvas.create_rectangle(left, y - 12, left + length, y + 12, fill=color, outline="")
        inside = length > span - 90
        canvas.create_text(left + (length - 8 if inside else length + 8) if length else left + 8, y, text=text,
                           anchor="e" if inside else "w", fill=WHITE if inside else INK, font=FONT_B)


def draw_charts(event=None):
    if not chart_data:
        return
    draw_donut(donut_canvas)
    total = sum(units for _, units in chart_data["cats"])
    colors = (GREEN, GREEN_DK, "#8a8f85", DARK)
    draw_bars(category_canvas, [
        (name, 100 * units / total if total else 0, colors[index],
         f"{round(100 * units / total) if total else 0}%  ({units:,})")
        for index, (name, units) in enumerate(chart_data["cats"])
    ])
    draw_bars(alert_canvas, [
        (str(name)[:13], min(quantity / max(reorder, 1), 1) * 100, RED if quantity == 0 else WARN,
         f"{quantity} / {reorder}")
        for code, name, quantity, reorder in chart_data["alerts"]
    ], "All stock levels are healthy.")


# ---------------- PRODUCT FORM ----------------
def clear_form():
    global selected_product_id
    selected_product_id = None
    form_title_var.set("New product")
    quantity_entry.configure(state="normal")
    for variable in product_vars:
        variable.set("")
    price_var.set("0.00")
    qty_var.set("0")
    reorder_var.set("10")
    unit_var.set("kg")
    tree.selection_remove(*tree.selection())


def valid_integer(text):
    return text.isdecimal() and len(text) <= 7 and int(text) <= 1000000


def validate_form():
    code, name, category, unit, price_text, quantity, reorder, supplier = (v.get().strip() for v in product_vars)
    if not (code and name and category and unit):
        messagebox.showwarning("Missing Data", "Product Code, Name, Category, and Unit are required.")
        return None
    if category not in CATEGORIES:
        messagebox.showwarning("Invalid Category", "Choose Seeds, Fertilizer, Feed, or Pesticides.")
        return None
    for value, limit, label in ((code, 30, "Product Code"), (name, 100, "Product Name"),
                                (unit, 20, "Unit"), (supplier, 100, "Supplier")):
        if len(value) > limit:
            messagebox.showwarning("Too Long", f"{label}: maximum {limit} characters.")
            return None
    if not valid_integer(quantity) or not valid_integer(reorder):
        messagebox.showwarning("Invalid Number", "Stock and Reorder Level must be whole numbers from 0 to 1,000,000.")
        return None
    try:
        price = Decimal(price_text)
        if not price.is_finite() or not 0 <= price <= Decimal("9999999.99") or price != price.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError):
        messagebox.showwarning("Invalid Price", "Enter a price from 0 to 9,999,999.99 with at most 2 decimals.")
        return None
    return (code, name, category, unit, price, int(quantity), int(reorder), supplier)


def auto_code():
    prefix = CODE_PREFIX.get(category_var.get())
    if prefix is None:
        messagebox.showwarning("Choose Category", "Choose a category first, then click Auto.")
        return
    ok, rows = select("SELECT product_code FROM products WHERE product_code LIKE %s", (prefix + "-%",))
    if ok:
        numbers = [int(r[0][len(prefix) + 1:]) for r in rows if r[0][len(prefix) + 1:].isdigit()]
        code_var.set(f"{prefix}-{max(numbers, default=0) + 1:04d}")


def on_tree_select(event=None):
    global selected_product_id
    selected = tree.selection()
    if not selected:
        return
    values = tree.item(selected[0], "values")
    selected_product_id = int(values[0])
    form_title_var.set("Edit product")
    for variable, value in zip(product_vars, values[1:]):
        variable.set(value)
    quantity_entry.configure(state="readonly")  # existing stock changes only through Stock In / Out


# ---------------- PRODUCTS: READ / CREATE / UPDATE / DELETE ----------------
def load_products():
    if current_user_id is None:
        return
    pattern = "%" + search_var.get().strip() + "%"
    sql = """SELECT product_id, product_code, product_name, category, unit, unit_price,
                    quantity, reorder_level, supplier
             FROM products WHERE (product_code LIKE %s OR product_name LIKE %s OR supplier LIKE %s)"""
    params = [pattern, pattern, pattern]
    if category_filter_var.get() != "All":
        sql += " AND category = %s"
        params.append(category_filter_var.get())
    if stock_filter_var.get() == "Low stock":
        sql += " AND quantity > 0 AND quantity <= reorder_level"
    elif stock_filter_var.get() == "Out of stock":
        sql += " AND quantity = 0"
    ok, rows = select(sql + " ORDER BY product_id DESC", params)
    if not ok:
        return
    clear_form()
    tree.delete(*tree.get_children())
    for index, row in enumerate(rows):
        tag = "out" if row[6] == 0 else "low" if row[6] <= row[7] else "even" if index % 2 == 0 else "odd"
        tree.insert("", "end", values=row, tags=(tag,))
    displayed_var.set(f"{len(rows)} products displayed")


def reset_filters():
    search_var.set("")
    category_filter_var.set("All")
    stock_filter_var.set("All")
    load_products()


def refresh_all():
    load_products()
    load_history()
    load_dashboard()


def add_product():
    if current_user_id is None:
        return
    values = validate_form()
    if values is None:
        return

    def work(cursor):
        cursor.execute("""INSERT INTO products (product_code, product_name, category, unit, unit_price,
                          quantity, reorder_level, supplier) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", values)
        if values[5] > 0:
            cursor.execute("""INSERT INTO stock_movements (product_id, movement_type, quantity, before_stock,
                              after_stock, note, user_id) VALUES (%s, 'Opening', %s, 0, %s, 'Opening stock', %s)""",
                           (cursor.lastrowid, values[5], values[5], current_user_id))

    if db(work)[0]:
        messagebox.showinfo("Success", "Product added successfully.")
        refresh_all()

def update_product():
    if current_user_id is None:
        return
    if selected_product_id is None:
        messagebox.showwarning("Select Product", "Select a product from the table first.")
        return
    values = validate_form()
    if values is None:
        return

    def work(cursor):
        cursor.execute("""UPDATE products SET product_code = %s, product_name = %s, category = %s, unit = %s,
                          unit_price = %s, reorder_level = %s, supplier = %s WHERE product_id = %s""",
                       values[:5] + values[6:] + (selected_product_id,))
        if cursor.rowcount == 0:
            cursor.execute("SELECT product_id FROM products WHERE product_id = %s", (selected_product_id,))
            if cursor.fetchone() is None:
                raise ValueError("This product has already been removed.")

    if db(work)[0]:
        messagebox.showinfo("Success", "Product updated successfully.")
        refresh_all()


def delete_product():
    if current_user_id is None:
        return
    if selected_product_id is None:
        messagebox.showwarning("Select Product", "Select a product from the table first.")
        return
    product_id = selected_product_id
    if not messagebox.askyesno("Confirm Delete", f"Delete {code_var.get()} and its stock history?"):
        return

    def work(cursor):
        cursor.execute("DELETE FROM stock_movements WHERE product_id = %s", (product_id,))
        cursor.execute("DELETE FROM products WHERE product_id = %s", (product_id,))

    if db(work)[0]:
        messagebox.showinfo("Success", "Product deleted.")
        refresh_all()


# ---------------- STOCK IN / OUT ----------------
def open_stock_dialog(kind):
    if current_user_id is None:
        return
    if selected_product_id is None:
        messagebox.showwarning("Select Product", "Select a product from the table first.")
        return
    product_id = selected_product_id
    window = tk.Toplevel(root, bg=WHITE)
    window.title("Stock " + kind)
    window.geometry(f"440x320+{root.winfo_rootx() + (root.winfo_width() - 440) // 2}+{root.winfo_rooty() + 180}")
    window.resizable(False, False)
    window.transient(root)
    window.grab_set()
    panel = tk.Frame(window, bg=WHITE, padx=26, pady=22)
    panel.pack(fill="both", expand=True)
    amount_var, note_var = tk.StringVar(), tk.StringVar()
    label(panel, "Stock In (receive)" if kind == "IN" else "Stock Out (release)", 16, True).pack(anchor="w")
    label(panel, code_var.get() + "  /  " + name_var.get(), 10, False, MUTED, wraplength=380).pack(anchor="w", pady=(2, 14))
    label(panel, "Quantity (whole number)").pack(anchor="w")
    entry = ttk.Entry(panel, textvariable=amount_var)
    entry.pack(fill="x", pady=(4, 10))
    entry.focus_set()
    label(panel, "Note / Reference (optional)").pack(anchor="w")
    ttk.Entry(panel, textvariable=note_var).pack(fill="x", pady=(4, 16))

    def save_movement():
        text, note = amount_var.get().strip(), note_var.get().strip()
        if not valid_integer(text) or int(text) == 0:
            messagebox.showwarning("Invalid Quantity", "Enter a whole number from 1 to 1,000,000.", parent=window)
            return
        if len(note) > 255:
            messagebox.showwarning("Too Long", "Note: maximum 255 characters.", parent=window)
            return
        amount = int(text)

        def work(cursor):
            cursor.execute("SELECT quantity FROM products WHERE product_id = %s FOR UPDATE", (product_id,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError("This product no longer exists.")
            before = row[0]
            after = before + amount if kind == "IN" else before - amount
            if after < 0:
                raise ValueError(f"Available stock: {before}. Cannot release {amount}.")
            if after > 1000000:
                raise ValueError("Maximum stock per product is 1,000,000.")
            cursor.execute("UPDATE products SET quantity = %s WHERE product_id = %s", (after, product_id))
            cursor.execute("""INSERT INTO stock_movements (product_id, movement_type, quantity, before_stock,
                              after_stock, note, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                           (product_id, kind, amount, before, after, note, current_user_id))
            return after

        ok, after = db(work, parent=window)
        if ok:
            messagebox.showinfo("Success", f"Stock {kind} saved. New stock: {after}.", parent=window)
            window.destroy()
            refresh_all()

    actions = tk.Frame(panel, bg=WHITE)
    actions.pack(fill="x")
    ttk.Button(actions, text="Save stock", style="Primary.TButton", command=save_movement).pack(side="left")
    ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right")


# ---------------- STOCK HISTORY ----------------
def load_history():
    if current_user_id is None:
        return
    pattern = "%" + history_search_var.get().strip() + "%"
    sql = """SELECT m.movement_id, p.product_code, p.product_name, m.movement_type, m.quantity,
                    m.before_stock, m.after_stock, m.note, u.username, m.created_at
             FROM stock_movements m JOIN products p ON m.product_id = p.product_id
             JOIN users u ON m.user_id = u.user_id
             WHERE (p.product_code LIKE %s OR p.product_name LIKE %s OR m.note LIKE %s)"""
    params = [pattern, pattern, pattern]
    if history_type_var.get() != "All":
        sql += " AND m.movement_type = %s"
        params.append(history_type_var.get())
    ok, rows = select(sql + " ORDER BY m.movement_id DESC", params)
    if not ok:
        return
    history_tree.delete(*history_tree.get_children())
    for index, row in enumerate(rows):
        history_tree.insert("", "end", values=row, tags=("even" if index % 2 == 0 else "odd",))
    history_count_var.set(f"{len(rows)} movements displayed")


def reset_history():
    history_search_var.set("")
    history_type_var.set("All")
    load_history()


# ---------------- CSV EXPORT / PASSWORD ----------------
def export_csv():
    if current_user_id is None:
        return
    is_history = current_page == "history"
    table, headings = (history_tree, HISTORY_HEADINGS) if is_history else (tree, HEADINGS)
    items = table.get_children()
    if not items:
        messagebox.showwarning("No Records", "There are no displayed records to export.")
        return
    filename = filedialog.asksaveasfilename(
        title="Export Displayed Records", defaultextension=".csv",
        initialfile="stock_history.csv" if is_history else "products.csv", filetypes=[("CSV files", "*.csv")])
    if not filename:
        return
    try:
        with open(filename, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(headings)
            for item in items:
                writer.writerow(["'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                                 for v in table.item(item, "values")])
        messagebox.showinfo("Export Complete", f"Exported {len(items)} record(s).\n\n{filename}")
    except OSError as error:
        messagebox.showerror("Export Error", str(error))


def change_password():
    if current_user_id is None:
        return
    window = tk.Toplevel(root, bg=WHITE)
    window.title("Change Password")
    window.geometry(f"420x340+{root.winfo_rootx() + (root.winfo_width() - 420) // 2}+{root.winfo_rooty() + 160}")
    window.resizable(False, False)
    window.transient(root)
    window.grab_set()
    panel = tk.Frame(window, bg=WHITE, padx=26, pady=22)
    panel.pack(fill="both", expand=True)
    label(panel, "Change password", 16, True).pack(anchor="w", pady=(0, 12))
    variables = []
    for text in ("Current password", "New password (6 to 128 characters)", "Confirm new password"):
        label(panel, text).pack(anchor="w")
        variable = tk.StringVar()
        ttk.Entry(panel, textvariable=variable, show="*").pack(fill="x", pady=(4, 10))
        variables.append(variable)
    old_var, new_var, confirm_var = variables

    def save_password():
        new_password = new_var.get()
        if not 6 <= len(new_password) <= 128 or not new_password.strip():
            messagebox.showwarning("Invalid Password", "Use 6 to 128 characters.", parent=window)
            return
        if new_password != confirm_var.get():
            messagebox.showwarning("Mismatch", "New passwords do not match.", parent=window)
            return

        def work(cursor):
            cursor.execute("SELECT password_salt, password_hash FROM users WHERE user_id = %s", (current_user_id,))
            user = cursor.fetchone()
            if user is None or not password_matches(old_var.get(), user[0], user[1]):
                raise ValueError("Current password is incorrect.")
            salt = secrets.token_hex(16)
            cursor.execute("UPDATE users SET password_salt = %s, password_hash = %s WHERE user_id = %s",
                           (salt, hash_password(new_password, salt), current_user_id))

        if db(work, parent=window)[0]:
            messagebox.showinfo("Success", "Password changed.", parent=window)
            window.destroy()

    actions = tk.Frame(panel, bg=WHITE)
    actions.pack(fill="x", pady=(6, 0))
    ttk.Button(actions, text="Save password", style="Primary.TButton", command=save_password).pack(side="left")
    ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right")


# ---------------- WINDOW + THEME ----------------
root = tk.Tk()
root.title("AgriCrop Inventory")
root.geometry("1366x768")
root.minsize(1100, 680)
try:
    root.state("zoomed")
except tk.TclError:
    pass
root.configure(background=PAGE)
root.protocol("WM_DELETE_WINDOW", exit_program)
root.option_add("*TCombobox*Listbox.selectBackground", GREEN)
root.option_add("*TCombobox*Listbox.font", FONT)

style = ttk.Style(root)
style.theme_use("clam")
style.configure(".", font=FONT)
style.configure("TEntry", fieldbackground=WHITE, foreground=INK, bordercolor=LINE, lightcolor=LINE,
                darkcolor=LINE, padding=6)
style.map("TEntry", bordercolor=[("focus", GREEN)], lightcolor=[("focus", GREEN)],
          darkcolor=[("focus", GREEN)], fieldbackground=[("readonly", TILE)])
style.configure("TCombobox", fieldbackground=WHITE, background=WHITE, foreground=INK, bordercolor=LINE,
                lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED, padding=5)
style.map("TCombobox", fieldbackground=[("readonly", WHITE)], bordercolor=[("focus", GREEN)],
          selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
style.configure("TButton", font=FONT_B, background=WHITE, foreground=INK, bordercolor=LINE,
                lightcolor=LINE, darkcolor=LINE, padding=(12, 7))
style.map("TButton", background=[("active", "#eef3ee")])
for name, color, hover in (("Primary", GREEN, "#3f9443"), ("Dark", DARK, "#1f272d"), ("Danger", RED, "#b94441")):
    style.configure(name + ".TButton", background=color, foreground=WHITE, bordercolor=color,
                    lightcolor=color, darkcolor=color)
    style.map(name + ".TButton", background=[("active", hover), ("pressed", hover)])
style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK, borderwidth=0,
                rowheight=36, font=FONT)
style.configure("Treeview.Heading", background=TILE, foreground="#4b5a52", font=FONT_B,
                padding=(8, 9), relief="flat")
style.map("Treeview", background=[("selected", "#d9efdb")], foreground=[("selected", INK)])
style.map("Treeview.Heading", background=[("active", LINE)])
for orientation in ("Vertical", "Horizontal"):
    style.configure(orientation + ".TScrollbar", background="#cbd5cc", troughcolor=PAGE,
                    bordercolor=PAGE, arrowcolor=MUTED)


def label(parent, text="", size=10, bold=False, color=INK, bg=WHITE, **options):
    return tk.Label(parent, text=text, font=("Segoe UI", size, "bold" if bold else "normal"),
                    fg=color, bg=bg, **options)


def card(parent, **options):
    return tk.Frame(parent, bg=WHITE, highlightthickness=1, highlightbackground=LINE, **options)


# ---------------- TK VARIABLES ----------------
username_var, password_var = tk.StringVar(), tk.StringVar()
show_password_var = tk.BooleanVar(value=False)
user_name_var = tk.StringVar()
profile_text_var = tk.StringVar()
form_title_var = tk.StringVar(value="New product")
code_var, name_var, category_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
unit_var, price_var = tk.StringVar(value="kg"), tk.StringVar(value="0.00")
qty_var, reorder_var, supplier_var = tk.StringVar(value="0"), tk.StringVar(value="10"), tk.StringVar()
product_vars = (code_var, name_var, category_var, unit_var, price_var, qty_var, reorder_var, supplier_var)
search_var, category_filter_var = tk.StringVar(), tk.StringVar(value="All")
stock_filter_var = tk.StringVar(value="All")
history_search_var, history_type_var = tk.StringVar(), tk.StringVar(value="All")
displayed_var = tk.StringVar(value="0 products displayed")
history_count_var = tk.StringVar(value="0 movements displayed")
value_vars, note_vars = {}, {}

# ---------------- LOGIN SCREEN ----------------
login_frame = tk.Frame(root, bg=PAGE)
login_frame.pack(fill="both", expand=True)
login_card = card(login_frame, width=860, height=460)
login_card.place(relx=0.5, rely=0.5, anchor="center")
login_card.pack_propagate(False)
brand = tk.Frame(login_card, bg=DARK, width=340)
brand.pack(side="left", fill="y")
brand.pack_propagate(False)
label(brand, "AgriCrop", 24, True, WHITE, DARK).pack(anchor="w", padx=34, pady=(40, 0))
label(brand, "Inventory\nManagement", 28, True, WHITE, DARK, justify="left").pack(anchor="w", padx=34, pady=(60, 0))
label(brand, "Seeds, fertilizer, feed, and pesticides.\nEvery code. Every quantity.", 11, False, "#9fb3a5", DARK,
      justify="left").pack(anchor="w", padx=34, pady=(16, 0))
panel = tk.Frame(login_card, bg=WHITE, padx=44, pady=40)
panel.pack(side="left", fill="both", expand=True)
label(panel, "Welcome back", 24, True).pack(anchor="w")
label(panel, "Sign in as administrator.", 10, False, MUTED).pack(anchor="w", pady=(4, 22))
label(panel, "Username").pack(anchor="w", pady=(0, 4))
username_entry = ttk.Entry(panel, textvariable=username_var)
username_entry.pack(fill="x", pady=(0, 14))
label(panel, "Password").pack(anchor="w", pady=(0, 4))
password_entry = ttk.Entry(panel, textvariable=password_var, show="*")
password_entry.pack(fill="x", pady=(0, 8))
tk.Checkbutton(panel, text="Show password", variable=show_password_var, command=toggle_password, bg=WHITE,
               fg=MUTED, activebackground=WHITE, selectcolor=WHITE, bd=0, font=FONT_S).pack(anchor="w", pady=(0, 18))
ttk.Button(panel, text="Sign in", style="Primary.TButton", command=login).pack(fill="x")
ttk.Button(panel, text="Exit application", command=exit_program).pack(fill="x", pady=(10, 0))
username_entry.bind("<Return>", lambda event: login())
password_entry.bind("<Return>", lambda event: login())

# ---------------- MAIN LAYOUT: SIDEBAR (full height) + HEADER + PAGES ----------------
main_frame = tk.Frame(root, bg=PAGE)

# Sidebar is packed first so it runs the full height of the window
sidebar = tk.Frame(main_frame, bg=SIDE, width=250)
sidebar.pack(side="left", fill="y")
sidebar.pack_propagate(False)

# Header and pages sit to the right of the sidebar
right_side = tk.Frame(main_frame, bg=PAGE)
right_side.pack(side="left", fill="both", expand=True)

header = tk.Frame(right_side, bg=WHITE, height=56, highlightthickness=1, highlightbackground=LINE)
header.pack(fill="x")
header.pack_propagate(False)
profile_button = tk.Menubutton(header, textvariable=profile_text_var, font=FONT_B, bg=WHITE, fg=INK,
                               relief="flat", padx=12, pady=6, cursor="hand2", activebackground=TILE)
profile_menu = tk.Menu(profile_button, tearoff=0, font=FONT)
profile_menu.add_command(label="Change Password", command=change_password)
profile_menu.add_separator()
profile_menu.add_command(label="Logout", command=logout)
profile_button.configure(menu=profile_menu)
profile_button.pack(side="right", padx=22)

content = tk.Frame(right_side, bg=PAGE)
content.pack(fill="both", expand=True)
content.rowconfigure(0, weight=1)
content.columnconfigure(0, weight=1)
pages = {name: tk.Frame(content, bg=PAGE, padx=28, pady=20) for name in ("dashboard", "products", "history", "about")}
for page in pages.values():
    page.grid(row=0, column=0, sticky="nsew")

nav_buttons = {}


def show_page(name):
    global current_page
    current_page = name
    pages[name].tkraise()
    for key, button in nav_buttons.items():
        active = key == name
        button.configure(bg=SIDE_ACTIVE if active else SIDE, activebackground=SIDE_ACTIVE if active else SIDE_HOVER,
                         fg=WHITE if active else "#d5e3d9")
    if name == "dashboard":
        load_dashboard()
    elif name == "history":
        load_history()


def nav_button(text, command, key=None):
    button = tk.Button(sidebar, text=text, command=command, anchor="w", relief="flat", bd=0, bg=SIDE,
                       fg="#d5e3d9", activebackground=SIDE_HOVER, activeforeground=WHITE, font=FONT,
                       padx=18, pady=12, cursor="hand2")
    button.pack(fill="x", padx=14, pady=2)
    if key:
        nav_buttons[key] = button


logo = tk.Frame(sidebar, bg=SIDE)
logo.pack(fill="x", padx=22, pady=(24, 22))
tk.Label(logo, text="\U0001F33F", font=("Segoe UI Emoji", 26), bg=SIDE, fg="#8fd19e").pack(side="left")
logo_text = tk.Frame(logo, bg=SIDE)
logo_text.pack(side="left", padx=(8, 0))
label(logo_text, "AgriCrop", 20, True, WHITE, SIDE).pack(anchor="w")
label(logo_text, "Inventory", 11, False, "#cfe0d4", SIDE).pack(anchor="w")
label(sidebar, "Tamarind Inventory System\nPSAU", 9, False, "#cfe0d4", SIDE, justify="left").pack(
    side="bottom", anchor="w", padx=22, pady=20)
nav_button("\u2302    Dashboard", lambda: show_page("dashboard"), "dashboard")
nav_button("\u2756    Products", lambda: show_page("products"), "products")
nav_button("\u21BB    Stock History", lambda: show_page("history"), "history")
nav_button("\u2193    Export CSV", export_csv)
nav_button("\u24D8    About", lambda: show_page("about"), "about")
tk.Frame(sidebar, bg=SIDE, height=18).pack()
nav_button("\u270E    Change Password", change_password)
nav_button("\u21AA    Logout", logout)
nav_button("\u2716    Exit application", exit_program)

# ---------------- DASHBOARD PAGE ----------------
dash = pages["dashboard"]
label(dash, "Inventory Overview", 18, True, bg=PAGE).pack(anchor="w", pady=(0, 14))


def stat_row(items):
    row = tk.Frame(dash, bg=PAGE)
    row.pack(fill="x", pady=(0, 14))
    for column, (key, icon, title) in enumerate(items):
        row.columnconfigure(column, weight=1, uniform="stat")
        value_vars[key], note_vars[key] = tk.StringVar(value="0"), tk.StringVar()
        box = card(row)
        box.grid(row=0, column=column, sticky="nsew", padx=(0, 12 if column < len(items) - 1 else 0))
        tk.Label(box, text=icon, font=("Segoe UI Emoji", 16), bg=TILE, fg=INK, width=3).grid(
            row=0, column=0, rowspan=3, padx=14, pady=14, ipady=8)
        label(box, title, 9, False, MUTED).grid(row=0, column=1, sticky="sw", pady=(12, 0))
        label(box, "", 20, True, textvariable=value_vars[key]).grid(row=1, column=1, sticky="w")
        label(box, "", 8, False, GREEN_DK, textvariable=note_vars[key]).grid(row=2, column=1, sticky="nw", pady=(0, 10))


stat_row((("total", "\U0001F4E6", "Products"), ("Seeds", "\U0001F331", "Seeds"),
          ("Fertilizer", "\U0001F9EA", "Fertilizer"), ("Feed", "\U0001F33E", "Feed"),
          ("Pesticides", "\U0001F6E1", "Pesticides")))
stat_row((("units", "\U0001F4CA", "Total stock units"), ("value", "\U0001F4B0", "Stock value"),
          ("low", "\u26A0", "Low stock"), ("out", "\u26D4", "Out of stock")))
label(dash, "Stock Overview", 14, True, bg=PAGE).pack(anchor="w", pady=(6, 10))
charts = tk.Frame(dash, bg=PAGE)
charts.pack(fill="both", expand=True)
canvases = []
for column, title in enumerate(("Healthy vs Low Stock", "Units by Category", "Low Stock Alerts")):
    charts.columnconfigure(column, weight=1, uniform="chart")
    charts.rowconfigure(0, weight=1)
    box = card(charts, padx=16, pady=14)
    box.grid(row=0, column=column, sticky="nsew", padx=(0, 12 if column < 2 else 0))
    label(box, title, 10, True).pack(anchor="w")
    canvas = tk.Canvas(box, height=230, bg=WHITE, highlightthickness=0)
    canvas.pack(fill="both", expand=True, pady=(8, 0))
    canvas.bind("<Configure>", draw_charts)
    canvases.append(canvas)
donut_canvas, category_canvas, alert_canvas = canvases

# ---------------- PRODUCTS PAGE ----------------
prod = pages["products"]
label(prod, "Products", 18, True, bg=PAGE).pack(anchor="w", pady=(0, 12))
prod_body = tk.Frame(prod, bg=PAGE)
prod_body.pack(fill="both", expand=True)
form_card = card(prod_body, padx=16, pady=14)
form_card.pack(side="left", fill="y", padx=(0, 14))
label(form_card, "", 14, True, textvariable=form_title_var).grid(row=0, column=0, columnspan=2, sticky="w")
label(form_card, "Use Stock In / Out to change existing stock.", 9, False, MUTED).grid(
    row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
fields = (("Product Code *", code_var), ("Product Name *", name_var), ("Category *", category_var),
          ("Unit *", unit_var), ("Price (PHP) *", price_var), ("Opening Stock *", qty_var),
          ("Reorder Level *", reorder_var), ("Supplier", supplier_var))
for row, (text, variable) in enumerate(fields, start=2):
    label(form_card, text, 9).grid(row=row, column=0, sticky="w", pady=4)
    if variable is code_var:
        widget = tk.Frame(form_card, bg=WHITE)
        ttk.Entry(widget, textvariable=variable, width=13).pack(side="left", fill="x", expand=True)
        ttk.Button(widget, text="Auto", command=auto_code, padding=(8, 4)).pack(side="left", padx=(6, 0))
    elif variable is category_var:
        widget = ttk.Combobox(form_card, textvariable=variable, values=CATEGORIES, state="readonly", width=20)
    elif variable is unit_var:
        widget = ttk.Combobox(form_card, textvariable=variable, values=UNITS, width=20)
    else:
        widget = ttk.Entry(form_card, textvariable=variable, width=23)
        if variable is qty_var:
            quantity_entry = widget
    widget.grid(row=row, column=1, padx=(10, 0), pady=4, sticky="ew")
label(form_card, "* Required. Each product code must be unique.", 8, False, MUTED).grid(
    row=10, column=0, columnspan=2, sticky="w", pady=(6, 8))
buttons = tk.Frame(form_card, bg=WHITE)
buttons.grid(row=11, column=0, columnspan=2, sticky="ew")
for index, (text, command, button_style) in enumerate((
        ("Add product", add_product, "Primary.TButton"), ("Update", update_product, "Dark.TButton"),
        ("Stock In", lambda: open_stock_dialog("IN"), "Primary.TButton"),
        ("Stock Out", lambda: open_stock_dialog("OUT"), "Dark.TButton"),
        ("Delete", delete_product, "Danger.TButton"), ("Clear form", clear_form, "TButton"))):
    buttons.columnconfigure(index % 2, weight=1)
    ttk.Button(buttons, text=text, command=command, style=button_style).grid(
        row=index // 2, column=index % 2, sticky="ew", padx=(0, 6 if index % 2 == 0 else 0), pady=3)

table_card = card(prod_body, padx=16, pady=14)
table_card.pack(side="left", fill="both", expand=True)
toolbar = tk.Frame(table_card, bg=WHITE)
toolbar.pack(fill="x", pady=(0, 10))
search_entry = ttk.Entry(toolbar, textvariable=search_var)
search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
ttk.Combobox(toolbar, textvariable=category_filter_var, values=("All",) + CATEGORIES,
             state="readonly", width=11).pack(side="left", padx=(0, 8))
ttk.Combobox(toolbar, textvariable=stock_filter_var, values=("All", "Low stock", "Out of stock"),
             state="readonly", width=11).pack(side="left", padx=(0, 8))
ttk.Button(toolbar, text="Search", style="Primary.TButton", command=load_products).pack(side="left", padx=(0, 6))
ttk.Button(toolbar, text="Reset", command=reset_filters).pack(side="left")
table_area = tk.Frame(table_card, bg=WHITE)
table_area.pack(fill="both", expand=True)
table_area.rowconfigure(0, weight=1)
table_area.columnconfigure(0, weight=1)
tree = ttk.Treeview(table_area, columns=COLUMNS, show="headings", selectmode="browse", displaycolumns=COLUMNS[1:])
for column, heading, width in zip(COLUMNS, HEADINGS, (45, 100, 200, 100, 60, 95, 70, 85, 160)):
    tree.heading(column, text=heading)
    tree.column(column, width=width, minwidth=width, stretch=False,
                anchor="center" if column in ("quantity", "reorder_level", "unit") else "w")
tree.tag_configure("even", background=WHITE)
tree.tag_configure("odd", background="#f8fbf8")
tree.tag_configure("low", background="#fff4e0", foreground="#8a5a10")
tree.tag_configure("out", background="#fdeaea", foreground="#a33a37")
tree.grid(row=0, column=0, sticky="nsew")
scroll_y = ttk.Scrollbar(table_area, orient="vertical", command=tree.yview)
scroll_y.grid(row=0, column=1, sticky="ns")
scroll_x = ttk.Scrollbar(table_area, orient="horizontal", command=tree.xview)
scroll_x.grid(row=1, column=0, sticky="ew")
tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
label(table_card, "", 9, False, MUTED, textvariable=displayed_var).pack(anchor="w", pady=(8, 0))
label(table_card, "Orange rows are low stock. Red rows are out of stock.", 9, False, MUTED).pack(anchor="w")
tree.bind("<<TreeviewSelect>>", on_tree_select)
search_entry.bind("<Return>", lambda event: load_products())

# ---------------- STOCK HISTORY PAGE ----------------
hist = pages["history"]
label(hist, "Stock History", 18, True, bg=PAGE).pack(anchor="w", pady=(0, 12))
history_card = card(hist, padx=16, pady=14)
history_card.pack(fill="both", expand=True)
history_bar = tk.Frame(history_card, bg=WHITE)
history_bar.pack(fill="x", pady=(0, 10))
history_search_entry = ttk.Entry(history_bar, textvariable=history_search_var)
history_search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
ttk.Combobox(history_bar, textvariable=history_type_var, values=("All", "Opening", "IN", "OUT"),
             state="readonly", width=9).pack(side="left", padx=(0, 8))
ttk.Button(history_bar, text="Search", style="Primary.TButton", command=load_history).pack(side="left", padx=(0, 6))
ttk.Button(history_bar, text="Reset", command=reset_history).pack(side="left")
history_area = tk.Frame(history_card, bg=WHITE)
history_area.pack(fill="both", expand=True)
history_area.rowconfigure(0, weight=1)
history_area.columnconfigure(0, weight=1)
history_tree = ttk.Treeview(history_area, columns=HISTORY_COLUMNS, show="headings", selectmode="browse",
                            displaycolumns=HISTORY_COLUMNS[1:])
for column, heading, width in zip(HISTORY_COLUMNS, HISTORY_HEADINGS, (45, 100, 200, 80, 70, 70, 70, 230, 110, 160)):
    history_tree.heading(column, text=heading)
    history_tree.column(column, width=width, minwidth=width, stretch=False)
history_tree.tag_configure("even", background=WHITE)
history_tree.tag_configure("odd", background="#f8fbf8")
history_tree.grid(row=0, column=0, sticky="nsew")
history_y = ttk.Scrollbar(history_area, orient="vertical", command=history_tree.yview)
history_y.grid(row=0, column=1, sticky="ns")
history_x = ttk.Scrollbar(history_area, orient="horizontal", command=history_tree.xview)
history_x.grid(row=1, column=0, sticky="ew")
history_tree.configure(yscrollcommand=history_y.set, xscrollcommand=history_x.set)
label(history_card, "", 9, False, MUTED, textvariable=history_count_var).pack(anchor="w", pady=(8, 0))
history_search_entry.bind("<Return>", lambda event: load_history())

# ---------------- ABOUT PAGE ----------------
HERO_BG, ICON_BG = "#f1f2ea", "#dcebdd"
about = pages["about"]
about.configure(padx=0, pady=0)
about_canvas = tk.Canvas(about, bg=PAGE, highlightthickness=0)
about_scroll = ttk.Scrollbar(about, orient="vertical", command=about_canvas.yview)
about_canvas.configure(yscrollcommand=about_scroll.set)
about_scroll.pack(side="right", fill="y")
about_canvas.pack(side="left", fill="both", expand=True)
about_body = tk.Frame(about_canvas, bg=PAGE)
about_window = about_canvas.create_window((0, 0), window=about_body, anchor="nw")
about_body.bind("<Configure>", lambda event: about_canvas.configure(scrollregion=about_canvas.bbox("all")))
about_canvas.bind("<Configure>", lambda event: about_canvas.itemconfigure(about_window, width=event.width))
about_canvas.bind("<Enter>", lambda event: about_canvas.bind_all(
    "<MouseWheel>", lambda wheel: about_canvas.yview_scroll(-1 * (wheel.delta // 120), "units")))
about_canvas.bind("<Leave>", lambda event: about_canvas.unbind_all("<MouseWheel>"))


def load_image(file_name):
    # The two pictures sit in the same folder as this file. The page still works without them.
    try:
        return tk.PhotoImage(file=os.path.join(os.path.dirname(os.path.abspath(__file__)), file_name))
    except tk.TclError:
        return None


hero_image, basket_image = load_image("about_hero.png"), load_image("about_basket.png")

hero = tk.Frame(about_body, bg=HERO_BG, height=232)
hero.pack(fill="x")
hero.pack_propagate(False)
if hero_image:  # placed on the right edge; its left side fades into the page color
    tk.Label(hero, image=hero_image, bg=HERO_BG, bd=0).place(relx=1.0, rely=0, anchor="ne")
hero_text = tk.Frame(hero, bg=HERO_BG)
hero_text.pack(side="left", fill="y", padx=44)
label(hero_text, "\u2666  ABOUT US", 10, True, GREEN_DK, HERO_BG).pack(anchor="w", pady=(34, 8))
title_row = tk.Frame(hero_text, bg=HERO_BG)
title_row.pack(anchor="w")
tk.Frame(title_row, bg=GREEN_DK, width=4).pack(side="left", fill="y")
title_box = tk.Frame(title_row, bg=HERO_BG)
title_box.pack(side="left", padx=(16, 0))
label(title_box, "Tamarind Inventory\nManagement System", 26, True, "#14452c", HERO_BG, justify="left").pack(anchor="w")
label(title_box, "Organize   \u2022   Track   \u2022   Grow", 15, False, "#2f6b47", HERO_BG).pack(anchor="w", pady=(8, 0))

about_row = tk.Frame(about_body, bg=PAGE, padx=36, pady=22)
about_row.pack(fill="x")
about_row.columnconfigure(0, weight=1)
purpose = card(about_row, padx=28, pady=22)
purpose.grid(row=0, column=0, sticky="nsew", padx=(0, 22))
tk.Label(purpose, text="\U0001F33F", font=("Segoe UI Emoji", 18), bg=ICON_BG, width=3).grid(
    row=0, column=0, sticky="n", padx=(0, 18), ipady=6)
purpose_copy = tk.Frame(purpose, bg=WHITE)
purpose_copy.grid(row=0, column=1, sticky="nw")
label(purpose_copy, "Our Purpose", 16, True).pack(anchor="w", pady=(0, 10))
purpose_paragraphs = []
for paragraph in (
        "The Tamarind Inventory Management System is designed to help manage and monitor tamarind stocks "
        "efficiently. It allows users to record tamarind supplies, track available quantities, and organize "
        "inventory information in one system.",
        "Our goal is to make inventory management easier, more organized, and more accurate by providing a "
        "convenient way to monitor stock levels, record incoming and outgoing items, and maintain updated "
        "inventory records.",
        "Through this system, we aim to support better inventory management and help ensure that tamarind "
        "supplies are properly monitored and managed."):
    purpose_paragraphs.append(label(purpose_copy, paragraph, 10, False, "#44544b", justify="left", wraplength=400))
    purpose_paragraphs[-1].pack(anchor="w", pady=(0, 12))
if basket_image:
    tk.Label(about_row, image=basket_image, bd=0, bg=PAGE).grid(row=0, column=1, sticky="n")


def fit_about(event=None):
    # Wrap the purpose text to the room left beside the picture.
    room = about_row.winfo_width() - 72 - 22 - 58 - 92 - (440 if basket_image else 0)
    for paragraph in purpose_paragraphs:
        paragraph.configure(wraplength=max(room, 280))


about_row.bind("<Configure>", fit_about)

works = card(about_body, padx=24, pady=18)
works.pack(fill="x", padx=36, pady=(0, 28))
label(works, "\U0001F33F  What We Do", 14, True).pack(anchor="w", pady=(0, 14))
works_row = tk.Frame(works, bg=WHITE)
works_row.pack(fill="x")
for index, (icon, title, text) in enumerate((
        ("\U0001F4E6", "Track Inventory", "Monitor tamarind stocks in real-time and keep your records updated."),
        ("\U0001F4CA", "Manage Supplies", "Record incoming and outgoing items with ease and accuracy."),
        ("\U0001F6E1", "Ensure Accuracy", "Reduce errors and maintain reliable inventory data at all times."),
        ("\U0001F33F", "Support Agriculture", "Help local growers and businesses make better decisions for a stronger future."))):
    works_row.columnconfigure(index * 2, weight=1, uniform="work")
    if index:
        tk.Frame(works_row, bg=LINE, width=1).grid(row=0, column=index * 2 - 1, sticky="ns", padx=14)
    item = tk.Frame(works_row, bg=WHITE)
    item.grid(row=0, column=index * 2, sticky="nw")
    tk.Label(item, text=icon, font=("Segoe UI Emoji", 14), bg=ICON_BG, width=3).pack(anchor="w", ipady=4)
    label(item, title, 11, True).pack(anchor="w", pady=(10, 4))
    label(item, text, 9, False, "#5b6b62", justify="left", wraplength=190).pack(anchor="w")

# ---------------- START PROGRAM ----------------
if __name__ == "__main__":
    setup_database()
    username_entry.focus_set()
    root.mainloop()