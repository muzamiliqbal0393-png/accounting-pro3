"""
Ledger engine.

Every figure in the application is derived from this module.  A "line" is a
normalised, read-only view of a journal / adjustment / closing line so that all
downstream reports share exactly one source of truth.
"""
from datetime import date
from decimal import Decimal

from models import (Account, JournalEntry, JournalLine, Adjustment,
                    AdjustmentLine, ClosingEntry, ClosingLine,
                    ACTIVE_ENTRY_STATUS, money, ZERO)


# ---------------------------------------------------------------------------
# Line providers
# ---------------------------------------------------------------------------
def posted_journal_lines(business_id, start=None, end=None, account_id=None):
    """Posted (and reversed) journal lines."""
    q = (JournalEntry.query
         .filter(JournalEntry.business_id == business_id,
                 JournalEntry.status.in_(ACTIVE_ENTRY_STATUS)))
    if start:
        q = q.filter(JournalEntry.entry_date >= start)
    if end:
        q = q.filter(JournalEntry.entry_date <= end)
    rows = []
    for entry in q.order_by(JournalEntry.entry_date, JournalEntry.id).all():
        for line in entry.lines:
            if account_id and line.account_id != account_id:
                continue
            rows.append({
                "date": entry.entry_date,
                "voucher_no": entry.voucher_no,
                "account_id": line.account_id,
                "debit": money(line.debit),
                "credit": money(line.credit),
                "description": line.description or entry.description or "",
                "narration": entry.narration or "",
                "source": "Journal",
                "entry_id": entry.id,
                "entry_type": entry.entry_type,
            })
    return rows


def posted_adjustment_lines(business_id, start=None, end=None, account_id=None):
    q = Adjustment.query.filter_by(business_id=business_id, status="posted")
    if start:
        q = q.filter(Adjustment.adj_date >= start)
    if end:
        q = q.filter(Adjustment.adj_date <= end)
    rows = []
    for adj in q.order_by(Adjustment.adj_date, Adjustment.id).all():
        for line in adj.lines:
            if account_id and line.account_id != account_id:
                continue
            rows.append({
                "date": adj.adj_date,
                "voucher_no": adj.adj_no,
                "account_id": line.account_id,
                "debit": money(line.debit),
                "credit": money(line.credit),
                "description": line.description or adj.description or "",
                "narration": adj.narration or "",
                "source": "Adjustment",
                "entry_id": adj.id,
                "entry_type": adj.kind,
            })
    return rows


def posted_closing_lines(business_id, start=None, end=None, account_id=None):
    q = ClosingEntry.query.filter_by(business_id=business_id, status="posted")
    if start:
        q = q.filter(ClosingEntry.entry_date >= start)
    if end:
        q = q.filter(ClosingEntry.entry_date <= end)
    rows = []
    for closing in q.order_by(ClosingEntry.entry_date, ClosingEntry.id).all():
        for line in closing.lines:
            if account_id and line.account_id != account_id:
                continue
            rows.append({
                "date": closing.entry_date,
                "voucher_no": closing.entry_no,
                "account_id": line.account_id,
                "debit": money(line.debit),
                "credit": money(line.credit),
                "description": line.description or closing.description or "",
                "narration": closing.description or "",
                "source": "Closing",
                "entry_id": closing.id,
                "entry_type": "Closing",
            })
    return rows


def all_lines(business_id, start=None, end=None, account_id=None,
              include_adjustments=False, include_closing=False):
    rows = posted_journal_lines(business_id, start, end, account_id)
    if include_adjustments:
        rows += posted_adjustment_lines(business_id, start, end, account_id)
    if include_closing:
        rows += posted_closing_lines(business_id, start, end, account_id)
    rows.sort(key=lambda r: (r["date"], r["voucher_no"]))
    return rows


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------
def account_balances(business_id, start=None, end=None, include_adjustments=False,
                     include_closing=False, include_opening=True):
    """Return ``{account_id: balance dict}`` for every account of the business."""
    accounts = (Account.query.filter_by(business_id=business_id)
                .order_by(Account.code).all())
    data = {}
    for acc in accounts:
        opening = money(acc.opening_net) if include_opening else ZERO
        data[acc.id] = {
            "account": acc,
            "opening": opening,
            "debit_total": ZERO,
            "credit_total": ZERO,
            "opening_debit": money(acc.opening_debit) if include_opening else ZERO,
            "opening_credit": money(acc.opening_credit) if include_opening else ZERO,
        }
    for line in all_lines(business_id, start, end,
                          include_adjustments=include_adjustments,
                          include_closing=include_closing):
        row = data.get(line["account_id"])
        if row is None:
            continue
        row["debit_total"] += line["debit"]
        row["credit_total"] += line["credit"]
    for row in data.values():
        net = row["opening"] + row["debit_total"] - row["credit_total"]
        row["net"] = money(net)
        row["debit_balance"] = money(net) if net > 0 else ZERO
        row["credit_balance"] = money(-net) if net < 0 else ZERO
    return data


def totals(balances):
    """Sum debit / credit balances across a balance dict."""
    td = sum((b["debit_balance"] for b in balances.values()), ZERO)
    tc = sum((b["credit_balance"] for b in balances.values()), ZERO)
    return money(td), money(tc)


def sum_subcategories(balances, subcategories, side="debit"):
    """Total of the requested sub-categories on a given side."""
    total = ZERO
    for row in balances.values():
        if row["account"].subcategory in subcategories:
            total += row["debit_balance"] if side == "debit" else row["credit_balance"]
    return money(total)


def subcategory_items(balances, subcategories, side="debit", exclude_zero=True):
    items = []
    for row in balances.values():
        acc = row["account"]
        if acc.subcategory not in subcategories:
            continue
        amount = row["debit_balance"] if side == "debit" else row["credit_balance"]
        if exclude_zero and amount == 0:
            continue
        items.append({"account": acc, "amount": money(amount)})
    items.sort(key=lambda i: i["account"].code)
    return items


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
def ledger_rows(business_id, account, start=None, end=None,
                include_adjustments=True, include_closing=True):
    """Running-balance ledger for a single account (normal-balance aware)."""
    rows = all_lines(business_id, start, end, account_id=account.id,
                     include_adjustments=include_adjustments,
                     include_closing=include_closing)
    opening = money(account.opening_net)
    balance = opening
    out = []
    total_debit = money(account.opening_debit)
    total_credit = money(account.opening_credit)
    for r in rows:
        balance += r["debit"] - r["credit"]
        total_debit += r["debit"]
        total_credit += r["credit"]
        dr_side = account.normal_balance == "Debit"
        out.append({
            "date": r["date"],
            "particulars": r["description"] or r["entry_type"],
            "voucher_no": r["voucher_no"],
            "source": r["source"],
            "debit": r["debit"],
            "credit": r["credit"],
            "balance": money(balance),
            "balance_side": (("Dr" if balance >= 0 else "Cr") if dr_side
                             else ("Cr" if balance <= 0 else "Dr")),
        })
    return {
        "account": account,
        "rows": out,
        "opening": opening,
        "opening_side": "Dr" if opening >= 0 else "Cr",
        "total_debit": money(total_debit),
        "total_credit": money(total_credit),
        "closing": money(balance),
        "closing_side": ("Dr" if balance >= 0 else "Cr") if account.normal_balance == "Debit"
                        else ("Cr" if balance <= 0 else "Dr"),
    }


def cash_accounts(business_id):
    return (Account.query
            .filter(Account.business_id == business_id,
                    Account.subcategory.in_(("cash", "bank")))
            .order_by(Account.code).all())


def cash_balance(business_id, start=None, end=None):
    balances = account_balances(business_id, start, end, include_adjustments=True)
    total = ZERO
    for acc in cash_accounts(business_id):
        row = balances.get(acc.id)
        if row:
            total += row["net"]
    return money(total)
