"""
ACCOUNTING PRO - Complete Computerized Accounting & Business Management System.

Run:  python app.py      ->  http://127.0.0.1:5000
"""
import json
import os
import secrets
import shutil
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for, current_app)
from sqlalchemy import func, or_

from config import Config, ensure_dirs, prepare_data_dir, IS_SERVERLESS
from extensions import db

import models
from models import (Account, AccountingPeriod, Adjustment, AuditLog, Budget,
                    Business, ClosingEntry, JournalEntry, JournalLine, Product,
                    Sale, Purchase, InventoryTransaction, User, UserSettings,
                    ACTIVE_ENTRY_STATUS, ADJUSTMENT_KINDS, ACCOUNTING_METHODS,
                    CATEGORIES, ENTRY_TYPES, NORMAL_BALANCE, PERIOD_KINDS,
                    SUBCATEGORIES, SUBCATEGORY_CHOICES, money, ZERO)

from accounting import journal as journal_engine
from accounting import adjustments as adjustment_engine
from accounting import closing as closing_engine
from accounting import analytics
from accounting import inventory as inventory_engine
from accounting.ledger import (account_balances, cash_balance, ledger_rows,
                               posted_journal_lines, subcategory_items,
                               sum_subcategories)
from accounting.reports import journal_rows
from accounting.trial_balance import (trial_balance, adjusted_trial_balance,
                                      post_closing_trial_balance)
from accounting.financial_statements import (trading_account, profit_and_loss,
                                             balance_sheet, cash_flow)
from accounting.journal import JournalError

from exports.reports_data import build_report, REPORT_TITLES
from exports.pdf_export import render_pdf, export_filename
from exports.word_export import render_word
from exports.excel_export import (render_excel, render_full_workbook, to_bytes,
                                  _business_sheet, _accounts_sheet, _opening_sheet,
                                  _journal_sheet, _ledger_sheet, _trial_balance_sheet,
                                  _adjusted_tb_sheet, _two_side_sheet, _pl_sheet,
                                  _bs_sheet, _closing_sheet, _post_closing_sheet,
                                  _annual_sheet, _product_sheets, _budget_sheet)

from imports.processor import (IMPORT_KINDS, auto_map, parse_upload, validate_rows,
                               apply_import)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    # Serverless platforms mount the code bundle read-only: make sure the
    # writable data directory holds a database before SQLAlchemy connects.
    prepare_data_dir()
    db.init_app(app)
    ensure_dirs()
    register_jinja(app)
    register_routes(app)
    with app.app_context():
        db.create_all()
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def current_user():
    if "user_id" not in session:
        return None
    return db.session.get(User, session["user_id"])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _cached_notifications(business):
    """Compute the bell counts once per request."""
    cached = g.get("_notifications")
    if cached is not None:
        return cached
    data = {"total": 0, "items": []}
    if business is not None:
        drafts = JournalEntry.query.filter_by(business_id=business.id,
                                              status="draft").count()
        adj = Adjustment.query.filter_by(business_id=business.id, status="draft").count()
        closing = ClosingEntry.query.filter_by(business_id=business.id,
                                               status="draft").count()
        items = []
        if drafts:
            items.append({"label": f"{drafts} voucher draft{'' if drafts == 1 else 's'} to post",
                          "url": url_for("transactions"), "icon": "file-text"})
        if adj:
            items.append({"label": f"{adj} adjusting entr{'y' if adj == 1 else 'ies'} to post",
                          "url": url_for("adjustments"), "icon": "sliders"})
        if closing:
            items.append({"label": f"{closing} closing entr{'y' if closing == 1 else 'ies'} to post",
                          "url": url_for("closing_entries_page"), "icon": "lock"})
        data = {"total": drafts + adj + closing, "items": items}
    g._notifications = data
    return data


def ensure_business(user):
    business = (Business.query.filter_by(user_id=user.id)
                .order_by(Business.id).first())
    if business:
        return business
    business = Business(
        user_id=user.id,
        name="",
        currency=app_currency(user),
        fy_start=date.fromisoformat(Config.DEFAULT_PERIOD_START),
        fy_end=date.fromisoformat(Config.DEFAULT_PERIOD_END),
        accounting_method="Accrual",
    )
    db.session.add(business)
    db.session.flush()
    db.session.add(AccountingPeriod(
        business_id=business.id,
        label="1 Jan 2026 - 31 Mar 2026",
        kind="Quarterly",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
        is_active=True,
    ))
    if not user.settings:
        db.session.add(UserSettings(user_id=user.id))
    db.session.commit()
    return business


def app_currency(user):
    if user and user.settings and user.settings.currency:
        return user.settings.currency
    return Config.DEFAULT_CURRENCY


def get_business():
    """
    The signed-in user's business.

    The cached value is keyed by user id so that a cached business can never be
    served for a different signed-in user (data isolation).
    """
    user = current_user()
    if not user:
        return None
    cached = g.get("business")
    if cached is not None and g.get("business_user_id") == user.id:
        return cached
    business = ensure_business(user)
    g.business = business
    g.business_user_id = user.id
    return business


def business_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not get_business():
            flash("Please complete your business profile first.", "warning")
            return redirect(url_for("business_profile"))
        return view(*args, **kwargs)
    return wrapped


def audit(action, record_type=None, record_id=None, previous=None, new=None):
    user = current_user()
    try:
        db.session.add(AuditLog(
            user_id=user.id if user else None,
            business_id=get_business().id if user and get_business() else None,
            action=action, record_type=record_type, record_id=record_id,
            previous_values=json.dumps(previous, default=str)[:4000] if previous else None,
            new_values=json.dumps(new, default=str)[:4000] if new else None,
            ip_address=request.remote_addr,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def period_range():
    """Working period: query args > session override > active period."""
    business = get_business()
    active = business.active_period() if business else None
    default_start = active.start_date if active else business.fy_start
    default_end = active.end_date if active else business.fy_end
    start = (_parse_date(request.args.get("start"))
             or _parse_date(session.get("pr_start")) or default_start)
    end = (_parse_date(request.args.get("end"))
           or _parse_date(session.get("pr_end")) or default_end)
    if start > end:
        start, end = end, start
    return start, end


def default_entry_date():
    """A sensible default voucher date: today if inside the period, else period end."""
    start, end = period_range()
    today = date.today()
    if start and end and start <= today <= end:
        return today
    return end or today


def period_label(start, end):
    if not start or not end:
        return "All periods"
    return f"{start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}"


def safe_amount(raw, field="Amount"):
    try:
        return journal_engine.parse_amount(raw, field)
    except JournalError as exc:
        raise ValueError(str(exc))


def save_upload(file, subfolder, allowed=None):
    if not file or not file.filename:
        return None
    allowed = allowed or {"png", "jpg", "jpeg", "webp", "gif", "pdf", "doc", "docx",
                          "xls", "xlsx", "csv"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        raise ValueError(f"File type .{ext} is not allowed.")
    name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(Config.UPLOAD_FOLDER, subfolder)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, name))
    return name


class Pagination:
    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total

    @property
    def pages(self):
        return max(1, (self.total + self.per_page - 1) // self.per_page)

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1

    @property
    def next_num(self):
        return self.page + 1

    @property
    def start_index(self):
        return (self.page - 1) * self.per_page + 1

    def iter_pages(self):
        return range(1, self.pages + 1)


# ---------------------------------------------------------------------------
# Jinja
# ---------------------------------------------------------------------------
def register_jinja(app):
    @app.template_filter("rs")
    def rs_filter(value, symbol="Rs."):
        if value is None or value == "":
            return f"{symbol} 0.00"
        try:
            return f"{symbol} {Decimal(str(value)):,.2f}"
        except (InvalidOperation, ValueError):
            return str(value)

    @app.template_filter("num")
    def num_filter(value):
        try:
            return f"{Decimal(str(value)):,.2f}"
        except (InvalidOperation, ValueError):
            return "0.00"

    @app.template_filter("dt")
    def dt_filter(value, fmt="%d %b %Y"):
        if not value:
            return "-"
        try:
            return value.strftime(fmt)
        except AttributeError:
            return str(value)

    @app.template_filter("neg")
    def neg_filter(value):
        try:
            return Decimal(str(value)) < 0
        except (InvalidOperation, ValueError):
            return False

    @app.context_processor
    def inject_globals():
        user = current_user()
        business = get_business() if user else None
        start, end = (period_range() if business else (None, None))
        return {
            "current_user": user,
            "business": business,
            "period_start": start,
            "period_end": end,
            "period_label": period_label(start, end),
            "csrf_token": lambda: session.get("_csrf", ""),
            "currency_symbol": "Rs.",
            "app_name": "ACCOUNTING PRO",
            "today": date.today(),
            "active_period": business.active_period() if business else None,
            "categories": CATEGORIES,
            "subcategory_choices": SUBCATEGORY_CHOICES,
            "entry_types": ENTRY_TYPES,
            "adjustment_kinds": ADJUSTMENT_KINDS,
            "period_kinds": PERIOD_KINDS,
            "accounting_methods": ACCOUNTING_METHODS,
            "report_titles": REPORT_TITLES,
            "notifications": _cached_notifications(business),
        }

    @app.template_filter("tojson_safe")
    def tojson_safe(value):
        return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def register_routes(app):

    # -- security ---------------------------------------------------------
    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if app.config.get("WTF_CSRF_ENABLED", True) is False or app.testing:
                return
            token = session.get("_csrf")
            sent = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or token != sent:
                abort(400, description="Invalid or missing CSRF token. Please reload the page.")

    @app.before_request
    def load_user():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        g.user = current_user()

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def handle_error(exc):
        code = getattr(exc, "code", 500)
        messages = {
            400: ("Bad request", getattr(exc, "description", "The request could not be processed.")),
            403: ("Access denied", "You do not have permission to view this resource."),
            404: ("Page not found", "The page you are looking for does not exist."),
            413: ("File too large", "The uploaded file exceeds the 8 MB limit."),
            500: ("Server error", "Something went wrong. Your data has not been changed."),
        }
        title, message = messages.get(code, ("Error", "An unexpected error occurred."))
        return render_template("error.html", code=code, title=title,
                               message=message), code

    # -- public home page -------------------------------------------------
    @app.route("/")
    def index():
        """Public home page — the first screen a visitor sees.

        Signing in still lands on the existing dashboard; protected routes keep
        their own authentication, so nothing about the application flow changes.
        """
        return render_template(
            "home.html",
            form={},
            contact_errors=None,
            contact_sent=session.pop("contact_sent", False),
        )

    @app.route("/contact", methods=["POST"])
    def contact():
        """Contact form on the home page.

        Validated server-side (the browser's own validation is a convenience,
        never the guarantee). Messages are appended to a JSON-lines log inside
        the application's upload folder - no database table is added or changed.
        """
        import re as _re

        form = request.form
        errors = []

        # honeypot: a real visitor never fills this hidden field
        if (form.get("website") or "").strip():
            return redirect(url_for("index") + "#contact")

        name = (form.get("name") or "").strip()
        email = (form.get("email") or "").strip()
        subject = (form.get("subject") or "").strip()
        message = (form.get("message") or "").strip()

        if len(name) < 2:
            errors.append("Please enter your full name.")
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", email):
            errors.append("Please enter a valid email address.")
        if len(subject) > 120:
            errors.append("Subject is too long (120 characters maximum).")
        if len(message) < 10:
            errors.append("Please write a message of at least 10 characters.")
        if len(message) > 2000:
            errors.append("Message is too long (2000 characters maximum).")

        if errors:
            return render_template("home.html", form=form,
                                   contact_errors=errors, contact_sent=False), 400

        record = {
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "name": name,
            "email": email,
            "subject": subject,
            "message": message,
        }
        try:
            folder = current_app.config["UPLOAD_FOLDER"]
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "contact_messages.jsonl"), "a",
                      encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            # never lose the visitor's message because of a disk problem
            flash("Your message could not be saved just now. Please try again.", "danger")
            return redirect(url_for("index") + "#contact")

        session["contact_sent"] = True
        return redirect(url_for("index") + "#contact")

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.route("/terms")
    def terms():
        return render_template("terms.html")

    # -- auth -------------------------------------------------------------

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""
            errors = []
            if len(name) < 2:
                errors.append("Please enter your full name.")
            if "@" not in email or len(email) < 6:
                errors.append("Please enter a valid email address.")
            if len(password) < 8:
                errors.append("Password must be at least 8 characters.")
            if password != confirm:
                errors.append("Passwords do not match.")
            if User.query.filter_by(email=email).first():
                errors.append("An account with this email already exists.")
            if errors:
                for e in errors:
                    flash(e, "danger")
                return render_template("register.html", form=request.form)
            user = User(name=name, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            ensure_business(user)
            session.clear()
            session["_csrf"] = secrets.token_urlsafe(32)
            session["user_id"] = user.id
            audit("User registered", "User", user.id, new={"email": email})
            flash("Welcome to ACCOUNTING PRO! Complete your business profile to begin.", "success")
            return redirect(url_for("business_profile"))
        return render_template("register.html", form={})

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Invalid email or password.", "danger")
                return render_template("login.html", form=request.form)
            if not user.is_active:
                flash("This account has been deactivated.", "danger")
                return render_template("login.html", form=request.form)
            session.clear()
            session["_csrf"] = secrets.token_urlsafe(32)
            session["user_id"] = user.id
            session.permanent = True
            user.last_login = datetime.utcnow()
            db.session.commit()
            ensure_business(user)
            audit("User login", "User", user.id)
            return redirect(url_for("dashboard"))
        return render_template("login.html", form={})

    @app.route("/logout", methods=["POST", "GET"])
    def logout():
        if current_user():
            audit("User logout", "User", session.get("user_id"))
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        token = None
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            user = User.query.filter_by(email=email).first()
            if user:
                user.reset_token = secrets.token_urlsafe(32)
                user.reset_expires = datetime.utcnow() + timedelta(hours=2)
                db.session.commit()
                token = user.reset_token
                flash("Reset link generated. In this offline build the link is shown "
                      "below - no email is sent.", "info")
            else:
                flash("If that email exists, a reset link has been generated.", "info")
        return render_template("forgot_password.html", token=token)

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        user = User.query.filter_by(reset_token=token).first()
        if not user or not user.reset_expires or user.reset_expires < datetime.utcnow():
            flash("This password reset link is invalid or has expired.", "danger")
            return redirect(url_for("forgot_password"))
        if request.method == "POST":
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "danger")
            elif password != confirm:
                flash("Passwords do not match.", "danger")
            else:
                user.set_password(password)
                user.reset_token = None
                user.reset_expires = None
                db.session.commit()
                audit("Password reset", "User", user.id)
                flash("Password updated. Please sign in.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html", token=token)

    # -- period ------------------------------------------------------------
    @app.route("/period", methods=["POST"])
    @business_required
    def set_period():
        start = _parse_date(request.form.get("start"))
        end = _parse_date(request.form.get("end"))
        if not start or not end:
            flash("Please provide a valid date range.", "danger")
        elif start > end:
            flash("Start date must be before end date.", "danger")
        else:
            session["pr_start"] = start.isoformat()
            session["pr_end"] = end.isoformat()
            flash(f"Working period set to {period_label(start, end)}.", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/period/reset", methods=["POST"])
    @business_required
    def reset_period():
        session.pop("pr_start", None)
        session.pop("pr_end", None)
        flash("Working period reset to the active accounting period.", "success")
        return redirect(request.referrer or url_for("dashboard"))

    # -- dashboard ---------------------------------------------------------
    @app.route("/dashboard")
    @business_required
    def dashboard():
        business = get_business()
        start, end = period_range()
        summary = analytics.dashboard_summary(business.id, start, end)
        series = analytics.sales_expense_by_month(business.id, start, end)

        recent = (JournalEntry.query
                  .filter_by(business_id=business.id)
                  .order_by(JournalEntry.created_at.desc()).limit(8).all())
        drafts = (JournalEntry.query
                  .filter_by(business_id=business.id, status="draft")
                  .order_by(JournalEntry.created_at.desc()).limit(5).all())
        recent_adjustments = (Adjustment.query
                              .filter_by(business_id=business.id)
                              .order_by(Adjustment.created_at.desc()).limit(5).all())
        products = analytics.product_performance(business.id)[:6]
        counts = {
            "accounts": Account.query.filter_by(business_id=business.id).count(),
            "entries": JournalEntry.query.filter_by(business_id=business.id).count(),
            "posted": JournalEntry.query.filter_by(business_id=business.id,
                                                   status="posted").count(),
            "adjustments": Adjustment.query.filter_by(business_id=business.id).count(),
        }
        # ---- previous equal-length period: growth chips only when data exists
        span = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)
        previous = analytics.dashboard_summary(business.id, prev_start, prev_end)
        growth = {}
        if previous["has_data"]:
            for key in ("total_income", "total_expenses", "gross_profit", "net_profit",
                        "cash_balance", "receivable", "payable"):
                value = summary[key]
                base = previous[key]
                if key in ("gross_profit",) and not summary["is_gross_profit"]:
                    value = -summary["gross_loss"]
                    base = -previous["gross_loss"] if not previous["is_gross_profit"] \
                        else previous["gross_profit"]
                if key == "net_profit" and not summary["is_profit"]:
                    value = -summary["net_loss"]
                    base = -previous["net_loss"] if not previous["is_profit"] \
                        else previous["net_profit"]
                growth[key] = analytics.growth(value, base)

        # ---- expense analysis (share of total expenses by sub-category)
        expense_balances = account_balances(business.id, start, end, include_adjustments=True)
        buckets = {}
        for row in expense_balances.values():
            account = row["account"]
            if account.category != "Expenses":
                continue
            amount = row["debit_balance"] - row["credit_balance"]
            if amount <= 0:
                continue
            label = account.subcategory_label or account.name
            buckets[label] = buckets.get(label, ZERO) + amount
        ordered = sorted(buckets.items(), key=lambda item: item[1], reverse=True)
        top, rest = ordered[:4], ordered[4:]
        if rest:
            top.append(("Other", sum((amount for _, amount in rest), ZERO)))
        expense_total = sum((amount for _, amount in top), ZERO)
        expense_breakdown = [
            {"label": label,
             "amount": money(amount),
             "percent": money(amount / expense_total * 100) if expense_total else ZERO}
            for label, amount in top
        ]

        chart_data = {
            "labels": series["labels"],
            "sales": series["sales"],
            "income": series["income"],
            "expenses": series["expenses"],
            "profit": series["profit"],
            "products": [p["name"] for p in products],
            "product_revenue": [float(p["revenue"]) for p in products],
            "product_profit": [float(p["profit"]) for p in products],
            "expense_labels": [row["label"] for row in expense_breakdown],
            "expense_values": [float(row["amount"]) for row in expense_breakdown],
            "summary": {
                "income": float(summary["total_income"]),
                "expenses": float(summary["total_expenses"]),
                "assets": float(summary["total_assets"]),
                "liabilities": float(summary["total_liabilities"]),
            },
        }
        return render_template("dashboard.html", summary=summary, chart_data=chart_data,
                               recent=recent, drafts=drafts,
                               recent_adjustments=recent_adjustments, counts=counts,
                               products=products, growth=growth,
                               expense_breakdown=expense_breakdown,
                               previous=previous, previous_label=period_label(prev_start, prev_end),
                               previous_start=prev_start, previous_end=prev_end,
                               user_first_name=(current_user().name or "there").split(" ")[0])

    # -- global search -----------------------------------------------------
    @app.route("/search")
    @login_required
    def search():
        """Search accounts, vouchers and products of the signed-in business."""
        business = get_business()
        query = (request.args.get("q") or "").strip()
        accounts_found, vouchers_found, products_found = [], [], []
        if len(query) >= 2:
            like = f"%{query}%"
            accounts_found = (Account.query
                              .filter(Account.business_id == business.id,
                                      or_(Account.code.ilike(like),
                                          Account.name.ilike(like),
                                          Account.subcategory.ilike(like)))
                              .order_by(Account.code).limit(25).all())
            vouchers_found = (JournalEntry.query
                              .filter(JournalEntry.business_id == business.id,
                                      or_(JournalEntry.voucher_no.ilike(like),
                                          JournalEntry.description.ilike(like),
                                          JournalEntry.narration.ilike(like),
                                          JournalEntry.reference.ilike(like)))
                              .order_by(JournalEntry.entry_date.desc()).limit(25).all())
            products_found = (Product.query
                              .filter(Product.business_id == business.id,
                                      or_(Product.code.ilike(like),
                                          Product.name.ilike(like),
                                          Product.category.ilike(like)))
                              .order_by(Product.code).limit(25).all())
        return render_template("search.html", q=query,
                               accounts_found=accounts_found,
                               vouchers_found=vouchers_found,
                               products_found=products_found,
                               total=len(accounts_found) + len(vouchers_found)
                                     + len(products_found))

    # -- business profile --------------------------------------------------
    @app.route("/business", methods=["GET", "POST"])
    @login_required
    def business_profile():
        business = get_business()
        if request.method == "POST":
            previous = {"name": business.name, "owner_name": business.owner_name,
                        "currency": business.currency}
            name = (request.form.get("name") or "").strip()
            if not name:
                flash("Business name is required.", "danger")
                return render_template("business_profile.html", business=business)
            fy_start = _parse_date(request.form.get("fy_start"))
            fy_end = _parse_date(request.form.get("fy_end"))
            if not fy_start or not fy_end or fy_start > fy_end:
                flash("Please provide a valid financial year start and end date.", "danger")
                return render_template("business_profile.html", business=business)
            try:
                logo = save_upload(request.files.get("logo"), "logos",
                                   {"png", "jpg", "jpeg", "webp", "gif"})
                if logo:
                    business.logo = logo
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_template("business_profile.html", business=business)

            business.name = name
            business.owner_name = (request.form.get("owner_name") or "").strip()
            business.address = (request.form.get("address") or "").strip()
            business.phone = (request.form.get("phone") or "").strip()
            business.email = (request.form.get("email") or "").strip()
            business.business_type = (request.form.get("business_type") or "").strip()
            business.currency = (request.form.get("currency") or "PKR").strip()
            business.fy_start = fy_start
            business.fy_end = fy_end
            business.accounting_method = request.form.get("accounting_method") or "Accrual"
            business.description = (request.form.get("description") or "").strip()
            db.session.commit()
            audit("Business profile updated", "Business", business.id, previous,
                  {"name": business.name})
            flash("Business profile saved.", "success")
            return redirect(url_for("business_profile"))
        periods = (AccountingPeriod.query.filter_by(business_id=business.id)
                   .order_by(AccountingPeriod.start_date).all())
        return render_template("business_profile.html", business=business, periods=periods)

    @app.route("/business/logo/remove", methods=["POST"])
    @login_required
    def remove_business_logo():
        """Remove the uploaded logo only — every other business field is untouched."""
        business = get_business()
        if not business.logo:
            flash("There is no logo to remove.", "info")
            return redirect(url_for("business_profile"))
        previous_logo = business.logo
        business.logo = None
        db.session.commit()
        audit("Business logo removed", "Business", business.id,
              previous={"logo": previous_logo})
        flash("Business logo removed. Reports will use the plain letterhead.", "success")
        return redirect(url_for("business_profile"))

    @app.route("/business/periods", methods=["POST"])
    @business_required
    def add_period():
        business = get_business()
        label = (request.form.get("label") or "").strip()
        start = _parse_date(request.form.get("start_date"))
        end = _parse_date(request.form.get("end_date"))
        kind = request.form.get("kind") or "Custom"
        if not label or not start or not end or start > end:
            flash("Period label and a valid date range are required.", "danger")
            return redirect(url_for("business_profile"))
        if AccountingPeriod.query.filter_by(business_id=business.id, label=label).first():
            flash("A period with that label already exists.", "danger")
            return redirect(url_for("business_profile"))
        db.session.add(AccountingPeriod(business_id=business.id, label=label, kind=kind,
                                        start_date=start, end_date=end, is_active=False))
        db.session.commit()
        audit("Accounting period created", "AccountingPeriod", None, new={"label": label})
        flash(f"Accounting period '{label}' created.", "success")
        return redirect(url_for("business_profile"))

    @app.route("/business/periods/<int:period_id>/activate", methods=["POST"])
    @business_required
    def activate_period(period_id):
        business = get_business()
        for p in business.periods:
            p.is_active = (p.id == period_id)
        db.session.commit()
        session.pop("pr_start", None)
        session.pop("pr_end", None)
        flash("Active accounting period updated.", "success")
        return redirect(url_for("business_profile"))

    @app.route("/business/periods/<int:period_id>/delete", methods=["POST"])
    @business_required
    def delete_period(period_id):
        business = get_business()
        period = AccountingPeriod.query.filter_by(id=period_id,
                                                  business_id=business.id).first_or_404()
        if period.is_active:
            flash("The active period cannot be deleted.", "danger")
            return redirect(url_for("business_profile"))
        db.session.delete(period)
        db.session.commit()
        flash("Accounting period deleted.", "success")
        return redirect(url_for("business_profile"))

    # -- chart of accounts -------------------------------------------------
    @app.route("/accounts")
    @business_required
    def accounts():
        business = get_business()
        category = request.args.get("category") or ""
        search = (request.args.get("q") or "").strip().lower()
        status = request.args.get("status") or ""
        query = Account.query.filter_by(business_id=business.id)
        if category:
            query = query.filter_by(category=category)
        if status == "active":
            query = query.filter_by(is_active=True)
        elif status == "inactive":
            query = query.filter_by(is_active=False)
        items = query.order_by(Account.code).all()
        if search:
            items = [a for a in items
                     if search in a.name.lower() or search in a.code.lower()
                     or search in (a.description or "").lower()]
        balances = account_balances(business.id, *period_range(), include_adjustments=True)
        totals = {a.id: balances.get(a.id, {}).get("net", ZERO) for a in items}
        return render_template("accounts.html", accounts=items, balances=totals,
                               category=category, search=search, status=status)

    @app.route("/accounts/new", methods=["POST"])
    @business_required
    def account_create():
        business = get_business()
        data = request.form
        code = (data.get("code") or "").strip()
        name = (data.get("name") or "").strip()
        category = data.get("category")
        subcategory = data.get("subcategory")
        if not code or not name:
            flash("Account code and name are required.", "danger")
            return redirect(url_for("accounts"))
        if category not in CATEGORIES:
            flash("Please select a valid account category.", "danger")
            return redirect(url_for("accounts"))
        if not subcategory or SUBCATEGORIES.get(subcategory, ("", None))[1] != category:
            flash("Please select a valid subcategory for the chosen category.", "danger")
            return redirect(url_for("accounts"))
        if Account.query.filter_by(business_id=business.id, code=code).first():
            flash(f"Account code {code} already exists. Codes must be unique.", "danger")
            return redirect(url_for("accounts"))
        try:
            od = safe_amount(data.get("opening_debit"), "Opening debit")
            oc = safe_amount(data.get("opening_credit"), "Opening credit")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("accounts"))
        if od and oc:
            flash("Enter either an opening debit or an opening credit, not both.", "danger")
            return redirect(url_for("accounts"))
        account = Account(business_id=business.id, code=code, name=name,
                          category=category, subcategory=subcategory,
                          normal_balance=NORMAL_BALANCE[category],
                          opening_debit=od, opening_credit=oc,
                          description=(data.get("description") or "").strip(),
                          is_active=data.get("is_active", "on") == "on")
        db.session.add(account)
        db.session.commit()
        audit("Account created", "Account", account.id, new={"code": code, "name": name})
        flash(f"Account '{name}' created.", "success")
        return redirect(url_for("accounts"))

    @app.route("/accounts/<int:account_id>/edit", methods=["POST"])
    @business_required
    def account_edit(account_id):
        business = get_business()
        account = Account.query.filter_by(id=account_id,
                                         business_id=business.id).first_or_404()
        data = request.form
        previous = {"code": account.code, "name": account.name,
                    "opening_debit": str(account.opening_debit),
                    "opening_credit": str(account.opening_credit)}
        code = (data.get("code") or "").strip()
        name = (data.get("name") or "").strip()
        category = data.get("category")
        subcategory = data.get("subcategory")
        if not code or not name or category not in CATEGORIES:
            flash("Account code, name and a valid category are required.", "danger")
            return redirect(url_for("accounts"))
        if not subcategory or SUBCATEGORIES.get(subcategory, ("", None))[1] != category:
            flash("Please select a valid subcategory for the chosen category.", "danger")
            return redirect(url_for("accounts"))
        clash = Account.query.filter(Account.business_id == business.id,
                                     Account.code == code,
                                     Account.id != account.id).first()
        if clash:
            flash(f"Account code {code} is already used by another account.", "danger")
            return redirect(url_for("accounts"))
        if category != account.category:
            used = JournalLine.query.filter_by(account_id=account.id).count()
            if used:
                flash("The category cannot be changed because this account already has "
                      "posted transactions.", "danger")
                return redirect(url_for("accounts"))
        try:
            od = safe_amount(data.get("opening_debit"), "Opening debit")
            oc = safe_amount(data.get("opening_credit"), "Opening credit")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("accounts"))
        if od and oc:
            flash("Enter either an opening debit or an opening credit, not both.", "danger")
            return redirect(url_for("accounts"))
        account.code, account.name = code, name
        account.category, account.subcategory = category, subcategory
        account.normal_balance = NORMAL_BALANCE[category]
        account.opening_debit, account.opening_credit = od, oc
        account.description = (data.get("description") or "").strip()
        account.is_active = data.get("is_active", "on") == "on"
        db.session.commit()
        audit("Account updated", "Account", account.id, previous,
              {"code": code, "name": name})
        flash("Account updated.", "success")
        return redirect(url_for("accounts"))

    @app.route("/accounts/<int:account_id>/toggle", methods=["POST"])
    @business_required
    def account_toggle(account_id):
        business = get_business()
        account = Account.query.filter_by(id=account_id,
                                         business_id=business.id).first_or_404()
        account.is_active = not account.is_active
        db.session.commit()
        audit("Account status changed", "Account", account.id,
              {"is_active": not account.is_active}, {"is_active": account.is_active})
        flash(f"Account '{account.name}' is now "
              f"{'active' if account.is_active else 'inactive'}.", "success")
        return redirect(url_for("accounts"))

    @app.route("/accounts/load-default", methods=["POST"])
    @business_required
    def load_default_accounts():
        business = get_business()
        created = 0
        for code, name, category, sub in DEFAULT_CHART:
            if Account.query.filter_by(business_id=business.id, code=code).first():
                continue
            db.session.add(Account(business_id=business.id, code=code, name=name,
                                   category=category, subcategory=sub,
                                   normal_balance=NORMAL_BALANCE[category]))
            created += 1
        db.session.commit()
        audit("Standard chart of accounts loaded", "Account", None,
              new={"accounts_created": created})
        flash(f"{created} standard accounts added. Edit them to match your business.", "success")
        return redirect(url_for("accounts"))

    # -- transactions ------------------------------------------------------
    @app.route("/transactions")
    @business_required
    def transactions():
        business = get_business()
        page = max(1, int(request.args.get("page", 1) or 1))
        per_page = 20
        query = JournalEntry.query.filter_by(business_id=business.id)
        start = _parse_date(request.args.get("start"))
        end = _parse_date(request.args.get("end"))
        status = request.args.get("status") or ""
        entry_type = request.args.get("type") or ""
        account_id = request.args.get("account_id", type=int)
        search = (request.args.get("q") or "").strip()
        if start:
            query = query.filter(JournalEntry.entry_date >= start)
        if end:
            query = query.filter(JournalEntry.entry_date <= end)
        if status:
            query = query.filter(JournalEntry.status == status)
        if entry_type:
            query = query.filter(JournalEntry.entry_type == entry_type)
        if search:
            query = query.filter(db.or_(JournalEntry.voucher_no.ilike(f"%{search}%"),
                                        JournalEntry.description.ilike(f"%{search}%"),
                                        JournalEntry.narration.ilike(f"%{search}%")))
        if account_id:
            query = query.join(JournalLine).filter(JournalLine.account_id == account_id)
        total = query.distinct().count()
        items = (query.distinct().order_by(JournalEntry.entry_date.desc(),
                                           JournalEntry.id.desc())
                 .offset((page - 1) * per_page).limit(per_page).all())
        accounts_list = (Account.query.filter_by(business_id=business.id)
                         .order_by(Account.code).all())
        pagination = Pagination(items, page, per_page, total)
        return render_template("transactions.html", pagination=pagination, items=items,
                               accounts=accounts_list, status=status, entry_type=entry_type,
                               account_id=account_id, search=search,
                               start=start, end=end)

    @app.route("/transactions/new", methods=["GET", "POST"])
    @business_required
    def transaction_new():
        business = get_business()
        accounts_list = (Account.query.filter_by(business_id=business.id, is_active=True)
                         .order_by(Account.code).all())
        if request.method == "POST":
            rows = []
            for i in range(1, 41):
                rows.append({"account_id": request.form.get(f"account_id_{i}"),
                             "debit": request.form.get(f"debit_{i}"),
                             "credit": request.form.get(f"credit_{i}"),
                             "description": request.form.get(f"description_{i}")})
            data = {
                "voucher_no": request.form.get("voucher_no"),
                "entry_date": request.form.get("entry_date"),
                "entry_type": request.form.get("entry_type"),
                "description": request.form.get("description"),
                "narration": request.form.get("narration"),
                "reference": request.form.get("reference"),
            }
            action = request.form.get("action", "post")
            try:
                attachment = save_upload(request.files.get("attachment"), "attachments")
                if attachment:
                    data["attachment"] = attachment
                status = "draft" if action == "draft" else "posted"
                entry = journal_engine.create_entry(
                    business.id, current_user().id, data, rows, status=status,
                    period_id=(business.active_period().id if business.active_period() else None))
                audit("Transaction created", "JournalEntry", entry.id, new={
                    "voucher_no": entry.voucher_no, "amount": str(entry.total_debit),
                    "status": entry.status})
                if entry.status == "draft":
                    flash(f"Draft {entry.voucher_no} saved. Post it when you are ready.",
                          "success")
                else:
                    flash(f"{entry.voucher_no} posted successfully "
                          f"(Dr {entry.total_debit} = Cr {entry.total_credit}).", "success")
                return redirect(url_for("transaction_view", entry_id=entry.id))
            except (JournalError, ValueError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return render_template("transaction_entry.html", accounts=accounts_list,
                                       form=request.form, entry=None,
                                       suggested_no=journal_engine.next_voucher_no(business.id),
                                       default_date=default_entry_date())
        return render_template("transaction_entry.html", accounts=accounts_list, form={},
                               entry=None,
                               suggested_no=journal_engine.next_voucher_no(business.id),
                               default_date=default_entry_date())

    @app.route("/transactions/<int:entry_id>")
    @business_required
    def transaction_view(entry_id):
        business = get_business()
        entry = JournalEntry.query.filter_by(id=entry_id,
                                             business_id=business.id).first_or_404()
        accounts_map = {a.id: a for a in
                        Account.query.filter_by(business_id=business.id).all()}
        reversal = JournalEntry.query.filter_by(business_id=business.id,
                                                reversal_of_id=entry.id).first()
        return render_template("transaction_view.html", entry=entry,
                               accounts=accounts_map, reversal=reversal)

    @app.route("/transactions/<int:entry_id>/edit", methods=["GET", "POST"])
    @business_required
    def transaction_edit(entry_id):
        business = get_business()
        entry = JournalEntry.query.filter_by(id=entry_id,
                                             business_id=business.id).first_or_404()
        if entry.status != "draft":
            flash("Posted vouchers cannot be edited. Post a reversal entry instead.", "warning")
            return redirect(url_for("transaction_view", entry_id=entry.id))
        if request.method == "POST":
            rows = []
            for i in range(1, max(21, len(entry.lines) + 6)):
                rows.append({"account_id": request.form.get(f"account_id_{i}"),
                             "debit": request.form.get(f"debit_{i}"),
                             "credit": request.form.get(f"credit_{i}"),
                             "description": request.form.get(f"description_{i}")})
            data = {
                "voucher_no": request.form.get("voucher_no"),
                "entry_date": request.form.get("entry_date"),
                "entry_type": request.form.get("entry_type"),
                "description": request.form.get("description"),
                "narration": request.form.get("narration"),
                "reference": request.form.get("reference"),
            }
            try:
                journal_engine.update_draft(entry, data, rows)
                audit("Draft transaction updated", "JournalEntry", entry.id,
                      new={"voucher_no": entry.voucher_no})
                if request.form.get("action") == "post":
                    journal_engine.post_draft(entry)
                    audit("Transaction posted", "JournalEntry", entry.id,
                          new={"status": "posted"})
                    flash(f"{entry.voucher_no} posted successfully.", "success")
                else:
                    flash("Draft updated.", "success")
                return redirect(url_for("transaction_view", entry_id=entry.id))
            except (JournalError, ValueError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
        accounts_list = (Account.query.filter_by(business_id=business.id, is_active=True)
                         .order_by(Account.code).all())
        return render_template("transaction_entry.html", accounts=accounts_list,
                               form={}, entry=entry, suggested_no=entry.voucher_no,
                               default_date=default_entry_date())

    @app.route("/transactions/<int:entry_id>/post", methods=["POST"])
    @business_required
    def transaction_post(entry_id):
        business = get_business()
        entry = JournalEntry.query.filter_by(id=entry_id,
                                             business_id=business.id).first_or_404()
        try:
            journal_engine.post_draft(entry)
            audit("Transaction posted", "JournalEntry", entry.id, new={"status": "posted"})
            flash(f"{entry.voucher_no} posted.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("transaction_view", entry_id=entry.id))

    @app.route("/transactions/<int:entry_id>/delete", methods=["POST"])
    @business_required
    def transaction_delete(entry_id):
        business = get_business()
        entry = JournalEntry.query.filter_by(id=entry_id,
                                             business_id=business.id).first_or_404()
        try:
            voucher = entry.voucher_no
            journal_engine.delete_draft(entry)
            audit("Draft transaction deleted", "JournalEntry", entry_id,
                  previous={"voucher_no": voucher})
            flash(f"Draft {voucher} deleted.", "success")
            return redirect(url_for("transactions"))
        except JournalError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("transaction_view", entry_id=entry_id))

    @app.route("/transactions/<int:entry_id>/reverse", methods=["POST"])
    @business_required
    def transaction_reverse(entry_id):
        business = get_business()
        entry = JournalEntry.query.filter_by(id=entry_id,
                                             business_id=business.id).first_or_404()
        try:
            reason = (request.form.get("reason") or "").strip()
            reversal_date = _parse_date(request.form.get("reversal_date"))
            reversal = journal_engine.reverse_entry(entry, current_user().id, reason,
                                                    reversal_date)
            audit("Transaction reversed", "JournalEntry", entry.id,
                  previous={"voucher_no": entry.voucher_no},
                  new={"reversal": reversal.voucher_no, "reason": reason})
            flash(f"Reversal voucher {reversal.voucher_no} posted. "
                  f"Original {entry.voucher_no} marked as reversed.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("transaction_view", entry_id=entry.id))

    # -- reports -----------------------------------------------------------
    @app.route("/journal")
    @business_required
    def journal():
        business = get_business()
        start, end = period_range()
        account_id = request.args.get("account_id", type=int)
        search = (request.args.get("q") or "").strip()
        data = journal_rows(business.id, start, end, account_id, search)
        accounts_list = (Account.query.filter_by(business_id=business.id)
                         .order_by(Account.code).all())
        return render_template("journal.html", data=data, accounts=accounts_list,
                               account_id=account_id, search=search)

    @app.route("/ledger")
    @business_required
    def ledger():
        business = get_business()
        start, end = period_range()
        accounts_list = (Account.query.filter_by(business_id=business.id)
                         .order_by(Account.code).all())
        account_id = request.args.get("account_id", type=int)
        account = None
        report = None
        if account_id:
            account = Account.query.filter_by(id=account_id,
                                              business_id=business.id).first_or_404()
            report = ledger_rows(business.id, account, start, end,
                                 include_adjustments=True, include_closing=True)
        return render_template("ledger.html", accounts=accounts_list, account=account,
                               report=report, account_id=account_id)

    @app.route("/trial-balance")
    @business_required
    def trial_balance_page():
        business = get_business()
        start, end = period_range()
        data = trial_balance(business.id, start, end)
        by_cat = {}
        for row in data["rows"]:
            by_cat.setdefault(row["category"], []).append(row)
        return render_template("trial_balance.html", data=data, by_cat=by_cat,
                               include_zero=request.args.get("zero") == "1")

    @app.route("/trial-balance/manual", methods=["GET", "POST"])
    @business_required
    def trial_balance_manual():
        """Enter or import a trial balance directly into opening balances."""
        business = get_business()
        preview = None
        if request.method == "POST":
            action = request.form.get("action", "preview")
            entries = []
            for i in range(1, 26):
                code = (request.form.get(f"code_{i}") or "").strip()
                debit = request.form.get(f"debit_{i}")
                credit = request.form.get(f"credit_{i}")
                if not code:
                    continue
                entries.append({"code": code,
                                "debit": safe_amount(debit) if debit else ZERO,
                                "credit": safe_amount(credit) if credit else ZERO})
            total_d = sum((e["debit"] for e in entries), ZERO)
            total_c = sum((e["credit"] for e in entries), ZERO)
            unknown = [e["code"] for e in entries if not
                       Account.query.filter_by(business_id=business.id,
                                               code=e["code"]).first()]
            preview = {"entries": entries, "total_debit": money(total_d),
                       "total_credit": money(total_c),
                       "difference": money(total_d - total_c),
                       "unknown": unknown,
                       "is_balanced": money(total_d) == money(total_c)}
            if action == "apply":
                if unknown:
                    flash("Unknown account codes: " + ", ".join(unknown)
                          + ". Create the accounts first.", "danger")
                elif money(total_d) != money(total_c):
                    flash("Cannot apply: the trial balance does not balance. "
                          f"Difference Rs. {money(total_d - total_c):,}.", "danger")
                else:
                    for e in entries:
                        acc = Account.query.filter_by(business_id=business.id,
                                                      code=e["code"]).first()
                        acc.opening_debit = e["debit"]
                        acc.opening_credit = e["credit"]
                    db.session.commit()
                    audit("Trial balance entered manually", "Account", None,
                          new={"accounts": len(entries), "total": str(money(total_d))})
                    flash(f"Opening balances saved for {len(entries)} accounts.", "success")
                    return redirect(url_for("trial_balance_page"))
        accounts_list = (Account.query.filter_by(business_id=business.id)
                         .order_by(Account.code).all())
        return render_template("trial_balance_manual.html", accounts=accounts_list,
                               preview=preview, rows=range(1, 26))

    @app.route("/adjustments")
    @business_required
    def adjustments():
        business = get_business()
        start, end = period_range()
        items = (Adjustment.query.filter_by(business_id=business.id)
                 .filter(Adjustment.adj_date >= start, Adjustment.adj_date <= end)
                 .order_by(Adjustment.adj_date.desc(), Adjustment.id.desc()).all())
        accounts_map = {a.id: a for a in
                        Account.query.filter_by(business_id=business.id).all()}
        total = sum((a.total_debit for a in items), ZERO)
        return render_template("adjustments.html", items=items, accounts=accounts_map,
                               total=money(total))

    @app.route("/adjustments/new", methods=["GET", "POST"])
    @business_required
    def adjustment_new():
        business = get_business()
        accounts_list = (Account.query.filter_by(business_id=business.id, is_active=True)
                         .order_by(Account.code).all())
        if request.method == "POST":
            rows = []
            for i in range(1, 11):
                rows.append({"account_id": request.form.get(f"account_id_{i}"),
                             "debit": request.form.get(f"debit_{i}"),
                             "credit": request.form.get(f"credit_{i}"),
                             "description": request.form.get(f"description_{i}")})
            data = {"adj_no": request.form.get("adj_no"),
                    "adj_date": request.form.get("adj_date"),
                    "kind": request.form.get("kind"),
                    "description": request.form.get("description"),
                    "narration": request.form.get("narration")}
            try:
                adj = adjustment_engine.create_adjustment(
                    business.id, current_user().id, data, rows,
                    status="draft" if request.form.get("action") == "draft" else "posted",
                    period_id=business.active_period().id if business.active_period() else None)
                audit("Adjustment created", "Adjustment", adj.id,
                      new={"adj_no": adj.adj_no, "amount": str(adj.total_debit)})
                flash(f"Adjustment {adj.adj_no} saved ({adj.status}).", "success")
                return redirect(url_for("adjustments"))
            except (JournalError, ValueError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
        suggested = journal_engine.next_voucher_no(business.id, prefix="ADJ-",
                                                   model=Adjustment, field="adj_no")
        return render_template("adjustment_form.html", accounts=accounts_list,
                               form=request.form, suggested_no=suggested,
                               guide=adjustment_engine.ADJUSTMENT_GUIDE,
                               line_rows=range(1, 11))

    @app.route("/adjustments/<int:adjustment_id>/post", methods=["POST"])
    @business_required
    def adjustment_post(adjustment_id):
        business = get_business()
        adj = Adjustment.query.filter_by(id=adjustment_id,
                                         business_id=business.id).first_or_404()
        try:
            adjustment_engine.post_adjustment(adj)
            audit("Adjustment posted", "Adjustment", adj.id, new={"status": "posted"})
            flash(f"Adjustment {adj.adj_no} posted.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("adjustments"))

    @app.route("/adjustments/<int:adjustment_id>/delete", methods=["POST"])
    @business_required
    def adjustment_delete(adjustment_id):
        business = get_business()
        adj = Adjustment.query.filter_by(id=adjustment_id,
                                         business_id=business.id).first_or_404()
        try:
            no = adj.adj_no
            adjustment_engine.delete_adjustment(adj)
            audit("Adjustment deleted", "Adjustment", adjustment_id, previous={"adj_no": no})
            flash(f"Adjustment {no} deleted.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("adjustments"))

    @app.route("/adjusted-trial-balance")
    @business_required
    def adjusted_trial_balance_page():
        business = get_business()
        start, end = period_range()
        data = adjusted_trial_balance(business.id, start, end)
        return render_template("adjusted_trial_balance.html", data=data)

    @app.route("/trading-account")
    @business_required
    def trading_account_page():
        business = get_business()
        start, end = period_range()
        data = trading_account(business.id, start, end)
        return render_template("trading_account.html", t=data)

    @app.route("/profit-loss")
    @business_required
    def profit_loss_page():
        business = get_business()
        start, end = period_range()
        bal = None
        t = trading_account(business.id, start, end)
        pl = profit_and_loss(business.id, start, end, trading=t)
        return render_template("profit_loss.html", pl=pl, t=t)

    @app.route("/balance-sheet")
    @business_required
    def balance_sheet_page():
        business = get_business()
        start, end = period_range()
        pl = profit_and_loss(business.id, start, end)
        bs = balance_sheet(business.id, start, end, pl=pl)
        return render_template("balance_sheet.html", bs=bs, pl=pl)

    @app.route("/closing-entries")
    @business_required
    def closing_entries_page():
        business = get_business()
        start, end = period_range()
        preview = closing_engine.preview_closing(business.id, start, end)
        entries = (ClosingEntry.query.filter_by(business_id=business.id)
                   .order_by(ClosingEntry.entry_date.desc()).all())
        accounts_map = {a.id: a for a in
                        Account.query.filter_by(business_id=business.id).all()}
        return render_template("closing_entries.html", preview=preview, entries=entries,
                               accounts=accounts_map)

    @app.route("/closing-entries/generate", methods=["POST"])
    @business_required
    def closing_generate():
        business = get_business()
        start, end = period_range()
        try:
            entry = closing_engine.create_closing_entry(
                business.id, current_user().id, start, end,
                entry_date=request.form.get("entry_date") or end,
                period_id=business.active_period().id if business.active_period() else None,
                description=request.form.get("description"))
            audit("Closing entry created", "ClosingEntry", entry.id,
                  new={"entry_no": entry.entry_no, "net": str(entry.net_result)})
            if request.form.get("action") == "post":
                closing_engine.post_closing_entry(entry)
                audit("Closing entry posted", "ClosingEntry", entry.id,
                      new={"status": "posted"})
                flash(f"Closing entry {entry.entry_no} posted. Temporary accounts are closed.",
                      "success")
            else:
                flash(f"Closing entry {entry.entry_no} saved as draft for review.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("closing_entries_page"))

    @app.route("/closing-entries/<int:closing_id>/post", methods=["POST"])
    @business_required
    def closing_post(closing_id):
        business = get_business()
        entry = ClosingEntry.query.filter_by(id=closing_id,
                                             business_id=business.id).first_or_404()
        try:
            closing_engine.post_closing_entry(entry)
            audit("Closing entry posted", "ClosingEntry", entry.id, new={"status": "posted"})
            flash(f"Closing entry {entry.entry_no} posted.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("closing_entries_page"))

    @app.route("/closing-entries/<int:closing_id>/delete", methods=["POST"])
    @business_required
    def closing_delete(closing_id):
        business = get_business()
        entry = ClosingEntry.query.filter_by(id=closing_id,
                                             business_id=business.id).first_or_404()
        try:
            closing_engine.delete_closing_entry(entry)
            audit("Closing entry deleted", "ClosingEntry", closing_id)
            flash("Closing entry draft deleted.", "success")
        except JournalError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("closing_entries_page"))

    @app.route("/post-closing-trial-balance")
    @business_required
    def post_closing_tb_page():
        business = get_business()
        start, end = period_range()
        data = post_closing_trial_balance(business.id, start, end)
        return render_template("post_closing_trial_balance.html", data=data)

    @app.route("/cash-flow")
    @business_required
    def cash_flow_page():
        business = get_business()
        start, end = period_range()
        data = cash_flow(business.id, start, end)
        return render_template("cash_flow.html", cf=data)

    @app.route("/analysis")
    @business_required
    def analysis_page():
        business = get_business()
        grain = request.args.get("grain") or "monthly"
        start = _parse_date(request.args.get("start"))
        end = _parse_date(request.args.get("end"))
        comparison = analytics.period_comparison(business.id, grain, start, end)
        annual = analytics.annual_analysis(business.id, start or end or date.today())
        series = analytics.sales_expense_by_month(
            business.id, min(comparison["start"], comparison["prev_start"]),
            max(comparison["end"], comparison["prev_end"]))
        chart = {
            "labels": series["labels"], "income": series["income"],
            "expenses": series["expenses"], "profit": series["profit"],
            "cur_label": comparison["label"], "prev_label": comparison["prev_label"],
            "cur_income": float(comparison["current"]["total_income"]),
            "cur_expenses": float(comparison["current"]["total_expenses"]),
            "prev_income": float(comparison["previous"]["total_income"]),
            "prev_expenses": float(comparison["previous"]["total_expenses"]),
        }
        return render_template("analysis.html", comparison=comparison, annual=annual,
                               chart=chart, grain=grain, series=series)

    @app.route("/api/charts/analysis")
    @business_required
    def api_analysis_charts():
        business = get_business()
        grain = request.args.get("grain") or "monthly"
        comparison = analytics.period_comparison(business.id, grain)
        series = analytics.sales_expense_by_month(business.id, comparison["prev_start"],
                                                  comparison["end"])
        return jsonify({"series": series,
                        "current": {k: str(v) for k, v in comparison["current"].items()
                                    if isinstance(v, Decimal)}})

    # -- products / inventory / budgets ------------------------------------
    @app.route("/products", methods=["GET", "POST"])
    @business_required
    def products():
        business = get_business()
        if request.method == "POST":
            code = (request.form.get("code") or "").strip()
            name = (request.form.get("name") or "").strip()
            if not code or not name:
                flash("Product code and name are required.", "danger")
                return redirect(url_for("products"))
            if Product.query.filter_by(business_id=business.id, code=code).first():
                flash(f"Product code {code} already exists.", "danger")
                return redirect(url_for("products"))
            try:
                product = Product(
                    business_id=business.id, code=code, name=name,
                    category=(request.form.get("category") or "").strip(),
                    purchase_cost=safe_amount(request.form.get("purchase_cost")),
                    selling_price=safe_amount(request.form.get("selling_price")),
                    opening_qty=int(request.form.get("opening_qty") or 0),
                    reorder_level=int(request.form.get("reorder_level") or 0),
                    discount_percent=float(request.form.get("discount_percent") or 0))
            except (ValueError, TypeError) as exc:
                flash(str(exc), "danger")
                return redirect(url_for("products"))
            db.session.add(product)
            db.session.commit()
            audit("Product created", "Product", product.id, new={"code": code, "name": name})
            flash(f"Product '{name}' created.", "success")
            return redirect(url_for("products"))
        summary = inventory_engine.inventory_summary(business.id)
        return render_template("products.html", summary=summary)

    @app.route("/products/<int:product_id>/edit", methods=["POST"])
    @business_required
    def product_edit(product_id):
        business = get_business()
        product = Product.query.filter_by(id=product_id,
                                          business_id=business.id).first_or_404()
        try:
            product.name = (request.form.get("name") or product.name).strip()
            product.category = (request.form.get("category") or "").strip()
            product.purchase_cost = safe_amount(request.form.get("purchase_cost"))
            product.selling_price = safe_amount(request.form.get("selling_price"))
            product.opening_qty = int(request.form.get("opening_qty") or 0)
            product.reorder_level = int(request.form.get("reorder_level") or 0)
            db.session.commit()
            audit("Product updated", "Product", product.id, new={"name": product.name})
            flash("Product updated.", "success")
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("products"))

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    @business_required
    def product_delete(product_id):
        business = get_business()
        product = Product.query.filter_by(id=product_id,
                                          business_id=business.id).first_or_404()
        db.session.delete(product)
        db.session.commit()
        audit("Product deleted", "Product", product_id, previous={"code": product.code})
        flash("Product deleted.", "success")
        return redirect(url_for("products"))

    @app.route("/inventory", methods=["GET", "POST"])
    @business_required
    def inventory():
        business = get_business()
        if request.method == "POST":
            kind = request.form.get("kind")
            product = Product.query.filter_by(id=request.form.get("product_id", type=int),
                                              business_id=business.id).first()
            if not product:
                flash("Please select a valid product.", "danger")
                return redirect(url_for("inventory"))
            try:
                if kind == "sale":
                    sale, entry = inventory_engine.record_sale(
                        business.id, current_user().id, product,
                        request.form.get("quantity"), request.form.get("rate"),
                        request.form.get("txn_date"), request.form.get("party"),
                        request.form.get("document_no"),
                        request.form.get("discount") or ZERO,
                        post_journal=request.form.get("post_journal") == "on")
                    audit("Sale recorded", "Sale", sale.id,
                          new={"amount": str(sale.amount), "product": product.name,
                               "journal": entry.voucher_no if entry else None})
                    flash(f"Sale recorded for {product.name}. "
                          + (f"Journal draft {entry.voucher_no} created."
                             if entry else ""), "success")
                elif kind == "purchase":
                    purchase, entry = inventory_engine.record_purchase(
                        business.id, current_user().id, product,
                        request.form.get("quantity"), request.form.get("rate"),
                        request.form.get("txn_date"), request.form.get("party"),
                        request.form.get("document_no"),
                        post_journal=request.form.get("post_journal") == "on")
                    audit("Purchase recorded", "Purchase", purchase.id,
                          new={"amount": str(purchase.amount), "product": product.name})
                    flash(f"Purchase recorded for {product.name}. "
                          + (f"Journal draft {entry.voucher_no} created."
                             if entry else ""), "success")
                else:
                    flash("Unknown transaction type.", "danger")
            except (JournalError, ValueError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            return redirect(url_for("inventory"))
        summary = inventory_engine.inventory_summary(business.id)
        txns = (InventoryTransaction.query.filter_by(business_id=business.id)
                .order_by(InventoryTransaction.txn_date.desc(),
                          InventoryTransaction.id.desc()).limit(50).all())
        products_map = {p.id: p for p in summary["products"]}
        sales = (Sale.query.filter_by(business_id=business.id)
                 .order_by(Sale.sale_date.desc()).limit(20).all())
        purchases = (Purchase.query.filter_by(business_id=business.id)
                     .order_by(Purchase.purchase_date.desc()).limit(20).all())
        return render_template("inventory.html", summary=summary, txns=txns,
                               products_map=products_map, sales=sales,
                               purchases=purchases, default_date=default_entry_date())

    @app.route("/budgets", methods=["GET", "POST"])
    @business_required
    def budgets():
        business = get_business()
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            start = _parse_date(request.form.get("start_date"))
            end = _parse_date(request.form.get("end_date"))
            if not name or not start or not end or start > end:
                flash("Budget name and a valid period are required.", "danger")
                return redirect(url_for("budgets"))
            try:
                budget = Budget(business_id=business.id, name=name,
                                kind=request.form.get("kind") or "Monthly",
                                start_date=start, end_date=end,
                                planned_income=safe_amount(request.form.get("planned_income")),
                                planned_expenses=safe_amount(request.form.get("planned_expenses")),
                                notes=(request.form.get("notes") or "").strip())
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("budgets"))
            db.session.add(budget)
            db.session.commit()
            audit("Budget created", "Budget", budget.id, new={"name": name})
            flash(f"Budget '{name}' created.", "success")
            return redirect(url_for("budgets"))
        items = (Budget.query.filter_by(business_id=business.id)
                 .order_by(Budget.start_date.desc()).all())
        analyses = [analytics.budget_analysis(business.id, b) for b in items]
        chart = {
            "labels": [a["budget"].name for a in analyses],
            "planned_income": [float(a["planned_income"]) for a in analyses],
            "actual_income": [float(a["actual_income"]) for a in analyses],
            "planned_expenses": [float(a["planned_expenses"]) for a in analyses],
            "actual_expenses": [float(a["actual_expenses"]) for a in analyses],
        }
        return render_template("budgets.html", analyses=analyses, chart=chart)

    @app.route("/budgets/<int:budget_id>/delete", methods=["POST"])
    @business_required
    def budget_delete(budget_id):
        business = get_business()
        budget = Budget.query.filter_by(id=budget_id,
                                        business_id=business.id).first_or_404()
        db.session.delete(budget)
        db.session.commit()
        audit("Budget deleted", "Budget", budget_id, previous={"name": budget.name})
        flash("Budget deleted.", "success")
        return redirect(url_for("budgets"))

    # -- import center -----------------------------------------------------
    @app.route("/import")
    @business_required
    def import_center():
        return render_template("import_center.html", kinds=IMPORT_KINDS,
                               info=session.get("import_info"))

    @app.route("/import/upload", methods=["POST"])
    @business_required
    def import_upload():
        kind = request.form.get("kind")
        if kind not in IMPORT_KINDS:
            flash("Please choose what you want to import.", "danger")
            return redirect(url_for("import_center"))
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a file to upload.", "danger")
            return redirect(url_for("import_center"))
        try:
            name = save_upload(file, "imports", {"xlsx", "xls", "csv"})
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("import_center"))
        path = os.path.join(Config.UPLOAD_FOLDER, "imports", name)
        try:
            headers, rows = parse_upload(path)
        except Exception as exc:
            flash(f"Could not read the file: {exc}", "danger")
            return redirect(url_for("import_center"))
        if not rows:
            flash("The uploaded file contains no data rows.", "danger")
            return redirect(url_for("import_center"))
        mapping = auto_map(kind, headers)
        session["import_info"] = {"kind": kind, "path": path, "filename": file.filename,
                                  "headers": headers, "mapping": mapping,
                                  "row_count": len(rows)}
        flash(f"{len(rows)} rows read from {file.filename}. Review the preview below.", "success")
        return redirect(url_for("import_preview"))

    @app.route("/import/preview", methods=["GET", "POST"])
    @business_required
    def import_preview():
        info = session.get("import_info")
        if not info:
            flash("Please upload a file first.", "warning")
            return redirect(url_for("import_center"))
        if request.method == "POST":
            mapping = dict(info.get("mapping") or {})
            seen = False
            for field, _label, _req in IMPORT_KINDS[info["kind"]]["fields"]:
                key = f"map_{field}"
                if key in request.form:
                    seen = True
                    col = request.form.get(key)
                    if col:
                        mapping[field] = col
                    else:
                        mapping.pop(field, None)
            if seen:
                info["mapping"] = mapping
                session["import_info"] = info
        kind = info["kind"]
        try:
            headers, rows = parse_upload(info["path"])
        except Exception as exc:
            flash(f"Could not re-read the file: {exc}", "danger")
            return redirect(url_for("import_center"))
        result = validate_rows(kind, rows, info.get("mapping", {}), get_business().id)
        return render_template("import_preview.html", info=info, result=result,
                               kinds=IMPORT_KINDS, preview_rows=rows[:10])

    @app.route("/import/confirm", methods=["POST"])
    @business_required
    def import_confirm():
        info = session.get("import_info")
        if not info:
            flash("Please upload a file first.", "warning")
            return redirect(url_for("import_center"))
        kind = info["kind"]
        headers, rows = parse_upload(info["path"])
        result = validate_rows(kind, rows, info.get("mapping", {}), get_business().id)
        if not result["valid"]:
            flash("No valid rows to import. Please fix the errors and try again.", "danger")
            return redirect(url_for("import_preview"))
        business = get_business()
        summary = apply_import(kind, result["valid"], business.id, current_user().id,
                               period_id=(business.active_period().id
                                          if business.active_period() else None),
                               post_drafts=request.form.get("as_draft") == "on")
        audit("Data imported", "Import", None,
              new={"kind": kind, "created": summary["created"],
                   "skipped": summary["skipped"], "file": info.get("filename")})
        try:
            os.remove(info["path"])
        except OSError:
            pass
        session.pop("import_info", None)
        flash(f"Import complete: {summary['created']} record(s) created, "
              f"{summary['skipped']} skipped, {summary['updated']} updated.", "success")
        for msg in summary["messages"][:5]:
            flash(msg, "warning")
        return redirect(url_for("import_center"))

    @app.route("/import/cancel", methods=["POST"])
    @business_required
    def import_cancel():
        info = session.pop("import_info", None)
        if info:
            try:
                os.remove(info["path"])
            except OSError:
                pass
        flash("Import cancelled. No data was written.", "info")
        return redirect(url_for("import_center"))

    # -- export center -----------------------------------------------------
    @app.route("/export")
    @business_required
    def export_center():
        business = get_business()
        start, end = period_range()
        accounts_list = Account.query.filter_by(business_id=business.id).order_by(
            Account.code).all()
        return render_template("export_center.html", titles=REPORT_TITLES,
                               accounts=accounts_list)

    @app.route("/export/<fmt>/<report_name>")
    @business_required
    def export_report(fmt, report_name):
        business = get_business()
        start, end = period_range()
        account_id = request.args.get("account_id", type=int)
        if report_name not in REPORT_TITLES:
            abort(404)
        if fmt not in ("pdf", "word", "excel"):
            abort(404)
        report = build_report(report_name, business, start, end, account_id=account_id)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        base = export_filename(business, report_name)
        try:
            if fmt == "pdf":
                buffer = render_pdf(report)
                mimetype = "application/pdf"
                filename = f"{base[:-4]}_{stamp}.pdf"
            elif fmt == "word":
                buffer = render_word(report)
                mimetype = ("application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document")
                filename = f"{report_name}_{stamp}.docx"
            else:
                wb = render_excel(report, business)
                buffer = to_bytes(wb)
                mimetype = ("application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet")
                filename = f"{report_name}_{stamp}.xlsx"
        except Exception as exc:  # pragma: no cover
            flash(f"Export failed: {exc}", "danger")
            return redirect(url_for("export_center"))
        audit("Report exported", "Export", None,
              new={"report": report_name, "format": fmt})
        return send_file(buffer, mimetype=mimetype, as_attachment=True,
                         download_name=filename)

    @app.route("/export/<fmt>/full-project")
    @business_required
    def export_full(fmt):
        business = get_business()
        start, end = period_range()
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        if fmt == "pdf":
            report = build_report("full_project", business, start, end)
            buffer = render_pdf(report)
            mimetype, filename = "application/pdf", f"complete_accounting_file_{stamp}.pdf"
        elif fmt == "word":
            report = build_report("full_project", business, start, end)
            buffer = render_word(report)
            mimetype = ("application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document")
            filename = f"complete_accounting_file_{stamp}.docx"
        elif fmt == "excel":
            wb = render_full_workbook(business, start, end, period_label(start, end))
            buffer = to_bytes(wb)
            mimetype = ("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")
            filename = f"complete_accounting_file_{stamp}.xlsx"
        else:
            abort(404)
        audit("Full project exported", "Export", None, new={"format": fmt})
        return send_file(buffer, mimetype=mimetype, as_attachment=True,
                         download_name=filename)

    # -- settings / audit / backup ----------------------------------------
    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        business = get_business()
        user = current_user()
        settings_row = user.settings or UserSettings(user_id=user.id)
        if request.method == "POST":
            section = request.form.get("section")
            if section == "profile":
                user.name = (request.form.get("name") or user.name).strip()
                email = (request.form.get("email") or user.email).strip().lower()
                clash = User.query.filter(User.email == email,
                                          User.id != user.id).first()
                if clash:
                    flash("That email address is already in use.", "danger")
                else:
                    user.email = email
                    db.session.commit()
                    audit("User profile updated", "User", user.id)
                    flash("Profile updated.", "success")
            elif section == "password":
                current = request.form.get("current_password") or ""
                new = request.form.get("new_password") or ""
                confirm = request.form.get("confirm_password") or ""
                if not user.check_password(current):
                    flash("Current password is incorrect.", "danger")
                elif len(new) < 8:
                    flash("New password must be at least 8 characters.", "danger")
                elif new != confirm:
                    flash("New passwords do not match.", "danger")
                else:
                    user.set_password(new)
                    db.session.commit()
                    audit("Password changed", "User", user.id)
                    flash("Password changed successfully.", "success")
            elif section == "preferences":
                settings_row.currency = request.form.get("currency") or "PKR"
                settings_row.currency_symbol = request.form.get("currency_symbol") or "Rs."
                settings_row.theme = request.form.get("theme") or "light"
                settings_row.invoice_prefix = request.form.get("invoice_prefix") or "INV-"
                settings_row.invoice_terms = request.form.get("invoice_terms") or ""
                settings_row.report_footer = request.form.get("report_footer") or ""
                settings_row.show_decimals = request.form.get("show_decimals") == "on"
                if settings_row.id is None:
                    db.session.add(settings_row)
                if business:
                    business.currency = settings_row.currency
                db.session.commit()
                audit("Preferences updated", "UserSettings", settings_row.id)
                flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        counts = {
            "accounts": Account.query.filter_by(business_id=business.id).count() if business else 0,
            "entries": JournalEntry.query.filter_by(business_id=business.id).count() if business else 0,
            "products": Product.query.filter_by(business_id=business.id).count() if business else 0,
            "budgets": Budget.query.filter_by(business_id=business.id).count() if business else 0,
        }
        return render_template("settings.html", settings=settings_row, counts=counts,
                               user=user)

    @app.route("/audit-log")
    @login_required
    def audit_log():
        page = max(1, int(request.args.get("page", 1) or 1))
        per_page = 40
        query = AuditLog.query.filter_by(user_id=current_user().id)
        action = (request.args.get("action") or "").strip()
        if action:
            query = query.filter(AuditLog.action.ilike(f"%{action}%"))
        total = query.count()
        items = (query.order_by(AuditLog.created_at.desc())
                 .offset((page - 1) * per_page).limit(per_page).all())
        return render_template("audit_log.html",
                               pagination=Pagination(items, page, per_page, total),
                               action=action)

    @app.route("/backup")
    @login_required
    def backup_page():
        folder = os.path.join(Config.UPLOAD_FOLDER, "backups")
        os.makedirs(folder, exist_ok=True)
        files = []
        for f in sorted(os.listdir(folder), reverse=True)[:20]:
            path = os.path.join(folder, f)
            if os.path.isfile(path):
                files.append({"name": f,
                              "size": round(os.path.getsize(path) / 1024, 1),
                              "modified": datetime.fromtimestamp(os.path.getmtime(path))})
        db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        db_size = (round(os.path.getsize(db_path) / 1024, 1)
                   if os.path.exists(db_path) else 0)
        return render_template("backup.html", files=files, db_size=db_size)

    @app.route("/backup/download")
    @login_required
    def backup_download():
        db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        if not os.path.exists(db_path):
            flash("Database file not found.", "danger")
            return redirect(url_for("backup_page"))
        folder = os.path.join(Config.UPLOAD_FOLDER, "backups")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"accounting_pro_backup_{stamp}.db"
        db.session.remove()
        shutil.copy2(db_path, os.path.join(folder, name))
        audit("Database backup downloaded", "Backup", None, new={"file": name})
        return send_file(os.path.join(folder, name), as_attachment=True,
                         download_name=name, mimetype="application/x-sqlite3")

    @app.route("/backup/restore", methods=["POST"])
    @login_required
    def backup_restore():
        file = request.files.get("file")
        if request.form.get("confirm") != "yes":
            flash("Please tick the confirmation box before restoring - this replaces "
                  "the current database.", "danger")
            return redirect(url_for("backup_page"))
        if not file or not file.filename.lower().endswith(".db"):
            flash("Please upload a .db backup file.", "danger")
            return redirect(url_for("backup_page"))
        folder = os.path.join(Config.UPLOAD_FOLDER, "backups")
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "restore_upload.db")
        file.save(tmp)
        with open(tmp, "rb") as fh:
            header = fh.read(16)
        if not header.startswith(b"SQLite format 3"):
            os.remove(tmp)
            flash("The uploaded file is not a valid SQLite database.", "danger")
            return redirect(url_for("backup_page"))
        try:
            con = sqlite3.connect(tmp)
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            con.close()
        except sqlite3.DatabaseError:
            os.remove(tmp)
            flash("The backup file is corrupted and could not be opened.", "danger")
            return redirect(url_for("backup_page"))
        required = {"users", "accounts", "journal_entries", "journal_lines"}
        if not required.issubset(tables):
            os.remove(tmp)
            flash("The backup is missing required tables and was rejected. "
                  f"Found: {', '.join(sorted(tables)) or 'none'}.", "danger")
            return redirect(url_for("backup_page"))

        db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        safety = os.path.join(folder, "pre_restore_"
                              + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(db_path, safety)
        db.session.remove()
        db.engine.dispose()
        shutil.copy2(tmp, db_path)
        os.remove(tmp)
        session.clear()
        flash("Database restored successfully. Please sign in again. "
              f"A safety copy of the previous database was saved as "
              f"{os.path.basename(safety)}.", "success")
        return redirect(url_for("login"))

    # -- uploads -----------------------------------------------------------
    @app.route("/uploads/<path:filename>")
    @login_required
    def uploaded_file(filename):
        safe = os.path.normpath(filename).lstrip("/\\")
        if ".." in safe.split(os.sep):
            abort(404)
        path = os.path.join(Config.UPLOAD_FOLDER, safe)
        if not os.path.exists(path):
            abort(404)
        return send_file(path)

    # -- misc --------------------------------------------------------------
    @app.route("/manifest.json")
    def manifest():
        return send_file(os.path.join(BASE_DIR, "static", "manifest.json"))

    @app.route("/service-worker.js")
    def service_worker():
        return send_file(os.path.join(BASE_DIR, "static", "service-worker.js"),
                         mimetype="application/javascript")


#: A standard, editable chart of accounts (structure only - no business data).
DEFAULT_CHART = [
    ("1000", "Cash in Hand", "Assets", "cash"),
    ("1010", "Bank Account", "Assets", "bank"),
    ("1020", "Accounts Receivable", "Assets", "accounts_receivable"),
    ("1030", "Closing Stock / Inventory", "Assets", "inventory"),
    ("1040", "Opening Stock", "Assets", "opening_stock"),
    ("1050", "Furniture & Fixtures", "Assets", "fixed_asset"),
    ("1060", "Office Equipment", "Assets", "fixed_asset"),
    ("1070", "Machinery", "Assets", "fixed_asset"),
    ("1080", "Building", "Assets", "fixed_asset"),
    ("1090", "Prepaid Expenses", "Assets", "other_asset"),
    ("2000", "Accounts Payable", "Liabilities", "accounts_payable"),
    ("2010", "Outstanding Expenses", "Liabilities", "outstanding_expenses"),
    ("2020", "Bank Loan", "Liabilities", "loan"),
    ("2030", "Other Liabilities", "Liabilities", "other_liability"),
    ("3000", "Owner's Capital", "Equity", "capital"),
    ("3010", "Drawings", "Equity", "drawings"),
    ("3020", "Retained Earnings", "Equity", "retained_earnings"),
    ("4000", "Sales", "Revenue", "sales"),
    ("4010", "Sales Returns", "Revenue", "sales_returns"),
    ("4020", "Other Income", "Revenue", "other_income"),
    ("4030", "Trading A/c (Closing Stock Contra)", "Revenue", "trading_contra"),
    ("5000", "Purchases", "Expenses", "purchases"),
    ("5010", "Purchase Returns", "Expenses", "purchase_returns"),
    ("5020", "Carriage Inward", "Expenses", "direct_expenses"),
    ("5030", "Wages", "Expenses", "direct_expenses"),
    ("5040", "Salaries", "Expenses", "indirect_expenses"),
    ("5050", "Rent", "Expenses", "indirect_expenses"),
    ("5060", "Electricity & Utilities", "Expenses", "indirect_expenses"),
    ("5070", "Advertising", "Expenses", "indirect_expenses"),
    ("5080", "Depreciation", "Expenses", "depreciation"),
    ("5090", "Bad Debts", "Expenses", "bad_debts"),
    ("5100", "Provision for Doubtful Debts", "Expenses", "indirect_expenses"),
]


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
