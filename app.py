import os
import sqlite3
from functools import wraps
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"

DB_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-local-development-key"
)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

DATABASE = DB_DIR / "hotel.db"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS menu_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Meals',
                price REAL NOT NULL CHECK(price >= 0),
                image TEXT,
                available INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE NOT NULL,
                total REAL NOT NULL,
                payment_method TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                menu_item_id INTEGER,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                subtotal REAL NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id),
                FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
                    ON DELETE SET NULL
            );
        """)

        admin = db.execute(
            "SELECT id FROM users WHERE username = ?",
            ("admin",)
        ).fetchone()

        if admin is None:
            db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("admin12345"))
            )

        count = db.execute(
            "SELECT COUNT(*) AS total FROM menu_items"
        ).fetchone()["total"]

        if count == 0:
            sample_items = [
                ("Chips", "Fast Food", 150),
                ("Chips and Sausage", "Fast Food", 250),
                ("Beef Burger", "Burgers", 350),
                ("Chicken and Rice", "Meals", 400),
                ("Pilau", "Meals", 250),
                ("Tea", "Drinks", 50),
                ("Soda", "Drinks", 80),
            ]
            db.executemany(
                """INSERT INTO menu_items (name, category, price)
                   VALUES (?, ?, ?)""",
                sample_items
            )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))

        flash("Incorrect username or password.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as db:
        items = db.execute(
            "SELECT * FROM menu_items ORDER BY category, name"
        ).fetchall()

        today = datetime.now().date().isoformat()

        stats = db.execute("""
            SELECT
                COUNT(*) AS order_count,
                COALESCE(SUM(
                    CASE WHEN payment_status = 'PAID'
                         THEN total ELSE 0 END
                ), 0) AS paid_sales,
                COALESCE(SUM(
                    CASE WHEN payment_status = 'UNPAID'
                         THEN total ELSE 0 END
                ), 0) AS unpaid_total
            FROM orders
            WHERE substr(created_at, 1, 10) = ?
        """, (today,)).fetchone()

    return render_template(
        "dashboard.html", items=items, stats=stats
    )


@app.route("/menu")
@login_required
def menu():
    with get_db() as db:
        items = db.execute(
            "SELECT * FROM menu_items ORDER BY category, name"
        ).fetchall()
    return render_template("menu.html", items=items)


@app.route("/menu/add", methods=["POST"])
@login_required
def add_menu_item():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "Meals").strip() or "Meals"

    try:
        price = float(request.form.get("price", ""))
        if price < 0 or price > 1000000:
            raise ValueError
    except ValueError:
        flash("Enter a valid non-negative price.", "error")
        return redirect(url_for("menu"))

    if not name or len(name) > 100 or len(category) > 60:
        flash("Enter a valid food name and category.", "error")
        return redirect(url_for("menu"))

    image_name = None
    image_file = request.files.get("image")

    if image_file and image_file.filename:
        if not allowed_file(image_file.filename):
            flash("Use a JPG, PNG or WEBP image.", "error")
            return redirect(url_for("menu"))

        safe_name = secure_filename(image_file.filename)
        unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}"
        image_file.save(UPLOAD_DIR / unique_name)
        image_name = unique_name

    with get_db() as db:
        db.execute(
            """INSERT INTO menu_items (name, category, price, image)
               VALUES (?, ?, ?, ?)""",
            (name, category, price, image_name)
        )

    flash("Food item added successfully.", "success")
    return redirect(url_for("menu"))


@app.route("/menu/toggle/<int:item_id>", methods=["POST"])
@login_required
def toggle_menu_item(item_id):
    with get_db() as db:
        db.execute("""
            UPDATE menu_items
            SET available = CASE WHEN available = 1 THEN 0 ELSE 1 END
            WHERE id = ?
        """, (item_id,))
    return redirect(url_for("menu"))


@app.route("/api/orders", methods=["POST"])
@login_required
def create_order():
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])

    if not isinstance(items, list) or not items:
        return jsonify(error="Add at least one item."), 400

    method = data.get("payment_method", "")
    status = data.get("payment_status", "")

    if method not in {"CASH", "MPESA"}:
        return jsonify(error="Choose a valid payment method."), 400

    if status not in {"PAID", "UNPAID"}:
        return jsonify(error="Choose a valid payment status."), 400

    quantities = {}

    try:
        for item in items:
            item_id = int(item["id"])
            quantity = int(item["quantity"])

            if item_id < 1 or quantity < 1 or quantity > 100:
                raise ValueError

            quantities[item_id] = quantities.get(item_id, 0) + quantity

            if quantities[item_id] > 100:
                raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Invalid item or quantity."), 400

    with get_db() as db:
        order_lines = []
        total = 0.0

        for item_id, quantity in quantities.items():
            item = db.execute(
                """SELECT * FROM menu_items
                   WHERE id = ? AND available = 1""",
                (item_id,)
            ).fetchone()

            if item is None:
                return jsonify(
                    error="An item is unavailable. Refresh the menu."
                ), 400

            subtotal = round(item["price"] * quantity, 2)
            total += subtotal
            order_lines.append({
                "id": item["id"],
                "name": item["name"],
                "quantity": quantity,
                "price": item["price"],
                "subtotal": subtotal
            })

        total = round(total, 2)
        now = datetime.now().isoformat(timespec="seconds")

        cursor = db.execute(
            """INSERT INTO orders
               (order_number, total, payment_method,
                payment_status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("PENDING", total, method, status, now)
        )

        order_id = cursor.lastrowid
        order_number = f"ORD-{now[:10].replace('-', '')}-{order_id:04d}"

        db.execute(
            "UPDATE orders SET order_number = ? WHERE id = ?",
            (order_number, order_id)
        )

        for line in order_lines:
            db.execute(
                """INSERT INTO order_items
                   (order_id, menu_item_id, item_name,
                    quantity, unit_price, subtotal)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    order_id, line["id"], line["name"],
                    line["quantity"], line["price"], line["subtotal"]
                )
            )

    return jsonify(redirect=url_for("receipt", order_id=order_id)), 201


@app.route("/orders")
@login_required
def orders():
    with get_db() as db:
        all_orders = db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return render_template("orders.html", orders=all_orders)


@app.route("/orders/<int:order_id>/payment", methods=["POST"])
@login_required
def update_payment(order_id):
    status = request.form.get("payment_status", "")

    if status not in {"PAID", "UNPAID"}:
        flash("Invalid payment status.", "error")
        return redirect(url_for("orders"))

    with get_db() as db:
        order = db.execute(
            "SELECT payment_method FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if order is None:
            flash("Order not found.", "error")
        elif status == "PAID" and order["payment_method"] == "MPESA":
            flash(
                "Confirm the M-Pesa transaction on your phone first.",
                "error"
            )
        else:
            db.execute(
                "UPDATE orders SET payment_status = ? WHERE id = ?",
                (status, order_id)
            )
            flash("Payment status updated.", "success")

    return redirect(url_for("orders"))


@app.route("/receipt/<int:order_id>")
@login_required
def receipt(order_id):
    with get_db() as db:
        order = db.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        lines = db.execute(
            "SELECT * FROM order_items WHERE order_id = ?",
            (order_id,)
        ).fetchall()

    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("orders"))

    return render_template("receipt.html", order=order, lines=lines)


@app.errorhandler(413)
def file_too_large(error):
    flash("Image is too large. Maximum size is 5 MB.", "error")
    return redirect(url_for("menu"))


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
