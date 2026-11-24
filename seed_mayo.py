"""
Mayo Stationery Store — the project's real dataset (24 business transactions).

    accounting period : 01 January 2026 - 31 March 2026
    currency          : PKR (Rs.)
    login             : mayo@accountingpro.local / Mayo12345

Every figure in the application (journal, ledger, trial balance, adjusted trial
balance, trading account, profit & loss, balance sheet, closing entries,
post-closing trial balance, cash flow, analysis, inventory) is produced by the
accounting engine from these 24 vouchers + the closing-stock adjustment.
There is no hardcoded number anywhere in the templates.

Run once:      python seed_mayo.py
Re-run:        nothing happens (it refuses to duplicate the 24 transactions).
Force rebuild: python seed_mayo.py --reset      (drops the whole database file)
"""
import sys
from datetime import date
from decimal import Decimal

from app import create_app, DEFAULT_CHART
from extensions import db
from models import (Account, AccountingPeriod, Business, NORMAL_BALANCE, Product,
                    User, UserSettings, money)
from accounting.journal import JournalError, create_entry
from accounting.adjustments import create_adjustment

BUSINESS_NAME = "Mayo Stationery Store"
USER_EMAIL = "mayo@accountingpro.local"
USER_PASSWORD = "Mayo12345"

PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 3, 31)

# ---------------------------------------------------------------------------
# The 24 business transactions of Mayo Stationery Store
# (voucher no, date, entry type, description, [(account code, debit, credit)])
# ---------------------------------------------------------------------------
TRANSACTIONS = [
    ("JV-0001", "2026-01-01", "Capital", "Capital introduced by owner in cash",
     [("1000", "700000", "0"), ("3000", "0", "700000")]),

    ("JV-0002", "2026-01-02", "Cash Payment", "Shop furniture & fixtures purchased for cash",
     [("1050", "224000", "0"), ("1000", "0", "224000")]),

    ("JV-0003", "2026-01-05", "Credit Purchase", "Stationery stock purchased on credit - Karachi Paper House",
     [("5000", "400000", "0"), ("2000", "0", "400000")]),

    ("JV-0004", "2026-01-08", "Cash Purchase", "Registers and files purchased for cash",
     [("5000", "100000", "0"), ("1000", "0", "100000")]),

    ("JV-0005", "2026-01-10", "Cash Sale", "Counter sales 01-10 January",
     [("1000", "150000", "0"), ("4000", "0", "150000")]),

    ("JV-0006", "2026-01-15", "Credit Sale", "School supplies sold on credit - City School",
     [("1020", "250000", "0"), ("4000", "0", "250000")]),

    ("JV-0007", "2026-01-20", "Cash Receipt", "Cash received from City School",
     [("1000", "220000", "0"), ("1020", "0", "220000")]),

    ("JV-0008", "2026-01-25", "Cash Payment", "Payment to Karachi Paper House",
     [("2000", "200000", "0"), ("1000", "0", "200000")]),

    ("JV-0009", "2026-02-01", "Expense", "Salaries paid for January",
     [("5040", "60000", "0"), ("1000", "0", "60000")]),

    ("JV-0010", "2026-02-03", "Expense", "Shop rent for February",
     [("5050", "45000", "0"), ("1000", "0", "45000")]),

    ("JV-0011", "2026-02-05", "Expense", "Electricity bill for January",
     [("5060", "15000", "0"), ("1000", "0", "15000")]),

    ("JV-0012", "2026-02-10", "Cash Sale", "Counter sales 01-10 February",
     [("1000", "197000", "0"), ("4000", "0", "197000")]),

    ("JV-0013", "2026-02-14", "Credit Sale", "Office supplies sold on credit - Beacon House",
     [("1020", "180000", "0"), ("4000", "0", "180000")]),

    ("JV-0014", "2026-02-18", "Cash Receipt", "Cash received from Beacon House",
     [("1000", "180000", "0"), ("1020", "0", "180000")]),

    ("JV-0015", "2026-02-22", "Expense", "Advertising in local newspaper",
     [("5070", "10000", "0"), ("1000", "0", "10000")]),

    ("JV-0016", "2026-02-25", "Expense", "Carriage inward on stock purchased",
     [("5020", "8000", "0"), ("1000", "0", "8000")]),

    ("JV-0017", "2026-03-01", "Credit Purchase", "Stationery stock purchased on credit - Lahore Stationers",
     [("5000", "300000", "0"), ("2000", "0", "300000")]),

    ("JV-0018", "2026-03-05", "Cash Purchase", "Art paper and colours purchased for cash",
     [("5000", "60000", "0"), ("1000", "0", "60000")]),

    ("JV-0019", "2026-03-10", "Cash Sale", "Counter sales 01-10 March (admission season)",
     [("1000", "300000", "0"), ("4000", "0", "300000")]),

    ("JV-0020", "2026-03-15", "Cash Payment", "Payment to Lahore Stationers",
     [("2000", "300000", "0"), ("1000", "0", "300000")]),

    ("JV-0021", "2026-03-18", "Expense", "Salaries paid for February and March",
     [("5040", "60000", "0"), ("1000", "0", "60000")]),

    ("JV-0022", "2026-03-20", "Drawings", "Owner withdrew cash for personal use",
     [("3010", "30000", "0"), ("1000", "0", "30000")]),

    ("JV-0023", "2026-03-25", "Expense", "Wages paid to shop helper",
     [("5030", "25000", "0"), ("1000", "0", "25000")]),

    ("JV-0024", "2026-03-28", "Expense", "Utility bill for March",
     [("5060", "5000", "0"), ("1000", "0", "5000")]),
]

# closing stock adjustment (needed before the trading account can be prepared)
ADJUSTMENTS = [
    ("ADJ-0001", "2026-03-31", "Closing Stock",
     "Closing stock as at 31 March 2026 (physical count)",
     "Stationery stock in hand valued at cost",
     [("1030", "180000", "0"), ("4030", "0", "180000")]),
]

PRODUCTS = [
    ("SKU-001", "Registers (200 pages)", "Registers", "180", "260", 240, 40),
    ("SKU-002", "Ball pens (box of 50)", "Writing", "450", "650", 90, 20),
    ("SKU-003", "A4 reams", "Paper", "950", "1200", 150, 30),
    ("SKU-004", "Art colour sets", "Art", "320", "480", 60, 15),
    ("SKU-005", "School bags", "Bags", "1100", "1550", 35, 10),
]


def seed(reset=False):
    app = create_app()
    with app.app_context():
        existing = Business.query.filter_by(name=BUSINESS_NAME).first()
        if existing and not reset:
            print(f"'{BUSINESS_NAME}' is already seeded - nothing to do.")
            print(f"Login: {USER_EMAIL} / {USER_PASSWORD}")
            return

        if reset:
            # explicit, ordered delete (child rows first; works on SQLite and libSQL)
            from models import (AuditLog, Adjustment, AdjustmentLine, Budget, ClosingEntry,
                                ClosingLine, InventoryTransaction, JournalEntry, JournalLine,
                                Purchase, Sale)
            for model in (JournalLine, AdjustmentLine, ClosingLine, JournalEntry,
                          Adjustment, ClosingEntry, InventoryTransaction, Sale, Purchase,
                          Budget, AuditLog, Product, Account, AccountingPeriod, Business,
                          UserSettings, User):
                db.session.query(model).delete()
            db.session.commit()

        user = User.query.filter_by(email=USER_EMAIL).first()
        if user is None:
            user = User(name="Mayo Stationery Store", email=USER_EMAIL)
            user.set_password(USER_PASSWORD)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserSettings(user_id=user.id))
            db.session.commit()

        business = Business.query.filter_by(user_id=user.id, name=BUSINESS_NAME).first()
        if business is None:
            business = Business(
                user_id=user.id,
                name=BUSINESS_NAME,
                owner_name="Mayo Stationery Store",
                address="Main Bazaar, Mayo Road, Lahore, Pakistan",
                phone="+92 300 1234567",
                email=USER_EMAIL,
                business_type="Stationery Store",
                currency="PKR",
                fy_start=PERIOD_START,
                fy_end=PERIOD_END,
                accounting_method="Accrual",
                description="Stationery retail business - 24 business transactions for the "
                            "period 01 January 2026 to 31 March 2026.",
            )
            db.session.add(business)
            db.session.flush()
            db.session.add(AccountingPeriod(business_id=business.id,
                                            label="01 Jan 2026 - 31 Mar 2026",
                                            kind="Quarterly",
                                            start_date=PERIOD_START,
                                            end_date=PERIOD_END,
                                            is_active=True))
            db.session.commit()

        # ---- chart of accounts (the standard 32-account chart) ----------------
        acc = {}
        for code, name, category, sub in DEFAULT_CHART:
            account = Account.query.filter_by(business_id=business.id, code=code).first()
            if account is None:
                account = Account(business_id=business.id, code=code, name=name,
                                  category=category, subcategory=sub,
                                  normal_balance=NORMAL_BALANCE[category])
                db.session.add(account)
                db.session.flush()
            acc[code] = account.id
        db.session.commit()

        # ---- the 24 transactions ---------------------------------------------
        created = 0
        for no, dt, etype, desc, lines in TRANSACTIONS:
            payload = [{"account_id": str(acc[code]), "debit": dr, "credit": cr,
                        "description": desc} for code, dr, cr in lines]
            try:
                create_entry(business.id, user.id,
                             {"voucher_no": no, "entry_date": dt, "entry_type": etype,
                              "description": desc, "narration": desc},
                             payload, status="posted")
                created += 1
            except JournalError as exc:
                print(f"  skipped {no}: {exc}")

        # ---- closing stock adjustment ----------------------------------------
        for no, dt, kind, desc, narr, lines in ADJUSTMENTS:
            if Adjustment_exists(business.id, no):
                continue
            payload = [{"account_id": str(acc[code]), "debit": dr, "credit": cr,
                        "description": desc} for code, dr, cr in lines]
            try:
                create_adjustment(business.id, user.id,
                                  {"adj_no": no, "adj_date": dt, "kind": kind,
                                   "description": desc, "narration": narr},
                                  payload, status="posted")
            except JournalError as exc:
                print(f"  skipped {no}: {exc}")

        # ---- a small product list so the inventory module is not empty -------
        for code, name, cat, cost, price, qty, reorder in PRODUCTS:
            if Product.query.filter_by(business_id=business.id, code=code).first() is None:
                db.session.add(Product(business_id=business.id, code=code, name=name,
                                       category=cat, purchase_cost=money(cost),
                                       selling_price=money(price), opening_qty=qty,
                                       reorder_level=reorder))
        db.session.commit()

        print(f"Seeded '{BUSINESS_NAME}': {created} transactions, "
              f"{len(ADJUSTMENTS)} adjustment(s), {len(PRODUCTS)} products.")
        print(f"Login: {USER_EMAIL} / {USER_PASSWORD}")


def Adjustment_exists(business_id, adj_no):
    from models import Adjustment
    return Adjustment.query.filter_by(business_id=business_id, adj_no=adj_no).first() is not None


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
