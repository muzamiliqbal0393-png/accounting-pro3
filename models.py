"""
Database models for ACCOUNTING PRO.

Money is stored as INTEGER paisa (x100) through the ``Money`` type decorator so
that no floating point arithmetic ever touches a financial figure.  Reading a
column returns a ``Decimal`` quantised to two places.
"""
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import (Integer, String, Text, Date, DateTime, Boolean,
                        ForeignKey, UniqueConstraint, Index, TypeDecorator)
from sqlalchemy.orm import relationship
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    """Quantise any numeric input to 2dp Decimal (banker-safe half-up)."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(TWO, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)


class Money(TypeDecorator):
    """Exact decimal storage: INTEGER paisa in, ``Decimal`` out."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int((money(value) * 100).to_integral_value())

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / Decimal(100)).quantize(TWO)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
CATEGORIES = ["Assets", "Liabilities", "Equity", "Revenue", "Expenses"]

NORMAL_BALANCE = {
    "Assets": "Debit",
    "Expenses": "Debit",
    "Liabilities": "Credit",
    "Equity": "Credit",
    "Revenue": "Credit",
}

#: subcategory key -> (label, parent category)
SUBCATEGORIES = {
    # Assets
    "cash": ("Cash in Hand", "Assets"),
    "bank": ("Bank", "Assets"),
    "accounts_receivable": ("Accounts Receivable / Debtors", "Assets"),
    "inventory": ("Closing Stock / Inventory", "Assets"),
    "opening_stock": ("Opening Stock (Trading)", "Assets"),
    "fixed_asset": ("Fixed Assets", "Assets"),
    "other_asset": ("Other Assets", "Assets"),
    # Liabilities
    "accounts_payable": ("Accounts Payable / Creditors", "Liabilities"),
    "outstanding_expenses": ("Outstanding Expenses", "Liabilities"),
    "loan": ("Loans", "Liabilities"),
    "other_liability": ("Other Liabilities", "Liabilities"),
    # Equity
    "capital": ("Owner's Capital", "Equity"),
    "drawings": ("Drawings", "Equity"),
    "retained_earnings": ("Retained Earnings", "Equity"),
    # Revenue
    "sales": ("Sales", "Revenue"),
    "sales_returns": ("Sales Returns", "Revenue"),
    "other_income": ("Other Income", "Revenue"),
    "trading_contra": ("Trading A/c (contra)", "Revenue"),
    # Expenses
    "purchases": ("Purchases", "Expenses"),
    "purchase_returns": ("Purchase Returns", "Expenses"),
    "direct_expenses": ("Direct Expenses", "Expenses"),
    "indirect_expenses": ("Indirect Expenses", "Expenses"),
    "depreciation": ("Depreciation", "Expenses"),
    "bad_debts": ("Bad Debts", "Expenses"),
}

SUBCATEGORY_CHOICES = {cat: [(k, v[0]) for k, v in SUBCATEGORIES.items() if v[1] == cat]
                       for cat in CATEGORIES}

ACCOUNTING_METHODS = ["Accrual", "Cash"]
PERIOD_KINDS = ["Monthly", "Quarterly", "Half-Yearly", "Yearly", "Custom"]
ENTRY_TYPES = ["Cash Receipt", "Cash Payment", "Bank Receipt", "Bank Payment",
               "Credit Sale", "Credit Purchase", "Cash Sale", "Cash Purchase",
               "Expense", "Capital", "Drawings", "General Journal", "Reversal",
               "Opening Balance", "Other"]
#: Every adjustment type required by the project brief (p.96 of the project
#: handout) is listed here.
ADJUSTMENT_KINDS = ["Closing Stock", "Outstanding Expenses", "Outstanding Income",
                    "Prepaid Expenses", "Income Received in Advance", "Depreciation",
                    "Bad Debts", "Provision for Doubtful Debts", "Interest on Capital",
                    "Interest on Drawings", "Other"]

#: Older databases may hold the previous wording; map it so nothing breaks.
ADJUSTMENT_KIND_ALIASES = {"Accrued Income": "Outstanding Income"}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(120), nullable=False)
    email = db.Column(String(190), nullable=False, unique=True, index=True)
    password_hash = db.Column(String(255), nullable=False)
    is_active = db.Column(Boolean, default=True, nullable=False)
    reset_token = db.Column(String(120), index=True)
    reset_expires = db.Column(DateTime)
    last_login = db.Column(DateTime)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    businesses = relationship("Business", back_populates="user",
                              cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="user", uselist=False,
                            cascade="all, delete-orphan")

    # -- password helpers ------------------------------------------------
    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256:600000")

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw or "")

    def __repr__(self):
        return f"<User {self.email}>"


class UserSettings(db.Model):
    __tablename__ = "user_settings"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        unique=True, nullable=False)
    currency = db.Column(String(8), default="PKR", nullable=False)
    currency_symbol = db.Column(String(8), default="Rs.", nullable=False)
    theme = db.Column(String(20), default="light", nullable=False)
    invoice_prefix = db.Column(String(20), default="INV-")
    invoice_terms = db.Column(Text)
    report_footer = db.Column(Text)
    show_decimals = db.Column(Boolean, default=True)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="settings")


# ---------------------------------------------------------------------------
# Business & periods
# ---------------------------------------------------------------------------
class Business(db.Model):
    __tablename__ = "businesses"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name = db.Column(String(190), nullable=False)
    logo = db.Column(String(255))
    owner_name = db.Column(String(150))
    address = db.Column(Text)
    phone = db.Column(String(50))
    email = db.Column(String(190))
    business_type = db.Column(String(100))
    currency = db.Column(String(8), default="PKR", nullable=False)
    fy_start = db.Column(Date, default=lambda: date(2026, 1, 1), nullable=False)
    fy_end = db.Column(Date, default=lambda: date(2026, 3, 31), nullable=False)
    accounting_method = db.Column(String(20), default="Accrual", nullable=False)
    description = db.Column(Text)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="businesses")
    periods = relationship("AccountingPeriod", back_populates="business",
                           cascade="all, delete-orphan")
    accounts = relationship("Account", back_populates="business",
                            cascade="all, delete-orphan")

    def active_period(self):
        for p in self.periods:
            if p.is_active:
                return p
        return self.periods[0] if self.periods else None


class AccountingPeriod(db.Model):
    __tablename__ = "accounting_periods"
    __table_args__ = (UniqueConstraint("business_id", "label", name="uq_period_label"),)

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    label = db.Column(String(120), nullable=False)
    kind = db.Column(String(20), default="Yearly", nullable=False)
    start_date = db.Column(Date, nullable=False)
    end_date = db.Column(Date, nullable=False)
    is_active = db.Column(Boolean, default=True, nullable=False)
    is_closed = db.Column(Boolean, default=False, nullable=False)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    business = relationship("Business", back_populates="periods")

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------
class Account(db.Model):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_account_code"),
        Index("ix_account_business_cat", "business_id", "category"),
    )

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    code = db.Column(String(20), nullable=False)
    name = db.Column(String(150), nullable=False)
    category = db.Column(String(30), nullable=False)
    subcategory = db.Column(String(40), nullable=False)
    normal_balance = db.Column(String(10), nullable=False, default="Debit")
    opening_debit = db.Column(Money, default=ZERO, nullable=False)
    opening_credit = db.Column(Money, default=ZERO, nullable=False)
    description = db.Column(Text)
    is_active = db.Column(Boolean, default=True, nullable=False)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    business = relationship("Business", back_populates="accounts")
    lines = relationship("JournalLine", back_populates="account")

    @property
    def subcategory_label(self):
        return SUBCATEGORIES.get(self.subcategory, (self.subcategory, self.category))[0]

    @property
    def opening_net(self):
        """Signed opening balance (debit positive)."""
        return money(self.opening_debit) - money(self.opening_credit)

    @property
    def display(self):
        return f"{self.code} - {self.name}"

    @property
    def is_temporary(self):
        return self.category in ("Revenue", "Expenses")

    @property
    def is_permanent(self):
        return not self.is_temporary


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------
ACTIVE_ENTRY_STATUS = ("posted", "reversed")


class JournalEntry(db.Model):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("business_id", "voucher_no", name="uq_voucher_no"),
        Index("ix_entry_business_date", "business_id", "entry_date"),
    )

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    period_id = db.Column(Integer, ForeignKey("accounting_periods.id", ondelete="SET NULL"))
    voucher_no = db.Column(String(40), nullable=False)
    entry_date = db.Column(Date, nullable=False)
    entry_type = db.Column(String(40), default="General Journal", nullable=False)
    description = db.Column(String(255))
    narration = db.Column(Text)
    reference = db.Column(String(80))
    attachment = db.Column(String(255))
    status = db.Column(String(20), default="draft", nullable=False, index=True)
    reversal_of_id = db.Column(Integer, ForeignKey("journal_entries.id", ondelete="SET NULL"))
    total_debit = db.Column(Money, default=ZERO, nullable=False)
    total_credit = db.Column(Money, default=ZERO, nullable=False)
    created_by = db.Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    posted_at = db.Column(DateTime)

    lines = relationship("JournalLine", back_populates="entry",
                         cascade="all, delete-orphan", order_by="JournalLine.id")
    reversal_of = relationship("JournalEntry", remote_side=[id], foreign_keys=[reversal_of_id])

    @property
    def is_balanced(self):
        return money(self.total_debit) == money(self.total_credit)

    @property
    def difference(self):
        return money(self.total_debit) - money(self.total_credit)

    @property
    def is_posted(self):
        return self.status in ACTIVE_ENTRY_STATUS


class JournalLine(db.Model):
    __tablename__ = "journal_lines"

    id = db.Column(Integer, primary_key=True)
    entry_id = db.Column(Integer, ForeignKey("journal_entries.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    account_id = db.Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    debit = db.Column(Money, default=ZERO, nullable=False)
    credit = db.Column(Money, default=ZERO, nullable=False)
    description = db.Column(String(255))

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account", back_populates="lines")


# ---------------------------------------------------------------------------
# Adjusting entries
# ---------------------------------------------------------------------------
class Adjustment(db.Model):
    __tablename__ = "adjustments"
    __table_args__ = (UniqueConstraint("business_id", "adj_no", name="uq_adj_no"),)

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    period_id = db.Column(Integer, ForeignKey("accounting_periods.id", ondelete="SET NULL"))
    adj_no = db.Column(String(40), nullable=False)
    adj_date = db.Column(Date, nullable=False)
    kind = db.Column(String(50), default="Other", nullable=False)
    description = db.Column(String(255))
    narration = db.Column(Text)
    status = db.Column(String(20), default="draft", nullable=False)
    total_debit = db.Column(Money, default=ZERO, nullable=False)
    total_credit = db.Column(Money, default=ZERO, nullable=False)
    created_by = db.Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    lines = relationship("AdjustmentLine", back_populates="adjustment",
                         cascade="all, delete-orphan", order_by="AdjustmentLine.id")

    @property
    def is_balanced(self):
        return money(self.total_debit) == money(self.total_credit)

    @property
    def difference(self):
        return money(self.total_debit) - money(self.total_credit)


class AdjustmentLine(db.Model):
    __tablename__ = "adjustment_lines"

    id = db.Column(Integer, primary_key=True)
    adjustment_id = db.Column(Integer, ForeignKey("adjustments.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    account_id = db.Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    debit = db.Column(Money, default=ZERO, nullable=False)
    credit = db.Column(Money, default=ZERO, nullable=False)
    description = db.Column(String(255))

    adjustment = relationship("Adjustment", back_populates="lines")
    account = relationship("Account")


# ---------------------------------------------------------------------------
# Closing entries
# ---------------------------------------------------------------------------
class ClosingEntry(db.Model):
    __tablename__ = "closing_entries"

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    period_id = db.Column(Integer, ForeignKey("accounting_periods.id", ondelete="SET NULL"))
    entry_no = db.Column(String(40), nullable=False)
    entry_date = db.Column(Date, nullable=False)
    description = db.Column(String(255))
    status = db.Column(String(20), default="draft", nullable=False)
    total_debit = db.Column(Money, default=ZERO, nullable=False)
    total_credit = db.Column(Money, default=ZERO, nullable=False)
    net_result = db.Column(Money, default=ZERO, nullable=False)
    created_by = db.Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)
    posted_at = db.Column(DateTime)

    lines = relationship("ClosingLine", back_populates="closing",
                         cascade="all, delete-orphan", order_by="ClosingLine.id")

    @property
    def is_balanced(self):
        return money(self.total_debit) == money(self.total_credit)

    @property
    def difference(self):
        return money(self.total_debit) - money(self.total_credit)


class ClosingLine(db.Model):
    __tablename__ = "closing_lines"

    id = db.Column(Integer, primary_key=True)
    closing_id = db.Column(Integer, ForeignKey("closing_entries.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    account_id = db.Column(Integer, ForeignKey("accounts.id", ondelete="RESTRICT"),
                           nullable=False, index=True)
    debit = db.Column(Money, default=ZERO, nullable=False)
    credit = db.Column(Money, default=ZERO, nullable=False)
    description = db.Column(String(255))

    closing = relationship("ClosingEntry", back_populates="lines")
    account = relationship("Account")


# ---------------------------------------------------------------------------
# Inventory / products
# ---------------------------------------------------------------------------
class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("business_id", "code", name="uq_product_code"),)

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    code = db.Column(String(40), nullable=False)
    name = db.Column(String(150), nullable=False)
    category = db.Column(String(80))
    purchase_cost = db.Column(Money, default=ZERO, nullable=False)
    selling_price = db.Column(Money, default=ZERO, nullable=False)
    opening_qty = db.Column(Integer, default=0, nullable=False)
    purchase_qty = db.Column(Integer, default=0, nullable=False)
    sold_qty = db.Column(Integer, default=0, nullable=False)
    sales_returns = db.Column(Integer, default=0, nullable=False)
    purchase_returns = db.Column(Integer, default=0, nullable=False)
    discount_percent = db.Column(db.Float, default=0.0)
    reorder_level = db.Column(Integer, default=0)
    is_active = db.Column(Boolean, default=True, nullable=False)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def available_stock(self):
        return (self.opening_qty + self.purchase_qty - self.sold_qty
                + self.sales_returns - self.purchase_returns)

    @property
    def stock_value(self):
        return money(money(self.purchase_cost) * Decimal(self.available_stock))

    @property
    def net_sold(self):
        return self.sold_qty - self.sales_returns

    @property
    def revenue(self):
        return money(money(self.selling_price) * Decimal(self.net_sold))

    @property
    def cogs(self):
        return money(money(self.purchase_cost) * Decimal(self.net_sold))

    @property
    def gross_profit(self):
        return self.revenue - self.cogs

    @property
    def margin(self):
        if self.revenue == 0:
            return ZERO
        return (self.gross_profit / self.revenue * 100).quantize(TWO)

    @property
    def is_low_stock(self):
        return self.available_stock <= (self.reorder_level or 0)


class InventoryTransaction(db.Model):
    __tablename__ = "inventory_transactions"

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    product_id = db.Column(Integer, ForeignKey("products.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    txn_date = db.Column(Date, nullable=False)
    txn_type = db.Column(String(30), nullable=False)  # Opening / Purchase / Sale / Return
    quantity = db.Column(Integer, nullable=False, default=0)
    rate = db.Column(Money, default=ZERO, nullable=False)
    amount = db.Column(Money, default=ZERO, nullable=False)
    note = db.Column(String(255))
    entry_id = db.Column(Integer, ForeignKey("journal_entries.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product")


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    sale_date = db.Column(Date, nullable=False)
    invoice_no = db.Column(String(40))
    customer = db.Column(String(150))
    product_id = db.Column(Integer, ForeignKey("products.id", ondelete="SET NULL"))
    quantity = db.Column(Integer, default=0, nullable=False)
    rate = db.Column(Money, default=ZERO, nullable=False)
    discount = db.Column(Money, default=ZERO, nullable=False)
    amount = db.Column(Money, default=ZERO, nullable=False)
    entry_id = db.Column(Integer, ForeignKey("journal_entries.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product")


class Purchase(db.Model):
    __tablename__ = "purchases"

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    purchase_date = db.Column(Date, nullable=False)
    bill_no = db.Column(String(40))
    supplier = db.Column(String(150))
    product_id = db.Column(Integer, ForeignKey("products.id", ondelete="SET NULL"))
    quantity = db.Column(Integer, default=0, nullable=False)
    rate = db.Column(Money, default=ZERO, nullable=False)
    amount = db.Column(Money, default=ZERO, nullable=False)
    entry_id = db.Column(Integer, ForeignKey("journal_entries.id", ondelete="SET NULL"))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("Product")


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(Integer, primary_key=True)
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    name = db.Column(String(150), nullable=False)
    kind = db.Column(String(20), default="Monthly", nullable=False)
    start_date = db.Column(Date, nullable=False)
    end_date = db.Column(Date, nullable=False)
    planned_income = db.Column(Money, default=ZERO, nullable=False)
    planned_expenses = db.Column(Money, default=ZERO, nullable=False)
    notes = db.Column(Text)
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_user_time", "user_id", "created_at"),)

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    business_id = db.Column(Integer, ForeignKey("businesses.id", ondelete="SET NULL"))
    action = db.Column(String(60), nullable=False)
    record_type = db.Column(String(60))
    record_id = db.Column(Integer)
    previous_values = db.Column(Text)
    new_values = db.Column(Text)
    ip_address = db.Column(String(60))
    created_at = db.Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
