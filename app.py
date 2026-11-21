from flask import Flask, render_template, request, redirect, session, url_for, flash
import sqlite3
from datetime import date
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import matplotlib

# Use non-GUI backend for Flask
matplotlib.use('Agg')

import seaborn as sns
import matplotlib.pyplot as plt
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")


# Database connection
def get_db():
    con = sqlite3.connect("database/expenses.db")
    con.row_factory = sqlite3.Row
    return con


# Require login for private pages
def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)
    return wrapper


# Create tables and safely upgrade the existing database
def create_table():
    con = get_db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            expense_date TEXT NOT NULL
        )
    """)

    # Add user_id to an existing expenses table without deleting its data.
    expense_columns = [
        row["name"] for row in con.execute("PRAGMA table_info(expenses)").fetchall()
    ]
    if "user_id" not in expense_columns:
        con.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER")

    # Rebuild budgets so each user can have a budget for the same category/month.
    budget_exists = con.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='budgets'
    """).fetchone()

    if budget_exists:
        budget_columns = [
            row["name"] for row in con.execute("PRAGMA table_info(budgets)").fetchall()
        ]
        has_user_id = "user_id" in budget_columns
        if not has_user_id:
            con.execute("ALTER TABLE budgets RENAME TO budgets_old")
            con.execute("""
                CREATE TABLE budgets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    category TEXT NOT NULL,
                    month TEXT NOT NULL,
                    budget_amount REAL NOT NULL,
                    UNIQUE(user_id, category, month)
                )
            """)
            con.execute("""
                INSERT INTO budgets(category, month, budget_amount)
                SELECT category, month, budget_amount FROM budgets_old
            """)
            con.execute("DROP TABLE budgets_old")
    else:
        con.execute("""
            CREATE TABLE budgets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                category TEXT NOT NULL,
                month TEXT NOT NULL,
                budget_amount REAL NOT NULL,
                UNIQUE(user_id, category, month)
            )
        """)

    con.commit()
    con.close()


# Register
@app.route('/register', methods=['GET', 'POST'])
def register():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not email or not password:
            flash("Please fill in all fields.", "danger")
            return render_template('register.html')

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "danger")
            return render_template('register.html')

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('register.html')

        con = get_db()
        existing_user = con.execute(
            "SELECT id FROM users WHERE email=?", (email,)
        ).fetchone()

        if existing_user:
            con.close()
            flash("An account with this email already exists.", "danger")
            return render_template('register.html')

        password_hash = generate_password_hash(password)
        cursor = con.execute(
            "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
            (name, email, password_hash)
        )
        new_user_id = cursor.lastrowid

        # Preserve any expenses/budgets created before authentication was added:
        # assign those old records to the first account registered.
        con.execute(
            "UPDATE expenses SET user_id=? WHERE user_id IS NULL",
            (new_user_id,)
        )
        con.execute(
            "UPDATE budgets SET user_id=? WHERE user_id IS NULL",
            (new_user_id,)
        )

        con.commit()
        con.close()

        session.clear()
        session["user_id"] = new_user_id
        session["user_name"] = name
        flash("Registration successful. Welcome!", "success")
        return redirect(url_for("home"))

    return render_template('register.html')


# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        con = get_db()
        user = con.execute(
            "SELECT * FROM users WHERE email=?", (email,)
        ).fetchone()
        con.close()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            return redirect(url_for("home"))

        flash("Invalid email or password.", "danger")

    return render_template('login.html')


# Logout
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# Home page / Dashboard
@app.route('/')
@login_required
def home():
    user_id = session["user_id"]
    con = get_db()

    today = date.today().isoformat()
    month = today[:7]
    year = today[:4]

    today_total = con.execute("""
        SELECT SUM(amount) FROM expenses
        WHERE user_id=? AND expense_date=?
    """, (user_id, today)).fetchone()[0] or 0

    month_total = con.execute("""
        SELECT SUM(amount) FROM expenses
        WHERE user_id=? AND strftime('%Y-%m', expense_date)=?
    """, (user_id, month)).fetchone()[0] or 0

    year_total = con.execute("""
        SELECT SUM(amount) FROM expenses
        WHERE user_id=? AND strftime('%Y', expense_date)=?
    """, (user_id, year)).fetchone()[0] or 0

    categories = con.execute("""
        SELECT category, SUM(amount) AS total
        FROM expenses
        WHERE user_id=? AND strftime('%Y-%m', expense_date)=?
        GROUP BY category
    """, (user_id, month)).fetchall()

    monthly = con.execute("""
        SELECT strftime('%Y-%m', expense_date) AS month, SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
          AND expense_date >= date(?, 'start of month', '-5 months')
        GROUP BY month
        ORDER BY month
    """, (user_id, today)).fetchall()

    expenses = con.execute("""
        SELECT * FROM expenses
        WHERE user_id=?
        ORDER BY expense_date DESC, id DESC
        LIMIT 10
    """, (user_id,)).fetchall()

    budgets = con.execute("""
        SELECT
            budgets.category,
            budgets.month,
            budgets.budget_amount,
            COALESCE(SUM(expenses.amount), 0) AS spent
        FROM budgets
        LEFT JOIN expenses
            ON budgets.category = expenses.category
            AND budgets.month = strftime('%Y-%m', expenses.expense_date)
            AND expenses.user_id = budgets.user_id
        WHERE budgets.user_id=? AND budgets.month=?
        GROUP BY budgets.category, budgets.month, budgets.budget_amount
    """, (user_id, month)).fetchall()

    con.close()

    df = pd.DataFrame(categories, columns=['category', 'total'])
    if not df.empty:
        plt.figure(figsize=(8, 5))
        sns.barplot(data=df, x='category', y='total')
        plt.title('Monthly Category-wise Expenses')
        plt.xlabel('Category')
        plt.ylabel('Amount (₹)')
        plt.tight_layout()
        plt.savefig('static/category.png')
        plt.close()

    monthly_df = pd.DataFrame(monthly, columns=['month', 'total'])
    if not monthly_df.empty:
        plt.figure(figsize=(9, 5))
        sns.lineplot(data=monthly_df, x='month', y='total', marker='o')
        plt.title('Monthly Expense Trend')
        plt.xlabel('Month')
        plt.ylabel('Total Expenses (₹)')
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig('static/monthly.png')
        plt.close()

    return render_template(
        'index.html',
        expenses=expenses,
        total=month_total,
        today_total=today_total,
        month_total=month_total,
        year_total=year_total,
        categories=categories,
        monthly=monthly,
        chart_exists=not df.empty,
        budgets=budgets,
        user_name=session.get("user_name")
    )


# Add expense
@app.route('/add', methods=['POST'])
@login_required
def add():
    item = request.form['item_name']
    amount = request.form['amount']
    category = request.form['category']
    expense_date = request.form['expense_date']

    con = get_db()
    con.execute("""
        INSERT INTO expenses(item_name, amount, category, expense_date, user_id)
        VALUES (?, ?, ?, ?, ?)
    """, (item, amount, category, expense_date, session["user_id"]))
    con.commit()
    con.close()
    return redirect(url_for("home"))


# Edit expense
@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    con = get_db()

    if request.method == 'POST':
        item = request.form['item_name']
        amount = request.form['amount']
        category = request.form['category']
        expense_date = request.form['expense_date']

        con.execute("""
            UPDATE expenses
            SET item_name=?, amount=?, category=?, expense_date=?
            WHERE id=? AND user_id=?
        """, (item, amount, category, expense_date, id, session["user_id"]))
        con.commit()
        con.close()
        return redirect(url_for("home"))

    expense = con.execute(
        "SELECT * FROM expenses WHERE id=? AND user_id=?",
        (id, session["user_id"])
    ).fetchone()
    con.close()

    if expense is None:
        return redirect(url_for("home"))

    return render_template('edit.html', expense=expense)


# Delete expense
@app.route('/delete/<int:id>')
@login_required
def delete(id):
    con = get_db()
    con.execute(
        "DELETE FROM expenses WHERE id=? AND user_id=?",
        (id, session["user_id"])
    )
    con.commit()
    con.close()
    return redirect(url_for("home"))


# Monthly budget
@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    if request.method == 'POST':
        category = request.form['category']
        month = request.form['month']
        budget_amount = request.form['budget_amount']

        con = get_db()
        con.execute("""
            INSERT INTO budgets(user_id, category, month, budget_amount)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, category, month)
            DO UPDATE SET budget_amount=excluded.budget_amount
        """, (session["user_id"], category, month, budget_amount))
        con.commit()
        con.close()
        return redirect(url_for("home"))

    return render_template('budget.html')


# Filter expenses
@app.route('/filter', methods=['GET'])
@login_required
def filter_expenses():
    start = request.args.get('start')
    end = request.args.get('end')

    con = get_db()
    expenses = con.execute("""
        SELECT * FROM expenses
        WHERE user_id=? AND expense_date BETWEEN ? AND ?
        ORDER BY expense_date DESC
    """, (session["user_id"], start, end)).fetchall()

    total = con.execute("""
        SELECT SUM(amount) FROM expenses
        WHERE user_id=? AND expense_date BETWEEN ? AND ?
    """, (session["user_id"], start, end)).fetchone()[0]

    con.close()
    return render_template(
        'filter.html',
        expenses=expenses,
        total=total or 0,
        start=start,
        end=end
    )


# Summary / Reports
@app.route('/summary')
@login_required
def summary():
    con = get_db()
    monthly = con.execute("""
        SELECT strftime('%Y-%m', expense_date) AS month, SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        GROUP BY month
        ORDER BY month DESC
    """, (session["user_id"],)).fetchall()

    categories = con.execute("""
        SELECT category, SUM(amount) AS total
        FROM expenses
        WHERE user_id=?
        GROUP BY category
    """, (session["user_id"],)).fetchall()

    con.close()
    return render_template(
        'summary.html',
        monthly=monthly,
        categories=categories
    )


# Run application
if __name__ == '__main__':
    os.makedirs("database", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    create_table()
    app.run(debug=True)
