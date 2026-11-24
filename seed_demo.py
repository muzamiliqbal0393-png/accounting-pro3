"""
OPTIONAL demo data loader.

This creates a clearly-labelled DEMO user and business so you can explore every
screen immediately.  It is **sample data**, not your own books:

    login : demo@accountingpro.local
    password : Demo12345

Delete it at any time with:  python reset_db.py
(or remove the single demo user from the database).
"""
from datetime import date
from decimal import Decimal

from app import create_app, DEFAULT_CHART
from extensions import db
from models import (Account, AccountingPeriod, Budget, Business, NORMAL_BALANCE,
                    Product, User, UserSettings, money)
from accounting.journal import create_entry
from accounting.adjustments import create_adjustment
from accounting.inventory import record_sale

DEMO_EMAIL = "demo@accountingpro.local"
DEMO_PASSWORD = "Demo12345"


def build():
    app = create_app()
    with app.app_context():
        if User.query.filter_by(email=DEMO_EMAIL).first():
            print("Demo user already exists - nothing to do.")
            print(f"Login: {DEMO_EMAIL} / {DEMO_PASSWORD}")
            return

        user = User(name="Demo Accountant", email=DEMO_EMAIL)
        user.set_password(DEMO_PASSWORD)
        db.session.add(user)
        db.session.flush()
        db.session.add(UserSettings(user_id=user.id))

        business = Business(
            user_id=user.id,
            name="DEMO - Sample Traders",
            owner_name="Demo Accountant",
            address="12 Mall Road, Lahore, Pakistan",
            phone="+92 300 0000000",
            email="demo@accountingpro.local",
            business_type="Retail Trading",
            currency="PKR",
            fy_start=date(2026, 1, 1),
            fy_end=date(2026, 3, 31),
            accounting_method="Accrual",
            description="Sample business created by seed_demo.py so every report has data. "
                        "Replace it with your own business profile.",
        )
        db.session.add(business)
        db.session.flush()
        db.session.add(AccountingPeriod(business_id=business.id,
                                        label="1 Jan 2026 - 31 Mar 2026",
                                        kind="Quarterly", start_date=date(2026, 1, 1),
                                        end_date=date(2026, 3, 31), is_active=True))
        db.session.commit()

        acc = {}
        for code, name, category, sub in DEFAULT_CHART:
            account = Account(business_id=business.id, code=code, name=name,
                              category=category, subcategory=sub,
                              normal_balance=NORMAL_BALANCE[category])
            db.session.add(account)
            db.session.flush()
            acc[code] = account.id
        db.session.commit()

        def post(no, dt, etype, desc, lines):
            create_entry(business.id, user.id,
                         {"voucher_no": no, "entry_date": dt, "entry_type": etype,
                          "description": desc, "narration": desc},
                         [{"account_id": str(a), "debit": dr, "credit": cr,
                           "description": desc} for a, dr, cr in lines],
                         status="posted")

        data = [
            ("JV-0001", "2026-01-02", "Capital", "Owner introduced capital",
             [(acc["1000"], "800000", "0"), (acc["3000"], "0", "800000")]),
            ("JV-0002", "2026-01-03", "Cash Payment", "Purchase of shop furniture",
             [(acc["1050"], "250000", "0"), (acc["1000"], "0", "250000")]),
            ("JV-0003", "2026-01-05", "Cash Purchase", "Goods purchased for cash",
             [(acc["5000"], "300000", "0"), (acc["1000"], "0", "300000")]),
            ("JV-0004", "2026-01-20", "Cash Sale", "Cash sales - January",
             [(acc["1000"], "180000", "0"), (acc["4000"], "0", "180000")]),
            ("JV-0005", "2026-02-01", "Cash Sale", "Cash sales - February",
             [(acc["1000"], "260000", "0"), (acc["4000"], "0", "260000")]),
            ("JV-0006", "2026-02-03", "Credit Purchase", "Goods purchased on credit",
             [(acc["5000"], "200000", "0"), (acc["2000"], "0", "200000")]),
            ("JV-0007", "2026-02-10", "Expense", "Salaries for February",
             [(acc["5040"], "60000", "0"), (acc["1000"], "0", "60000")]),
            ("JV-0008", "2026-02-15", "Expense", "Shop rent for February",
             [(acc["5050"], "40000", "0"), (acc["1000"], "0", "40000")]),
            ("JV-0009", "2026-02-20", "Credit Sale", "Credit sales to Al-Karam Store",
             [(acc["1020"], "120000", "0"), (acc["4000"], "0", "120000")]),
            ("JV-0010", "2026-02-25", "Cash Receipt", "Recovery from Al-Karam Store",
             [(acc["1000"], "90000", "0"), (acc["1020"], "0", "90000")]),
            ("JV-0011", "2026-03-05", "Expense", "Electricity bill",
             [(acc["5060"], "15000", "0"), (acc["1000"], "0", "15000")]),
            ("JV-0012", "2026-03-10", "Expense", "Advertising campaign",
             [(acc["5070"], "25000", "0"), (acc["1000"], "0", "25000")]),
            ("JV-0013", "2026-03-15", "Drawings", "Owner drawings",
             [(acc["3010"], "30000", "0"), (acc["1000"], "0", "30000")]),
        ]
        for no, dt, etype, desc, lines in data:
            post(no, dt, etype, desc, lines)

        # --- adjusting entries -------------------------------------------------
        create_adjustment(business.id, user.id,
                          {"adj_no": "ADJ-0001", "adj_date": "2026-03-31",
                           "kind": "Closing Stock",
                           "description": "Closing stock as at 31 March 2026",
                           "narration": "Physical stock count"},
                          [{"account_id": str(acc["1030"]), "debit": "180000", "credit": "0",
                            "description": "Closing stock"},
                           {"account_id": str(acc["4030"]), "debit": "0", "credit": "180000",
                            "description": "To trading account"}],
                          status="posted")
        create_adjustment(business.id, user.id,
                          {"adj_no": "ADJ-0002", "adj_date": "2026-03-31",
                           "kind": "Depreciation",
                           "description": "Depreciation on furniture @ 10%",
                           "narration": "250,000 x 10%"},
                          [{"account_id": str(acc["5080"]), "debit": "25000", "credit": "0",
                            "description": "Depreciation on furniture"},
                           {"account_id": str(acc["1050"]), "debit": "0", "credit": "25000",
                            "description": "Accumulated depreciation"}],
                          status="posted")

        # --- products (quantity / margin tracking) -----------------------------
        products = [
            Product(business_id=business.id, code="SKU-001", name="Cotton Shirt",
                    category="Apparel", purchase_cost=money("850"),
                    selling_price=money("1250"), opening_qty=120, reorder_level=30),
            Product(business_id=business.id, code="SKU-002", name="Denim Jeans",
                    category="Apparel", purchase_cost=money("1450"),
                    selling_price=money("2100"), opening_qty=80, reorder_level=20),
            Product(business_id=business.id, code="SKU-003", name="Sports Shoes",
                    category="Footwear", purchase_cost=money("2200"),
                    selling_price=money("3200"), opening_qty=45, reorder_level=15),
        ]
        for p in products:
            db.session.add(p)
        db.session.commit()

        # product sales recorded for quantity / margin reporting only
        for product, qty in ((products[0], 60), (products[1], 35), (products[2], 18)):
            record_sale(business.id, user.id, product, qty, product.selling_price,
                        "2026-03-20", customer="Walk-in", post_journal=False)

        db.session.add(Budget(business_id=business.id, name="Q1 2026 Budget",
                              kind="Quarterly", start_date=date(2026, 1, 1),
                              end_date=date(2026, 3, 31),
                              planned_income=money("600000"),
                              planned_expenses=money("200000"),
                              notes="Demo budget for the first quarter"))
        db.session.commit()

        print("Demo data created.")
        print(f"  Business : {business.name}")
        print(f"  Login    : {DEMO_EMAIL}")
        print(f"  Password : {DEMO_PASSWORD}")


if __name__ == "__main__":
    build()
