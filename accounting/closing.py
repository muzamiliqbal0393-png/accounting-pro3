"""
Closing entries: zero out temporary accounts and transfer the result to capital.

Nothing is posted automatically - the user reviews a preview then confirms.
"""
from datetime import datetime

from extensions import db
from models import (Account, ClosingEntry, ClosingLine, money, ZERO,
                    ACTIVE_ENTRY_STATUS)
from accounting.journal import JournalError, next_voucher_no, parse_date
from accounting.financial_statements import statement_balances, profit_and_loss

TEMP_ASSET_SUBS = ("opening_stock",)


def preview_closing(business_id, start=None, end=None):
    """
    Build the closing entry lines without saving anything.

    Temporary accounts (all Revenue, all Expenses and Opening Stock) are zeroed.
    The resulting difference is transferred to the Capital account, exactly as an
    Income Summary account would.  The difference is then reconciled against the
    Profit & Loss result so any inconsistency is surfaced, never hidden.
    """
    bal = statement_balances(business_id, start, end)
    pl = profit_and_loss(business_id, start, end, bal)

    lines = []
    debit_total = ZERO
    credit_total = ZERO

    for row in sorted(bal.values(), key=lambda r: r["account"].code):
        acc = row["account"]
        is_temp = (acc.category in ("Revenue", "Expenses")
                   or (acc.category == "Assets" and acc.subcategory in TEMP_ASSET_SUBS))
        if not is_temp:
            continue
        if row["debit_balance"]:
            lines.append({"account": acc, "debit": ZERO,
                          "credit": row["debit_balance"],
                          "description": f"Close {acc.name}"})
            credit_total += row["debit_balance"]
        elif row["credit_balance"]:
            lines.append({"account": acc, "debit": row["credit_balance"],
                          "credit": ZERO,
                          "description": f"Close {acc.name}"})
            debit_total += row["credit_balance"]

    capital = (Account.query
               .filter_by(business_id=business_id, subcategory="capital")
               .order_by(Account.code).first())

    net = money(debit_total - credit_total)
    if capital and net != 0:
        if net > 0:
            lines.append({"account": capital, "debit": ZERO, "credit": net,
                          "description": "Net profit transferred to Capital"})
            credit_total += net
        else:
            lines.append({"account": capital, "debit": -net, "credit": ZERO,
                          "description": "Net loss transferred to Capital"})
            debit_total += -net

    drawings_rows = [r for r in bal.values()
                     if r["account"].subcategory == "drawings" and r["debit_balance"]]
    drawings_total = money(sum((r["debit_balance"] for r in drawings_rows), ZERO))
    for row in drawings_rows:
        lines.append({"account": row["account"], "debit": ZERO,
                      "credit": row["debit_balance"],
                      "description": "Drawings closed"})
        credit_total += row["debit_balance"]
    if capital and drawings_total:
        lines.append({"account": capital, "debit": drawings_total, "credit": ZERO,
                      "description": "Drawings transferred to Capital"})
        debit_total += drawings_total

    pl_net = pl["net_profit"] if pl["is_profit"] else -pl["net_loss"]
    difference = money(net - pl_net)

    return {
        "lines": lines,
        "net_result": net,
        "net_profit": max(net, ZERO),
        "net_loss": max(-net, ZERO),
        "is_profit": net >= 0,
        "total_debit": money(debit_total),
        "total_credit": money(credit_total),
        "is_balanced": money(debit_total) == money(credit_total),
        "capital_account": capital,
        "drawings_total": drawings_total,
        "profit_and_loss": pl,
        "pl_net": money(pl_net),
        "reconciliation_difference": difference,
        "reconciles": difference == 0,
    }


def create_closing_entry(business_id, user_id, start, end, entry_date=None,
                         period_id=None, description=None, status="draft"):
    preview = preview_closing(business_id, start, end)
    if not preview["lines"]:
        raise JournalError("There are no revenue, expense, drawings or opening stock "
                           "balances to close for the selected period.")
    if not preview["is_balanced"]:
        raise JournalError("Closing entry preview does not balance; it cannot be saved.")

    entry = ClosingEntry(
        business_id=business_id,
        period_id=period_id,
        entry_no=next_voucher_no(business_id, prefix="CL-", model=ClosingEntry,
                                 field="entry_no"),
        entry_date=parse_date(entry_date) if entry_date else (end or datetime.utcnow().date()),
        description=description or f"Closing entries for period ending {end}",
        status=status,
        total_debit=preview["total_debit"],
        total_credit=preview["total_credit"],
        net_result=preview["net_result"],
        created_by=user_id,
        posted_at=datetime.utcnow() if status == "posted" else None,
    )
    for line in preview["lines"]:
        entry.lines.append(ClosingLine(account_id=line["account"].id,
                                       debit=money(line["debit"]),
                                       credit=money(line["credit"]),
                                       description=line["description"][:255]))
    try:
        db.session.add(entry)
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        raise JournalError(f"Could not save closing entry: {exc}")
    return entry


def post_closing_entry(entry):
    if entry.status == "posted":
        raise JournalError("This closing entry is already posted.")
    if not entry.is_balanced:
        raise JournalError("Closing entry is unbalanced and cannot be posted.")
    entry.status = "posted"
    entry.posted_at = datetime.utcnow()
    db.session.commit()
    return entry


def delete_closing_entry(entry):
    if entry.status == "posted":
        raise JournalError("Posted closing entries cannot be deleted (audit trail).")
    db.session.delete(entry)
    db.session.commit()
