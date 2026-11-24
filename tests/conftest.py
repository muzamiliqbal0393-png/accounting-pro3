"""Pytest fixtures for ACCOUNTING PRO."""
import os
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from config import TestConfig  # noqa: E402
from extensions import db  # noqa: E402

#: File-backed database so backup / restore can be exercised for real.
TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "accounting_pro_test.db")


class TestDBConfig(TestConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + TEST_DB_PATH
    SECRET_KEY = "test-secret"

DEFAULT_ACCOUNTS = [
    ("1000", "Cash in Hand", "Assets", "cash"),
    ("1010", "Bank Account", "Assets", "bank"),
    ("1020", "Accounts Receivable", "Assets", "accounts_receivable"),
    ("1030", "Closing Stock", "Assets", "inventory"),
    ("1040", "Opening Stock", "Assets", "opening_stock"),
    ("1050", "Furniture", "Assets", "fixed_asset"),
    ("2000", "Accounts Payable", "Liabilities", "accounts_payable"),
    ("2020", "Bank Loan", "Liabilities", "loan"),
    ("3000", "Owner's Capital", "Equity", "capital"),
    ("3010", "Drawings", "Equity", "drawings"),
    ("4000", "Sales", "Revenue", "sales"),
    ("4010", "Sales Returns", "Revenue", "sales_returns"),
    ("4020", "Other Income", "Revenue", "other_income"),
    ("4030", "Trading A/c (Closing Stock Contra)", "Revenue", "trading_contra"),
    ("5000", "Purchases", "Expenses", "purchases"),
    ("5020", "Carriage Inward", "Expenses", "direct_expenses"),
    ("5050", "Rent", "Expenses", "indirect_expenses"),
    ("5080", "Depreciation", "Expenses", "depreciation"),
]


@pytest.fixture()
def app():
    application = create_app(TestDBConfig)
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                              SERVER_NAME="localhost")
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, name="Test User", email="test@example.com", password="Password123"):
    return client.post("/register", data={"name": name, "email": email,
                                          "password": password,
                                          "confirm_password": password},
                       follow_redirects=True)


def setup_business(client, name="Test Traders"):
    return client.post("/business", data={
        "name": name, "owner_name": "Test Owner", "phone": "0300-0000000",
        "email": "biz@example.com", "business_type": "Retail",
        "currency": "PKR", "accounting_method": "Accrual",
        "fy_start": "2026-01-01", "fy_end": "2026-03-31",
        "address": "Lahore", "description": "Test business",
    }, follow_redirects=True)


def load_accounts(app):
    """Insert the test chart of accounts (mirrors the standard chart).

    Returns ``{code: account_id}`` so callers can use the ids outside the
    application context without detached-instance errors.
    """
    from models import Account, Business, NORMAL_BALANCE
    business = Business.query.order_by(Business.id).first()
    accounts = {}
    for code, name, category, sub in DEFAULT_ACCOUNTS:
        acc = Account(business_id=business.id, code=code, name=name, category=category,
                      subcategory=sub, normal_balance=NORMAL_BALANCE[category])
        db.session.add(acc)
        db.session.flush()
        accounts[code] = acc.id
    db.session.commit()
    return accounts


def post_entry(app, business_id, user_id, voucher_no, lines, entry_date="2026-01-15",
               entry_type="General Journal", description="Test voucher"):
    """Post a balanced journal entry through the accounting engine."""
    from accounting.journal import create_entry
    raw = [{"account_id": str(acc_id), "debit": dr, "credit": cr,
            "description": note} for acc_id, dr, cr, note in lines]
    return create_entry(business_id, user_id, {
        "voucher_no": voucher_no, "entry_date": entry_date,
        "entry_type": entry_type, "description": description,
        "narration": "fixture"},
        raw, status="posted")


@pytest.fixture()
def seeded(app, client):
    """A logged-in user with a business profile, chart of accounts and vouchers."""
    from models import Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        cash = accounts["1000"]
        capital = accounts["3000"]
        sales = accounts["4000"]
        purchases = accounts["5000"]
        rent = accounts["5050"]
        stock = accounts["1030"]
        contra = accounts["4030"]

        post_entry(app, business.id, user.id, "JV-0001",
                   [(cash, "500000", "0", "Capital introduced"),
                    (capital, "0", "500000", "Capital introduced")],
                   entry_date="2026-01-02", entry_type="Capital",
                   description="Owner introduced capital")
        post_entry(app, business.id, user.id, "JV-0002",
                   [(purchases, "100000", "0", "Goods purchased"),
                    (cash, "0", "100000", "Goods purchased")],
                   entry_date="2026-01-10", entry_type="Cash Purchase",
                   description="Cash purchases")
        post_entry(app, business.id, user.id, "JV-0003",
                   [(cash, "250000", "0", "Cash sales"),
                    (sales, "0", "250000", "Cash sales")],
                   entry_date="2026-02-05", entry_type="Cash Sale",
                   description="Cash sales for February")
        post_entry(app, business.id, user.id, "JV-0004",
                   [(rent, "20000", "0", "Shop rent"),
                    (cash, "0", "20000", "Shop rent")],
                   entry_date="2026-02-10", entry_type="Expense",
                   description="Shop rent for February")

        from accounting.adjustments import create_adjustment
        create_adjustment(business.id, user.id,
                          {"adj_no": "ADJ-0001", "adj_date": "2026-03-31",
                           "kind": "Closing Stock",
                           "description": "Closing stock as at 31 March 2026",
                           "narration": "Counted stock"},
                          [{"account_id": str(stock), "debit": "30000", "credit": "0",
                            "description": "Closing stock"},
                           {"account_id": str(contra), "debit": "0", "credit": "30000",
                            "description": "To trading account"}],
                          status="posted")
        business_id, user_id = business.id, user.id
    return {"accounts": accounts, "business_id": business_id, "user_id": user_id,
            "client": client}
