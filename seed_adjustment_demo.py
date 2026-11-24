"""
OPTIONAL DEMO - the ten adjusting entries required by the project brief.

The project handout (p.96) asks for an option to record these adjustments:
    Closing Stock, Outstanding Expenses, Outstanding Income, Prepaid Expenses,
    Income Received in Advance, Depreciation, Bad Debts,
    Provision for Doubtful Debts, Interest on Capital, Interest on Drawings.

This script posts one example of each type on top of the Mayo Stationery Store
dataset so the "Adjusting Entries" page, the Adjusted Trial Balance and the
financial statements can be shown with every type present.  Run it ONLY on a
test database copy - it changes the figures (that is the whole point of an
adjustment), so the original 24-transaction results must be captured before.

    ACCOUNTING_PRO_DATA_DIR=/tmp/adj_demo python seed_mayo.py
    ACCOUNTING_PRO_DATA_DIR=/tmp/adj_demo python seed_adjustment_demo.py
"""
from datetime import date

from app import create_app
from models import Account, Adjustment, Business, User
from accounting.adjustments import create_adjustment

AS_OF = "2026-03-31"

# (adj_no, kind, description, debit account code, credit account code, amount)
EXAMPLES = [
    ("ADJ-0002", "Outstanding Expenses", "Salaries for March 2026 unpaid",
     "5040", "2010", "8000"),
    ("ADJ-0003", "Outstanding Income", "Commission earned but not yet received",
     "1100", "4020", "5000"),
    ("ADJ-0004", "Prepaid Expenses", "Shop rent paid in advance for April 2026",
     "1090", "5050", "6000"),
    ("ADJ-0005", "Income Received in Advance", "Advance order money received from a school",
     "4000", "2040", "4000"),
    ("ADJ-0006", "Depreciation", "Depreciation on furniture & fixtures @ 5%",
     "5080", "1050", "11200"),
    ("ADJ-0007", "Bad Debts", "Bad debts written off",
     "5090", "1020", "2000"),
    ("ADJ-0008", "Provision for Doubtful Debts", "Provision for doubtful debts @ 5% of debtors",
     "5100", "1020", "1500"),
    ("ADJ-0009", "Interest on Capital", "Interest on capital @ 1% for three months",
     "5110", "3000", "7000"),
    ("ADJ-0010", "Interest on Drawings", "Interest on drawings @ 10%",
     "3010", "4040", "3000"),
]


def main():
    app = create_app()
    with app.app_context():
        business = Business.query.filter_by(name="Mayo Stationery Store").first()
        if business is None:
            print("Run seed_mayo.py first - Mayo Stationery Store was not found.")
            return
        user = User.query.filter_by(email="mayo@accountingpro.local").first()
        accounts = {a.code: a for a in
                    Account.query.filter_by(business_id=business.id).all()}

        posted = 0
        for adj_no, kind, desc, dr, cr, amount in EXAMPLES:
            if Adjustment.query.filter_by(business_id=business.id, adj_no=adj_no).first():
                print(f"  {adj_no} already exists - skipped")
                continue
            for code in (dr, cr):
                if code not in accounts:
                    raise SystemExit(f"Account {code} missing - open Chart of Accounts and "
                                     f"press 'Load standard chart' first.")
            create_adjustment(business.id, user.id,
                              {"adj_no": adj_no, "adj_date": AS_OF, "kind": kind,
                               "description": desc, "narration": f"{kind} - {desc}"},
                              [{"account_id": str(accounts[dr].id), "debit": amount,
                                "credit": "0", "description": desc},
                               {"account_id": str(accounts[cr].id), "debit": "0",
                                "credit": amount, "description": desc}],
                              status="posted")
            posted += 1
            print(f"  posted {adj_no}  {kind}: Dr {accounts[dr].code} / Cr {accounts[cr].code} "
                  f"Rs. {amount}")

        print(f"\n{posted} adjusting entries posted as at {AS_OF}.")
        print("Only the Adjusted Trial Balance and the statements after it are affected - "
              "the Trial Balance itself stays on the original vouchers.")


if __name__ == "__main__":
    main()
