"""Adjusting entries (accruals, prepayments, depreciation, stock, provisions...)."""
from datetime import datetime

from extensions import db
from models import Adjustment, AdjustmentLine, ADJUSTMENT_KINDS, money, ZERO
from accounting.journal import (JournalError, build_lines, validate_balance,
                                validate_accounts, validate_period, parse_date,
                                parse_amount, next_voucher_no)

#: Suggested debit/credit subcategories for each adjustment type.  The user
#: always confirms the accounts - nothing is guessed silently.
ADJUSTMENT_GUIDE = {
    "Closing Stock": ("Asset: Closing Stock / Inventory", "Revenue: Trading A/c (contra)"),
    "Outstanding Expenses": ("Expenses: matching expense", "Liabilities: Outstanding Expenses"),
    "Prepaid Expenses": ("Asset: Other Assets (prepaid)", "Expenses: matching expense"),
    "Accrued Income": ("Asset: Accounts Receivable", "Revenue: Other Income"),
    "Income Received in Advance": ("Revenue: Sales / Other Income", "Liabilities: Other Liabilities"),
    "Depreciation": ("Expenses: Depreciation", "Asset: fixed asset (contra)"),
    "Bad Debts": ("Expenses: Bad Debts", "Asset: Accounts Receivable"),
    "Provision for Doubtful Debts": ("Expenses: indirect expense", "Asset: Accounts Receivable"),
    "Interest on Capital": ("Expenses: indirect expense", "Equity: Owner's Capital"),
    "Interest on Drawings": ("Equity: Drawings", "Revenue: Other Income"),
    "Other": ("Any debit account", "Any credit account"),
}


def create_adjustment(business_id, user_id, data, raw_lines, status="posted",
                      period_id=None, commit=True):
    lines = build_lines(raw_lines)
    total_debit, total_credit = validate_balance(lines)
    validate_accounts(business_id, lines)

    adj_no = (data.get("adj_no") or "").strip() or next_voucher_no(
        business_id, prefix="ADJ-", model=Adjustment, field="adj_no")
    if Adjustment.query.filter_by(business_id=business_id, adj_no=adj_no).first():
        raise JournalError(f"Adjustment number '{adj_no}' already exists.")

    adj_date = parse_date(data.get("adj_date"), "Adjustment date")
    validate_period(business_id, adj_date, label="Adjustment")

    adj = Adjustment(
        business_id=business_id,
        period_id=period_id,
        adj_no=adj_no,
        adj_date=adj_date,
        kind=data.get("kind") if data.get("kind") in ADJUSTMENT_KINDS else "Other",
        description=(data.get("description") or "")[:255],
        narration=(data.get("narration") or "").strip(),
        status=status,
        total_debit=total_debit,
        total_credit=total_credit,
        created_by=user_id,
    )
    for line in lines:
        adj.lines.append(AdjustmentLine(**line))
    if commit:
        try:
            db.session.add(adj)
            db.session.commit()
        except Exception as exc:  # pragma: no cover
            db.session.rollback()
            raise JournalError(f"Could not save adjustment: {exc}")
    else:
        db.session.add(adj)
    return adj


def post_adjustment(adj):
    if adj.status == "posted":
        raise JournalError("This adjustment is already posted.")
    if not adj.is_balanced:
        raise JournalError("Unbalanced adjustment cannot be posted.")
    adj.status = "posted"
    db.session.commit()
    return adj


def delete_adjustment(adj):
    if adj.status == "posted":
        raise JournalError("Posted adjustments cannot be deleted. "
                           "Reverse it with an opposite adjustment instead.")
    db.session.delete(adj)
    db.session.commit()


def quick_closing_stock(business_id, user_id, amount, as_of, inventory_account_id,
                        contra_account_id, period_id=None, narration=None):
    """One-click closing stock adjustment: Dr Inventory / Cr Trading A/c."""
    lines = [
        {"account_id": inventory_account_id, "debit": parse_amount(amount), "credit": ZERO,
         "description": "Closing stock"},
        {"account_id": contra_account_id, "debit": ZERO, "credit": parse_amount(amount),
         "description": "Closing stock transferred to Trading A/c"},
    ]
    return create_adjustment(business_id, user_id,
                             {"adj_date": as_of, "kind": "Closing Stock",
                              "description": f"Closing stock as at {as_of}",
                              "narration": narration or "Closing stock adjustment"},
                             [{"account_id": str(l["account_id"]),
                               "debit": l["debit"], "credit": l["credit"],
                               "description": l["description"]} for l in lines],
                             period_id=period_id)
