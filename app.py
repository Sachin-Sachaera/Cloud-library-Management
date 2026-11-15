"""
Cloud-Based Library Management System
A digital, centralized, and efficient approach to modern library operations.

Run with:  python app.py
Then open: http://127.0.0.1:5000
Default login: admin / admin123
"""

import os
import sqlite3
import csv
from io import StringIO
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify, Response
)

DATABASE = os.getenv("LIBRARY_DB", "library.db")
LOAN_PERIOD_DAYS = 14
RENEWAL_DAYS = 7
MAX_RENEWALS = 1
FINE_PER_DAY = 10  # rupees per day overdue

app = Flask(__name__)
app.secret_key = "cloud-library-secret-key-change-in-production"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_database_path():
    return os.getenv("LIBRARY_DB", DATABASE)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(get_database_path())
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        with open("schema.sql") as f:
            db.executescript(f.read())
        migrate_db(db)
        # seed default admin user
        cur = db.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            db.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ("admin", "admin123", "admin"),
            )
        # seed a few sample books/members if empty, so the demo isn't blank
        cur = db.execute("SELECT COUNT(*) AS c FROM books")
        if cur.fetchone()["c"] == 0:
            sample_books = [
                ("Clean Code", "Robert C. Martin", "9780132350884", "Technology", 3, 3),
                ("The Pragmatic Programmer", "Andrew Hunt", "9780201616224", "Technology", 2, 2),
                ("Sapiens", "Yuval Noah Harari", "9780062316097", "History", 4, 4),
                ("Atomic Habits", "James Clear", "9780735211292", "Self-Help", 5, 5),
                ("Introduction to Algorithms", "Thomas H. Cormen", "9780262033848", "Technology", 2, 2),
            ]
            db.executemany(
                "INSERT INTO books (title, author, isbn, category, total_copies, available_copies) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                sample_books,
            )
        cur = db.execute("SELECT COUNT(*) AS c FROM members")
        if cur.fetchone()["c"] == 0:
            sample_members = [
                ("MEM001", "Aakash S", "aakash@example.com", "9876543210"),
                ("MEM002", "Priya R", "priya@example.com", "9876500000"),
            ]
            db.executemany(
                "INSERT INTO members (membership_id, name, email, phone) VALUES (?, ?, ?, ?)",
                sample_members,
            )
        seed_member_accounts(db)
        db.commit()


def migrate_db(db):
    user_columns = [row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()]
    if "member_id" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN member_id INTEGER")
    transaction_columns = [row["name"] for row in db.execute("PRAGMA table_info(transactions)").fetchall()]
    if "renewals" not in transaction_columns:
        db.execute("ALTER TABLE transactions ADD COLUMN renewals INTEGER NOT NULL DEFAULT 0")
    db.execute(
        """CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            request_date TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending',
            FOREIGN KEY (book_id) REFERENCES books (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS borrow_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            request_date TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending',
            due_date TEXT,
            approved_date TEXT,
            notes TEXT,
            FOREIGN KEY (book_id) REFERENCES books (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )"""
    )


def seed_member_accounts(db):
    sample_accounts = [
        ("MEM001", "mem001", "member123"),
        ("MEM002", "mem002", "member123"),
    ]
    for membership_id, username, password in sample_accounts:
        member = db.execute(
            "SELECT id FROM members WHERE membership_id = ?", (membership_id,)
        ).fetchone()
        if not member:
            continue
        exists = db.execute(
            "SELECT id FROM users WHERE username = ? OR member_id = ?",
            (username, member["id"]),
        ).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO users (username, password, role, member_id) VALUES (?, ?, 'member', ?)",
                (username, password, member["id"]),
            )


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access is required for that action.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


def current_member_id():
    return session.get("member_id")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND password = ?",
            (username, password),
        ).fetchone()
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["member_id"] = user["member_id"]
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        if not all([name, email, username, password]):
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for("signup"))

        db = get_db()
        try:
            member_count = db.execute("SELECT COUNT(*) AS c FROM members").fetchone()["c"]
            membership_id = f"MEM{member_count + 1:03d}"
            cur = db.execute(
                "INSERT INTO members (membership_id, name, email, phone) VALUES (?, ?, ?, ?)",
                (membership_id, name, email, phone),
            )
            db.execute(
                "INSERT INTO users (username, password, role, member_id) VALUES (?, ?, 'member', ?)",
                (username, password, cur.lastrowid),
            )
            db.commit()
            flash("Account created successfully. You can sign in now.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("That membership ID, email, or username already exists.", "danger")

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Utility: overdue check
# ---------------------------------------------------------------------------

def refresh_overdue_status(db):
    """Mark any issued books past their due date as overdue and compute fines."""
    today = datetime.now().date()
    rows = db.execute(
        "SELECT * FROM transactions WHERE status = 'issued'"
    ).fetchall()
    for row in rows:
        due = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
        if today > due:
            days_late = (today - due).days
            fine = days_late * FINE_PER_DAY
            db.execute(
                "UPDATE transactions SET status = 'overdue', fine = ? WHERE id = ?",
                (fine, row["id"]),
            )
    db.commit()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    refresh_overdue_status(db)

    if session.get("role") == "member":
        member_id = current_member_id()
        active_loans = db.execute(
            """SELECT COUNT(*) AS n FROM transactions
               WHERE member_id = ? AND status IN ('issued','overdue')""",
            (member_id,),
        ).fetchone()["n"]
        overdue_loans = db.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE member_id = ? AND status = 'overdue'",
            (member_id,),
        ).fetchone()["n"]
        available_titles = db.execute(
            "SELECT COUNT(*) AS n FROM books WHERE available_copies > 0"
        ).fetchone()["n"]
        total_fines = db.execute(
            "SELECT COALESCE(SUM(fine),0) AS n FROM transactions WHERE member_id = ?",
            (member_id,),
        ).fetchone()["n"]
        pending_reservations = db.execute(
            "SELECT COUNT(*) AS n FROM reservations WHERE member_id = ? AND status = 'pending'",
            (member_id,),
        ).fetchone()["n"]
        my_transactions = db.execute(
            """SELECT t.*, b.title AS book_title, b.author AS book_author
               FROM transactions t
               JOIN books b ON t.book_id = b.id
               WHERE t.member_id = ?
               ORDER BY t.id DESC LIMIT 8""",
            (member_id,),
        ).fetchall()
        my_requests_history = db.execute(
            """SELECT br.*, b.title AS book_title
               FROM borrow_requests br
               JOIN books b ON br.book_id = b.id
               WHERE br.member_id = ?
               ORDER BY br.id DESC LIMIT 6""",
            (member_id,),
        ).fetchall()
        return render_template(
            "member_dashboard.html",
            active_loans=active_loans,
            overdue_loans=overdue_loans,
            available_titles=available_titles,
            total_fines=total_fines,
            pending_reservations=pending_reservations,
            my_transactions=my_transactions,
            my_requests_history=my_requests_history,
        )

    total_books = db.execute("SELECT COALESCE(SUM(total_copies),0) AS n FROM books").fetchone()["n"]
    total_titles = db.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"]
    total_members = db.execute("SELECT COUNT(*) AS n FROM members").fetchone()["n"]
    issued_count = db.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE status IN ('issued','overdue')"
    ).fetchone()["n"]
    overdue_count = db.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE status = 'overdue'"
    ).fetchone()["n"]
    available_copies = db.execute("SELECT COALESCE(SUM(available_copies),0) AS n FROM books").fetchone()["n"]
    pending_reservations = db.execute(
        "SELECT COUNT(*) AS n FROM reservations WHERE status = 'pending'"
    ).fetchone()["n"]

    recent_transactions = db.execute(
        """SELECT t.*, b.title AS book_title, m.name AS member_name
           FROM transactions t
           JOIN books b ON t.book_id = b.id
           JOIN members m ON t.member_id = m.id
           ORDER BY t.id DESC LIMIT 8"""
    ).fetchall()

    return render_template(
        "dashboard.html",
        total_books=total_books,
        total_titles=total_titles,
        total_members=total_members,
        issued_count=issued_count,
        overdue_count=overdue_count,
        available_copies=available_copies,
        pending_reservations=pending_reservations,
        recent_transactions=recent_transactions,
    )


# ---------------------------------------------------------------------------
# Book Catalog Management
# ---------------------------------------------------------------------------

@app.route("/books")
@login_required
def books():
    db = get_db()
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    sql = "SELECT * FROM books WHERE 1=1"
    params = []
    if query:
        sql += " AND (title LIKE ? OR author LIKE ? OR isbn LIKE ?)"
        like = f"%{query}%"
        params += [like, like, like]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY title"

    all_books = db.execute(sql, params).fetchall()
    categories = db.execute(
        "SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND category != '' ORDER BY category"
    ).fetchall()

    return render_template(
        "books.html", books=all_books, categories=categories, query=query, selected_category=category
    )


@app.route("/books/<int:book_id>")
@login_required
def book_detail(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for("books"))

    active_request = None
    if session.get("role") == "member":
        active_request = db.execute(
            "SELECT * FROM borrow_requests WHERE book_id = ? AND member_id = ? AND status = 'pending'",
            (book_id, current_member_id()),
        ).fetchone()

    return render_template("book_detail.html", book=book, active_request=active_request)


@app.route("/books/add", methods=["GET", "POST"])
@admin_required
def add_book():
    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip() or None
        category = request.form.get("category", "").strip()
        copies = int(request.form.get("copies", 1))

        db = get_db()
        try:
            db.execute(
                "INSERT INTO books (title, author, isbn, category, total_copies, available_copies) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, author, isbn, category, copies, copies),
            )
            db.commit()
            flash(f'Book "{title}" added to the catalog.', "success")
            return redirect(url_for("books"))
        except sqlite3.IntegrityError:
            flash("A book with that ISBN already exists.", "danger")
    return render_template("book_form.html", book=None)


@app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_book(book_id):
    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for("books"))

    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip() or None
        category = request.form.get("category", "").strip()
        new_total = int(request.form.get("copies", 1))

        # adjust available copies by the same delta as total copies
        delta = new_total - book["total_copies"]
        new_available = max(0, book["available_copies"] + delta)

        try:
            db.execute(
                """UPDATE books SET title=?, author=?, isbn=?, category=?,
                   total_copies=?, available_copies=? WHERE id=?""",
                (title, author, isbn, category, new_total, new_available, book_id),
            )
            db.commit()
            flash(f'Book "{title}" updated.', "success")
            return redirect(url_for("books"))
        except sqlite3.IntegrityError:
            flash("A book with that ISBN already exists.", "danger")

    return render_template("book_form.html", book=book)


@app.route("/books/<int:book_id>/delete", methods=["POST"])
@admin_required
def delete_book(book_id):
    db = get_db()
    active = db.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE book_id=? AND status IN ('issued','overdue')",
        (book_id,),
    ).fetchone()["n"]
    if active > 0:
        flash("Cannot delete a book that currently has copies issued.", "danger")
    else:
        db.execute("DELETE FROM books WHERE id = ?", (book_id,))
        db.commit()
        flash("Book removed from catalog.", "info")
    return redirect(url_for("books"))


@app.route("/books/<int:book_id>/request", methods=["POST"])
@login_required
def request_borrow(book_id):
    if session.get("role") != "member":
        flash("Only member accounts can request books.", "warning")
        return redirect(url_for("books"))

    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for("books"))

    member_id = current_member_id()
    existing = db.execute(
        "SELECT id FROM borrow_requests WHERE book_id = ? AND member_id = ? AND status IN ('pending', 'approved')",
        (book_id, member_id),
    ).fetchone()
    active_loan = db.execute(
        "SELECT id FROM transactions WHERE book_id = ? AND member_id = ? AND status IN ('issued', 'overdue')",
        (book_id, member_id),
    ).fetchone()
    if existing:
        flash("You already have an active borrow request for this book.", "warning")
    elif active_loan:
        flash("You already have this book issued.", "warning")
    else:
        db.execute(
            "INSERT INTO borrow_requests (book_id, member_id, request_date, status) VALUES (?, ?, ?, 'pending')",
            (book_id, member_id, datetime.now().strftime("%Y-%m-%d")),
        )
        db.commit()
        flash(f'Borrow request sent for "{book["title"]}". Please wait for admin approval.', "success")
    return redirect(url_for("book_detail", book_id=book_id))


@app.route("/books/<int:book_id>/reserve", methods=["POST"])
@login_required
def reserve_book(book_id):
    if session.get("role") != "member":
        flash("Only member accounts can reserve books.", "warning")
        return redirect(url_for("books"))

    db = get_db()
    book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        flash("Book not found.", "danger")
        return redirect(url_for("books"))

    member_id = current_member_id()
    active_loan = db.execute(
        """SELECT id FROM transactions
           WHERE book_id = ? AND member_id = ? AND status IN ('issued','overdue')""",
        (book_id, member_id),
    ).fetchone()
    existing = db.execute(
        "SELECT id FROM reservations WHERE book_id = ? AND member_id = ? AND status = 'pending'",
        (book_id, member_id),
    ).fetchone()
    if active_loan:
        flash("You already have this book issued.", "warning")
    elif existing:
        flash("You already have a pending reservation for this book.", "warning")
    else:
        db.execute(
            "INSERT INTO reservations (book_id, member_id, request_date, status) VALUES (?, ?, ?, 'pending')",
            (book_id, member_id, datetime.now().strftime("%Y-%m-%d")),
        )
        db.commit()
        flash(f'"{book["title"]}" reserved successfully.', "success")
    return redirect(url_for("books"))


# ---------------------------------------------------------------------------
# Member Registration
# ---------------------------------------------------------------------------

@app.route("/members")
@admin_required
def members():
    db = get_db()
    query = request.args.get("q", "").strip()
    sql = "SELECT * FROM members WHERE 1=1"
    params = []
    if query:
        sql += " AND (name LIKE ? OR email LIKE ? OR membership_id LIKE ?)"
        like = f"%{query}%"
        params += [like, like, like]
    sql += " ORDER BY name"
    all_members = db.execute(sql, params).fetchall()
    return render_template("members.html", members=all_members, query=query)


@app.route("/members/add", methods=["GET", "POST"])
@admin_required
def add_member():
    if request.method == "POST":
        membership_id = request.form["membership_id"].strip()
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        account_username = request.form.get("account_username", "").strip() or membership_id.lower()
        account_password = request.form.get("account_password", "").strip() or "member123"

        db = get_db()
        try:
            cur = db.execute(
                "INSERT INTO members (membership_id, name, email, phone) VALUES (?, ?, ?, ?)",
                (membership_id, name, email, phone),
            )
            db.execute(
                "INSERT INTO users (username, password, role, member_id) VALUES (?, ?, 'member', ?)",
                (account_username, account_password, cur.lastrowid),
            )
            db.commit()
            flash(f'Member "{name}" registered. Login: {account_username} / {account_password}', "success")
            return redirect(url_for("members"))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("A member or login account with those details already exists.", "danger")
    return render_template("member_form.html", member=None)


@app.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_member(member_id):
    db = get_db()
    member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    account = db.execute("SELECT * FROM users WHERE member_id = ?", (member_id,)).fetchone()
    if not member:
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    if request.method == "POST":
        membership_id = request.form["membership_id"].strip()
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        account_username = request.form.get("account_username", "").strip()
        account_password = request.form.get("account_password", "").strip()
        try:
            db.execute(
                "UPDATE members SET membership_id=?, name=?, email=?, phone=? WHERE id=?",
                (membership_id, name, email, phone, member_id),
            )
            if account:
                db.execute(
                    "UPDATE users SET username=? WHERE member_id=?",
                    (account_username or account["username"], member_id),
                )
                if account_password:
                    db.execute(
                        "UPDATE users SET password=? WHERE member_id=?",
                        (account_password, member_id),
                    )
            elif account_username:
                db.execute(
                    "INSERT INTO users (username, password, role, member_id) VALUES (?, ?, 'member', ?)",
                    (account_username, account_password or "member123", member_id),
                )
            db.commit()
            flash(f'Member "{name}" updated.', "success")
            return redirect(url_for("members"))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("A member or login account with those details already exists.", "danger")

    return render_template("member_form.html", member=member, account=account)


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@admin_required
def delete_member(member_id):
    db = get_db()
    active = db.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE member_id=? AND status IN ('issued','overdue')",
        (member_id,),
    ).fetchone()["n"]
    if active > 0:
        flash("Cannot delete a member with books currently issued.", "danger")
    else:
        db.execute("DELETE FROM users WHERE member_id = ?", (member_id,))
        db.execute("DELETE FROM members WHERE id = ?", (member_id,))
        db.commit()
        flash("Member removed.", "info")
    return redirect(url_for("members"))


# ---------------------------------------------------------------------------
# Borrow Requests & Approval Workflow
# ---------------------------------------------------------------------------

@app.route("/borrow-requests")
@admin_required
def borrow_requests():
    db = get_db()
    status_filter = request.args.get("status", "pending").strip()
    sql = """SELECT br.*, b.title AS book_title, b.author AS book_author,
                    m.name AS member_name, m.membership_id AS membership_id
              FROM borrow_requests br
              JOIN books b ON br.book_id = b.id
              JOIN members m ON br.member_id = m.id
              WHERE 1=1"""
    params = []
    if status_filter:
        sql += " AND br.status = ?"
        params.append(status_filter)
    sql += " ORDER BY br.id DESC"
    rows = db.execute(sql, params).fetchall()
    return render_template("borrow_requests.html", requests=rows, status_filter=status_filter)


@app.route("/borrow-requests/<int:request_id>/approve", methods=["POST"])
@admin_required
def approve_borrow_request(request_id):
    db = get_db()
    req = db.execute("SELECT * FROM borrow_requests WHERE id = ?", (request_id,)).fetchone()
    if not req or req["status"] != "pending":
        flash("Borrow request not found or already processed.", "danger")
        return redirect(url_for("borrow_requests"))

    book = db.execute("SELECT * FROM books WHERE id = ?", (req["book_id"],)).fetchone()
    if not book or book["available_copies"] < 1:
        flash("This book is currently unavailable for issue.", "warning")
        return redirect(url_for("borrow_requests"))

    due_date = request.form.get("due_date", "").strip() or (
        datetime.now() + timedelta(days=LOAN_PERIOD_DAYS)
    ).strftime("%Y-%m-%d")
    issue_date = datetime.now().strftime("%Y-%m-%d")

    db.execute(
        "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) VALUES (?, ?, ?, ?, 'issued')",
        (req["book_id"], req["member_id"], issue_date, due_date),
    )
    db.execute("UPDATE books SET available_copies = available_copies - 1 WHERE id = ?", (req["book_id"],))
    db.execute(
        "UPDATE borrow_requests SET status = 'approved', due_date = ?, approved_date = ? WHERE id = ?",
        (due_date, issue_date, request_id),
    )
    db.commit()
    flash(f'Borrow request approved for "{book["title"]}". Due date: {due_date}.', "success")
    return redirect(url_for("borrow_requests"))


@app.route("/borrow-requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_borrow_request(request_id):
    db = get_db()
    db.execute(
        "UPDATE borrow_requests SET status = 'rejected', notes = ? WHERE id = ?",
        (request.form.get("notes", "Rejected by admin"), request_id),
    )
    db.commit()
    flash("Borrow request rejected.", "info")
    return redirect(url_for("borrow_requests"))


@app.route("/borrow-requests/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_borrow_request(request_id):
    db = get_db()
    req = db.execute("SELECT * FROM borrow_requests WHERE id = ?", (request_id,)).fetchone()
    if not req or req["status"] != "pending":
        flash("Request not found or already processed.", "danger")
        return redirect(url_for("my_requests"))
    if session.get("role") != "admin" and req["member_id"] != current_member_id():
        flash("You can only cancel your own requests.", "danger")
        return redirect(url_for("my_requests"))

    db.execute("UPDATE borrow_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
    db.commit()
    flash("Borrow request cancelled.", "info")
    if session.get("role") == "admin":
        return redirect(url_for("borrow_requests"))
    return redirect(url_for("my_requests"))


@app.route("/my-requests")
@login_required
def my_requests():
    if session.get("role") != "member":
        return redirect(url_for("borrow_requests"))

    db = get_db()
    rows = db.execute(
        """SELECT br.*, b.title AS book_title, b.author AS book_author
           FROM borrow_requests br
           JOIN books b ON br.book_id = b.id
           WHERE br.member_id = ?
           ORDER BY br.id DESC""",
        (current_member_id(),),
    ).fetchall()
    return render_template("my_requests.html", requests=rows)


# ---------------------------------------------------------------------------
# Issue & Return Tracking
# ---------------------------------------------------------------------------

@app.route("/transactions")
@admin_required
def transactions():
    db = get_db()
    refresh_overdue_status(db)
    status_filter = request.args.get("status", "").strip()

    sql = """SELECT t.*, b.title AS book_title, b.author AS book_author,
                     m.name AS member_name, m.membership_id AS membership_id
              FROM transactions t
              JOIN books b ON t.book_id = b.id
              JOIN members m ON t.member_id = m.id
              WHERE 1=1"""
    params = []
    if status_filter:
        sql += " AND t.status = ?"
        params.append(status_filter)
    sql += " ORDER BY t.id DESC"

    all_transactions = db.execute(sql, params).fetchall()
    return render_template("transactions.html", transactions=all_transactions, status_filter=status_filter)


@app.route("/transactions/issue", methods=["GET", "POST"])
@admin_required
def issue_book():
    db = get_db()
    if request.method == "POST":
        book_id = int(request.form["book_id"])
        member_id = int(request.form["member_id"])

        book = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book or book["available_copies"] < 1:
            flash("Selected book is not available for issue.", "danger")
            return redirect(url_for("issue_book"))

        due_date = request.form.get("due_date", "").strip() or (
            datetime.now() + timedelta(days=LOAN_PERIOD_DAYS)
        ).strftime("%Y-%m-%d")
        db.execute(
            "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) "
            "VALUES (?, ?, ?, ?, 'issued')",
            (book_id, member_id, datetime.now().strftime("%Y-%m-%d"), due_date),
        )
        db.execute(
            "UPDATE books SET available_copies = available_copies - 1 WHERE id = ?",
            (book_id,),
        )
        db.commit()
        flash(f'"{book["title"]}" issued successfully. Due back {due_date}.', "success")
        return redirect(url_for("transactions"))

    available_books = db.execute(
        "SELECT * FROM books WHERE available_copies > 0 ORDER BY title"
    ).fetchall()
    all_members = db.execute("SELECT * FROM members ORDER BY name").fetchall()
    default_due_date = (datetime.now() + timedelta(days=LOAN_PERIOD_DAYS)).strftime("%Y-%m-%d")
    return render_template(
        "issue_form.html",
        books=available_books,
        members=all_members,
        default_due_date=default_due_date,
        today=datetime.now(),
    )


@app.route("/transactions/<int:txn_id>/return", methods=["POST"])
@admin_required
def return_book(txn_id):
    db = get_db()
    txn = db.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not txn or txn["status"] == "returned":
        flash("Transaction not found or already returned.", "danger")
        return redirect(url_for("transactions"))

    today = datetime.now().date()
    due = datetime.strptime(txn["due_date"], "%Y-%m-%d").date()
    days_late = max(0, (today - due).days)
    fine = days_late * FINE_PER_DAY

    db.execute(
        "UPDATE transactions SET status='returned', return_date=?, fine=? WHERE id=?",
        (today.strftime("%Y-%m-%d"), fine, txn_id),
    )
    db.execute(
        "UPDATE books SET available_copies = available_copies + 1 WHERE id = ?",
        (txn["book_id"],),
    )
    db.commit()

    if fine > 0:
        flash(f"Book returned. Overdue by {days_late} day(s) — fine: {fine}.", "warning")
    else:
        flash("Book returned on time. Thank you!", "success")
    return redirect(url_for("transactions"))


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------

@app.route("/reservations")
@admin_required
def reservations():
    db = get_db()
    status_filter = request.args.get("status", "pending").strip()
    sql = """SELECT r.*, b.title AS book_title, b.author AS book_author,
                    b.available_copies AS available_copies,
                    m.name AS member_name, m.membership_id AS membership_id
             FROM reservations r
             JOIN books b ON r.book_id = b.id
             JOIN members m ON r.member_id = m.id
             WHERE 1=1"""
    params = []
    if status_filter:
        sql += " AND r.status = ?"
        params.append(status_filter)
    sql += " ORDER BY r.id DESC"
    rows = db.execute(sql, params).fetchall()
    return render_template("reservations.html", reservations=rows, status_filter=status_filter)


@app.route("/reservations/<int:reservation_id>/fulfill", methods=["POST"])
@admin_required
def fulfill_reservation(reservation_id):
    db = get_db()
    reservation = db.execute(
        """SELECT r.*, b.title AS book_title, b.available_copies AS available_copies
           FROM reservations r
           JOIN books b ON r.book_id = b.id
           WHERE r.id = ?""",
        (reservation_id,),
    ).fetchone()
    if not reservation or reservation["status"] != "pending":
        flash("Reservation not found or already closed.", "danger")
        return redirect(url_for("reservations"))
    if reservation["available_copies"] < 1:
        flash("No copies are available yet for this reservation.", "warning")
        return redirect(url_for("reservations"))

    due_date = (datetime.now() + timedelta(days=LOAN_PERIOD_DAYS)).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) VALUES (?, ?, ?, ?, 'issued')",
        (reservation["book_id"], reservation["member_id"], datetime.now().strftime("%Y-%m-%d"), due_date),
    )
    db.execute("UPDATE books SET available_copies = available_copies - 1 WHERE id = ?", (reservation["book_id"],))
    db.execute("UPDATE reservations SET status = 'fulfilled' WHERE id = ?", (reservation_id,))
    db.commit()
    flash(f'Reservation fulfilled and "{reservation["book_title"]}" issued.', "success")
    return redirect(url_for("reservations"))


@app.route("/reservations/<int:reservation_id>/cancel", methods=["POST"])
@login_required
def cancel_reservation(reservation_id):
    db = get_db()
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if not reservation or reservation["status"] != "pending":
        flash("Reservation not found or already closed.", "danger")
        return redirect(url_for("dashboard"))
    if session.get("role") != "admin" and reservation["member_id"] != current_member_id():
        flash("You can only cancel your own reservations.", "danger")
        return redirect(url_for("dashboard"))

    db.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?", (reservation_id,))
    db.commit()
    flash("Reservation cancelled.", "info")
    if session.get("role") == "admin":
        return redirect(url_for("reservations"))
    return redirect(url_for("my_reservations"))


# ---------------------------------------------------------------------------
# Member self-service
# ---------------------------------------------------------------------------

@app.route("/my-loans")
@login_required
def my_loans():
    if session.get("role") != "member":
        return redirect(url_for("transactions"))

    db = get_db()
    refresh_overdue_status(db)
    status_filter = request.args.get("status", "").strip()
    sql = """SELECT t.*, b.title AS book_title, b.author AS book_author,
                    b.isbn AS isbn, b.category AS category
             FROM transactions t
             JOIN books b ON t.book_id = b.id
             WHERE t.member_id = ?"""
    params = [current_member_id()]
    if status_filter:
        sql += " AND t.status = ?"
        params.append(status_filter)
    sql += " ORDER BY t.id DESC"
    loans = db.execute(sql, params).fetchall()
    return render_template("my_loans.html", loans=loans, status_filter=status_filter)


@app.route("/my-loans/<int:txn_id>/renew", methods=["POST"])
@login_required
def renew_loan(txn_id):
    if session.get("role") != "member":
        return redirect(url_for("transactions"))

    db = get_db()
    refresh_overdue_status(db)
    txn = db.execute(
        """SELECT t.*, b.title AS book_title
           FROM transactions t
           JOIN books b ON t.book_id = b.id
           WHERE t.id = ? AND t.member_id = ?""",
        (txn_id, current_member_id()),
    ).fetchone()
    if not txn or txn["status"] == "returned":
        flash("Loan not found or already returned.", "danger")
    elif txn["status"] == "overdue":
        flash("Overdue loans cannot be renewed. Please contact the librarian.", "warning")
    elif txn["renewals"] >= MAX_RENEWALS:
        flash("This loan has already used its renewal.", "warning")
    else:
        waiting = db.execute(
            """SELECT id FROM reservations
               WHERE book_id = ? AND member_id != ? AND status = 'pending'""",
            (txn["book_id"], current_member_id()),
        ).fetchone()
        if waiting:
            flash("This book has a pending reservation, so it cannot be renewed.", "warning")
        else:
            current_due = datetime.strptime(txn["due_date"], "%Y-%m-%d").date()
            new_due = current_due + timedelta(days=RENEWAL_DAYS)
            db.execute(
                "UPDATE transactions SET due_date = ?, renewals = renewals + 1 WHERE id = ?",
                (new_due.strftime("%Y-%m-%d"), txn_id),
            )
            db.commit()
            flash(f'"{txn["book_title"]}" renewed until {new_due}.', "success")
    return redirect(url_for("my_loans"))


@app.route("/my-reservations")
@login_required
def my_reservations():
    if session.get("role") != "member":
        return redirect(url_for("reservations"))

    db = get_db()
    rows = db.execute(
        """SELECT r.*, b.title AS book_title, b.author AS book_author,
                  b.available_copies AS available_copies
           FROM reservations r
           JOIN books b ON r.book_id = b.id
           WHERE r.member_id = ?
           ORDER BY r.id DESC""",
        (current_member_id(),),
    ).fetchall()
    return render_template("my_reservations.html", reservations=rows)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if session.get("role") != "member":
        flash("Admins manage profiles from the Members page.", "info")
        return redirect(url_for("members"))

    db = get_db()
    member = db.execute("SELECT * FROM members WHERE id = ?", (current_member_id(),)).fetchone()
    if not member:
        flash("Your member profile is not linked. Please contact the librarian.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

        try:
            db.execute("UPDATE members SET email=?, phone=? WHERE id=?", (email, phone, member["id"]))
            if new_password:
                if current_password != user["password"]:
                    db.rollback()
                    flash("Current password is incorrect.", "danger")
                    return redirect(url_for("profile"))
                db.execute("UPDATE users SET password=? WHERE id=?", (new_password, user["id"]))
            db.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("That email is already used by another member.", "danger")

    return render_template("member_profile.html", member=member)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route("/reports")
@admin_required
def reports():
    db = get_db()
    refresh_overdue_status(db)
    top_books = db.execute(
        """SELECT b.title, b.author, COUNT(t.id) AS issue_count
           FROM books b
           LEFT JOIN transactions t ON t.book_id = b.id
           GROUP BY b.id
           ORDER BY issue_count DESC, b.title
           LIMIT 8"""
    ).fetchall()
    category_summary = db.execute(
        """SELECT COALESCE(category, 'Uncategorized') AS category,
                  COUNT(*) AS titles,
                  COALESCE(SUM(total_copies),0) AS total_copies,
                  COALESCE(SUM(available_copies),0) AS available_copies
           FROM books
           GROUP BY COALESCE(category, 'Uncategorized')
           ORDER BY category"""
    ).fetchall()
    overdue_members = db.execute(
        """SELECT m.name, m.membership_id, COUNT(t.id) AS overdue_count,
                  COALESCE(SUM(t.fine),0) AS fine_total
           FROM members m
           JOIN transactions t ON t.member_id = m.id
           WHERE t.status = 'overdue'
           GROUP BY m.id
           ORDER BY overdue_count DESC, fine_total DESC"""
    ).fetchall()
    monthly_issues = db.execute(
        """SELECT substr(issue_date, 1, 7) AS month, COUNT(*) AS issue_count
           FROM transactions
           GROUP BY substr(issue_date, 1, 7)
           ORDER BY month DESC
           LIMIT 6"""
    ).fetchall()
    totals = {
        "fines": db.execute("SELECT COALESCE(SUM(fine),0) AS n FROM transactions").fetchone()["n"],
        "reservations": db.execute("SELECT COUNT(*) AS n FROM reservations WHERE status='pending'").fetchone()["n"],
        "returned": db.execute("SELECT COUNT(*) AS n FROM transactions WHERE status='returned'").fetchone()["n"],
    }
    return render_template(
        "reports.html",
        top_books=top_books,
        category_summary=category_summary,
        overdue_members=overdue_members,
        monthly_issues=monthly_issues,
        totals=totals,
    )


@app.route("/reports/export/transactions")
@admin_required
def export_transactions():
    db = get_db()
    rows = db.execute(
        """SELECT t.id, b.title AS book_title, m.name AS member_name, m.membership_id,
                  t.issue_date, t.due_date, t.return_date, t.status, t.fine, t.renewals
           FROM transactions t
           JOIN books b ON t.book_id = b.id
           JOIN members m ON t.member_id = m.id
           ORDER BY t.id DESC"""
    ).fetchall()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Book", "Member", "Membership ID", "Issue Date", "Due Date", "Return Date", "Status", "Fine", "Renewals"])
    for row in rows:
        writer.writerow([row[k] for k in row.keys()])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions_report.csv"},
    )


# ---------------------------------------------------------------------------
# Simple JSON API (demonstrates "cloud" accessibility from other clients)
# ---------------------------------------------------------------------------

@app.route("/api/books")
def api_books():
    db = get_db()
    rows = db.execute("SELECT * FROM books ORDER BY title").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/availability/<isbn>")
def api_availability(isbn):
    db = get_db()
    row = db.execute("SELECT * FROM books WHERE isbn = ?", (isbn,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    if not os.path.exists(DATABASE):
        init_db()
    else:
        # ensure tables exist even if db file exists but is empty
        init_db()
    app.run(debug=True)
