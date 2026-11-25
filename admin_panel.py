"""
ACCOUNTING PRO — Administrator panel.

A separate area, on its own URL (``/admin``), with its own sign-in, that reads
the *same* database as the accounting application.  Nothing in the business
side changes: the admin area only counts, lists and inspects the records that
the normal users create, so the two halves are always in step.

Sign in with the administrator credentials configured in ``config.py``
(``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``, both overridable with environment
variables).  An administrator is *not* a business user: the admin session is
kept apart from the normal user session and cannot touch a business's books.
"""
import json
import os
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (flash, redirect, render_template, request, session, url_for)
from sqlalchemy import func

from extensions import db
from models import (Account, AccountingPeriod, Adjustment, AuditLog, Budget,
                    Business, ClosingEntry, JournalEntry, JournalLine, Product,
                    User)
from accounting.trial_balance import (trial_balance, adjusted_trial_balance,
                                      post_closing_trial_balance)
from accounting.financial_statements import profit_and_loss

PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_ok"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _admin_credentials(app):
    email = (app.config.get("ADMIN_EMAIL") or "admin@accountingpro.local").strip().lower()
    password = app.config.get("ADMIN_PASSWORD") or "Admin@12345"
    name = app.config.get("ADMIN_NAME") or "System Administrator"
    return email, password, name


def _audit_admin(action, detail=None):
    """Write an entry into the same audit log the business pages use."""
    try:
        db.session.add(AuditLog(
            user_id=None, business_id=None, action=f"Admin · {action}",
            record_type="Admin", record_id=None,
            new_values=str(detail)[:4000] if detail else None,
            ip_address=request.remote_addr or "-"))
        db.session.commit()
    except Exception:                                    # pragma: no cover
        db.session.rollback()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def _db_size():
    uri = db.engine.url.database or ""
    if uri and os.path.exists(uri):
        size = os.path.getsize(uri)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
    return "remote database (Turso)"


def _overview():
    counts = {
        "users": db.session.query(func.count(User.id)).scalar() or 0,
        "businesses": db.session.query(func.count(Business.id)).scalar() or 0,
        "vouchers": db.session.query(func.count(JournalEntry.id)).scalar() or 0,
        "posted": db.session.query(func.count(JournalEntry.id))
                  .filter(JournalEntry.status == "posted").scalar() or 0,
        "drafts": db.session.query(func.count(JournalEntry.id))
                  .filter(JournalEntry.status == "draft").scalar() or 0,
        "reversed": db.session.query(func.count(JournalEntry.id))
                    .filter(JournalEntry.status == "reversed").scalar() or 0,
        "lines": db.session.query(func.count(JournalLine.id)).scalar() or 0,
        "adjustments": db.session.query(func.count(Adjustment.id)).scalar() or 0,
        "closings": db.session.query(func.count(ClosingEntry.id)).scalar() or 0,
        "products": db.session.query(func.count(Product.id)).scalar() or 0,
        "budgets": db.session.query(func.count(Budget.id)).scalar() or 0,
        "periods": db.session.query(func.count(AccountingPeriod.id)).scalar() or 0,
        "accounts": db.session.query(func.count(Account.id)).scalar() or 0,
        "events": db.session.query(func.count(AuditLog.id)).scalar() or 0,
    }
    first = db.session.query(func.min(JournalEntry.entry_date)).scalar()
    last = db.session.query(func.max(JournalEntry.entry_date)).scalar()
    counts["first_entry"] = first
    counts["last_entry"] = last
    return counts


DEMO_NAMES = {"demo - sample traders", "demo sample traders"}


def _is_demo(business, owner):
    """A business is treated as demo / placeholder when it is the seeded
    sample business, has no name, or belongs to the seeded demo user."""
    name = (business.name or "").strip().lower()
    if name in DEMO_NAMES:
        return True
    if not name:
        return True
    if owner is not None and (owner.email or "").lower().startswith("demo@"):
        return True
    return False


def _business_rows():
    """One row per business, with the numbers taken straight from the books."""
    businesses = Business.query.order_by(Business.name).all()
    voucher_counts = dict(db.session.query(JournalEntry.business_id,
                                           func.count(JournalEntry.id))
                          .group_by(JournalEntry.business_id).all())
    adj_counts = dict(db.session.query(Adjustment.business_id,
                                       func.count(Adjustment.id))
                      .group_by(Adjustment.business_id).all())
    close_counts = dict(db.session.query(ClosingEntry.business_id,
                                         func.count(ClosingEntry.id))
                        .group_by(ClosingEntry.business_id).all())
    last_dates = dict(db.session.query(JournalEntry.business_id,
                                       func.max(JournalEntry.entry_date))
                      .group_by(JournalEntry.business_id).all())
    owners = {u.id: u for u in User.query.all()}
    users_by_business = {b.id: owners.get(b.user_id) for b in businesses}

    rows = []
    for b in businesses:
        period = b.active_period()
        owner = users_by_business.get(b.id)
        row = {
            "business": b,
            "owner": owner,
            "vouchers": voucher_counts.get(b.id, 0),
            "adjustments": adj_counts.get(b.id, 0),
            "closings": close_counts.get(b.id, 0),
            "last_entry": last_dates.get(b.id),
            "period": period,
            "balanced": None,
            "demo": _is_demo(b, owner),
        }
        rows.append(row)
    return rows


def _contact_messages(limit=None):
    """Messages sent through the public contact form (stored in the audit log)."""
    rows = (AuditLog.query.filter(AuditLog.action == "Contact message")
            .order_by(AuditLog.created_at.desc()))
    if limit:
        rows = rows.limit(limit)
    out = []
    for r in rows.all():
        try:
            data = json.loads(r.new_values or "{}")
        except Exception:
            data = {"message": (r.new_values or "")[:400]}
        out.append({"id": r.id, "at": r.created_at, "ip": r.ip_address,
                    "name": data.get("name", "—"), "email": data.get("email", "—"),
                    "subject": data.get("subject") or "(no subject)",
                    "message": data.get("message", "")})
    return out


def _system_feed(limit=14):
    """What just happened in the whole system - logins, sign-ups, messages."""
    query = (AuditLog.query
             .filter(AuditLog.action.in_(["User login", "User registered",
                                          "Contact message", "Transaction posted",
                                          "Report exported"]))
             .order_by(AuditLog.created_at.desc()).limit(limit))
    users = {u.id: u for u in User.query.all()}
    out = []
    for e in query.all():
        who = users.get(e.user_id)
        detail = ""
        if e.action == "Contact message":
            try:
                d = json.loads(e.new_values or "{}")
                detail = f"{d.get('name','—')} · {d.get('subject') or d.get('message','')[:60]}"
            except Exception:
                detail = (e.new_values or "")[:70]
        elif who is not None:
            detail = who.email
        else:
            detail = e.ip_address or "—"
        out.append({"action": e.action, "at": e.created_at, "detail": detail,
                    "business_id": e.business_id, "user": who})
    return out


def _check_books(business_id, start=None, end=None):
    """Trial-balance check used by the admin panel (never edits anything)."""
    try:
        tb = trial_balance(business_id, start, end)
        return {"debit": tb["total_debit"], "credit": tb["total_credit"],
                "difference": tb["difference"], "balanced": tb["is_balanced"]}
    except Exception:                                    # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def register_admin(app):

    @app.context_processor
    def admin_context():
        email, _, name = _admin_credentials(app)
        return {"admin_email": email, "admin_name": name,
                "admin_signed_in": bool(session.get("admin_ok"))}

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        email, password, name = _admin_credentials(app)
        if session.get("admin_ok"):
            return redirect(url_for("admin_dashboard"))
        error = None
        if request.method == "POST":
            got_email = (request.form.get("email") or "").strip().lower()
            got_password = request.form.get("password") or ""
            if (secrets.compare_digest(got_email, email)
                    and secrets.compare_digest(got_password, password)):
                session.clear()
                session["admin_ok"] = True
                session["admin_email"] = email
                session["_csrf"] = secrets.token_urlsafe(32)
                session.permanent = True
                _audit_admin("login", {"email": email, "ip": request.remote_addr})
                nxt = request.args.get("next") or url_for("admin_dashboard")
                if not nxt.startswith("/admin"):
                    nxt = url_for("admin_dashboard")
                return redirect(nxt)
            error = ("Email or password is incorrect. The administrator sign-in is "
                     "separate from the business login.")
            _audit_admin("failed login attempt", {"email": got_email,
                                                  "ip": request.remote_addr})
        return render_template("admin/login.html", error=error)

    @app.route("/admin/logout", methods=["POST", "GET"])
    def admin_logout():
        if session.get("admin_ok"):
            _audit_admin("logout", {"email": session.get("admin_email")})
        session.pop("admin_ok", None)
        session.pop("admin_email", None)
        flash("Signed out of the administrator panel.", "success")
        return redirect(url_for("admin_login"))

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = _overview()
        all_rows = _business_rows()
        rows = [r for r in all_rows if not r["demo"]]
        for row in rows:
            row["books"] = _check_books(row["business"].id)
        stats["demo_hidden"] = len(all_rows) - len(rows)
        stats["contact_messages"] = (db.session.query(func.count(AuditLog.id))
                                     .filter(AuditLog.action == "Contact message")
                                     .scalar() or 0)

        # "new since your last visit" counter for the nav badge
        seen = session.get("admin_seen_at")
        if seen:
            try:
                since = datetime.fromisoformat(seen)
            except ValueError:
                since = None
            if since:
                stats["new_events"] = (db.session.query(func.count(AuditLog.id))
                                       .filter(AuditLog.created_at > since).scalar() or 0)

        feed = _system_feed()
        messages = _contact_messages(limit=5)
        recent_events = (AuditLog.query.order_by(AuditLog.created_at.desc())
                         .limit(12).all())
        recent_vouchers = (JournalEntry.query.order_by(JournalEntry.created_at.desc())
                           .limit(8).all())
        owners = {u.id: u for u in User.query.all()}
        template = render_template("admin/dashboard.html", stats=stats, rows=rows,
                                   recent_events=recent_events, feed=feed,
                                   messages=messages,
                                   recent_vouchers=recent_vouchers, owners=owners,
                                   db_size=_db_size(),
                                   now=datetime.utcnow(),
                                   today=date.today())
        session["admin_seen_at"] = datetime.utcnow().isoformat()
        return template

    @app.route("/admin/messages")
    @admin_required
    def admin_messages():
        return render_template("admin/messages.html",
                               messages=_contact_messages())

    @app.route("/admin/businesses")
    @admin_required
    def admin_businesses():
        q = (request.args.get("q") or "").strip()
        query = Business.query
        if q:
            query = query.filter(db.or_(Business.name.ilike(f"%{q}%"),
                                        Business.owner_name.ilike(f"%{q}%"),
                                        Business.business_type.ilike(f"%{q}%")))
        businesses = query.order_by(Business.name).all()
        rows = _business_rows()
        show_empty = request.args.get("empty") == "1"
        rows = [r for r in rows if not r["demo"]]
        if not show_empty:
            rows = [r for r in rows if (r["business"].name or "").strip()]
        if q:
            keep = {b.id for b in businesses}
            rows = [r for r in rows if r["business"].id in keep]
        for row in rows:
            row["books"] = _check_books(row["business"].id)
        return render_template("admin/businesses.html", rows=rows, q=q,
                               show_empty=show_empty)

    @app.route("/admin/business/<int:business_id>")
    @admin_required
    def admin_business_detail(business_id):
        business = db.session.get(Business, business_id)
        if business is None:
            flash("That business no longer exists.", "warning")
            return redirect(url_for("admin_businesses"))
        vouchers = (JournalEntry.query.filter_by(business_id=business.id)
                    .order_by(JournalEntry.entry_date.desc(),
                              JournalEntry.id.desc()).limit(15).all())
        adjustments = (Adjustment.query.filter_by(business_id=business.id)
                       .order_by(Adjustment.adj_date.desc()).limit(10).all())
        closings = (ClosingEntry.query.filter_by(business_id=business.id)
                    .order_by(ClosingEntry.entry_date.desc()).all())
        periods = (AccountingPeriod.query.filter_by(business_id=business.id)
                   .order_by(AccountingPeriod.start_date).all())
        start = min([v.entry_date for v in vouchers], default=None) or business.fy_start
        end = max([v.entry_date for v in vouchers], default=None) or business.fy_end
        books = _check_books(business.id, start, end)
        adjusted = adjusted_trial_balance(business.id, start, end)
        post_closing = post_closing_trial_balance(business.id, start, end)
        pl = profit_and_loss(business.id, start, end)
        owner = db.session.get(User, business.user_id) if business.user_id else None
        return render_template("admin/business_detail.html", business=business,
                               owner=owner, vouchers=vouchers,
                               adjustments=adjustments, closings=closings,
                               periods=periods, start=start, end=end,
                               books=books, adjusted=adjusted,
                               post_closing=post_closing, pl=pl)

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        q = (request.args.get("q") or "").strip()
        query = User.query
        if q:
            query = query.filter(db.or_(User.name.ilike(f"%{q}%"),
                                        User.email.ilike(f"%{q}%")))
        users = query.order_by(User.created_at.desc()).all()
        business_counts = dict(db.session.query(Business.user_id,
                                                func.count(Business.id))
                               .group_by(Business.user_id).all())
        voucher_counts = dict(db.session.query(JournalEntry.created_by,
                                               func.count(JournalEntry.id))
                              .group_by(JournalEntry.created_by).all())
        return render_template("admin/users.html", users=users, q=q,
                               business_counts=business_counts,
                               voucher_counts=voucher_counts)

    @app.route("/admin/activity")
    @admin_required
    def admin_activity():
        page = max(1, int(request.args.get("page", 1) or 1))
        action = (request.args.get("action") or "").strip()
        query = AuditLog.query
        if action:
            query = query.filter(AuditLog.action.ilike(f"%{action}%"))
        total = query.count()
        events = (query.order_by(AuditLog.created_at.desc())
                  .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        users = {u.id: u for u in User.query.all()}
        businesses = {b.id: b for b in Business.query.all()}
        return render_template("admin/activity.html", events=events, total=total,
                               page=page, pages=pages, action=action,
                               users=users, businesses=businesses)

    @app.route("/admin/reports")
    @admin_required
    def admin_reports():
        """Cross-business view: what every set of books adds up to."""
        rows = []
        owners = {u.id: u for u in User.query.all()}
        for business in Business.query.order_by(Business.name).all():
            if _is_demo(business, owners.get(business.user_id)):
                continue
            tb = _check_books(business.id)
            pl = profit_and_loss(business.id)
            rows.append({"business": business, "books": tb,
                         "net_profit": pl.get("net_profit"),
                         "net_loss": pl.get("net_loss"),
                         "gross_profit": pl.get("gross_profit"),
                         "has_activity": pl.get("has_activity")})
        return render_template("admin/reports.html", rows=rows)
