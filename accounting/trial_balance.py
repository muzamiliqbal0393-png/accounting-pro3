"""Trial balance, adjusted trial balance and post-closing trial balance."""
from decimal import Decimal

from models import ACTIVE_ENTRY_STATUS, money, ZERO
from accounting.ledger import (account_balances, posted_journal_lines,
                               posted_adjustment_lines, posted_closing_lines,
                               totals)


def _row(account, debit, credit):
    return {
        "account_id": account.id,
        "code": account.code,
        "name": account.name,
        "category": account.category,
        "subcategory": account.subcategory,
        "subcategory_label": account.subcategory_label,
        "normal_balance": account.normal_balance,
        "debit": money(debit),
        "credit": money(credit),
    }


def trial_balance(business_id, start=None, end=None, include_adjustments=False,
                  include_closing=False, include_zero=False, categories=None,
                  include_opening=True):
    """
    Trial balance built strictly from opening balances + posted vouchers.
    Nothing is ever forced to balance.
    """
    balances = account_balances(business_id, start, end,
                                include_adjustments=include_adjustments,
                                include_closing=include_closing,
                                include_opening=include_opening)
    rows = []
    for row in sorted(balances.values(), key=lambda r: r["account"].code):
        acc = row["account"]
        if categories and acc.category not in categories:
            continue
        debit, credit = row["debit_balance"], row["credit_balance"]
        if not include_zero and debit == 0 and credit == 0:
            continue
        rows.append(_row(acc, debit, credit))
    total_debit = money(sum((r["debit"] for r in rows), ZERO))
    total_credit = money(sum((r["credit"] for r in rows), ZERO))
    return {
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "difference": money(total_debit - total_credit),
        "is_balanced": total_debit == total_credit,
        "start": start,
        "end": end,
    }


def adjusted_trial_balance(business_id, start=None, end=None):
    """
    Trial balance columns: original / adjustments / adjusted.

    Original TB   = opening balances + posted journal vouchers
    Adjustments   = posted adjusting entries for the period
    Adjusted TB   = original + adjustments
    """
    original = trial_balance(business_id, start, end, include_zero=True)

    # Adjustment column = posted *adjusting entries only*.  (account_balances()
    # always includes ordinary journal vouchers, so it cannot be used here.)
    adj_totals = {}
    for line in posted_adjustment_lines(business_id, start, end):
        acc = adj_totals.setdefault(line["account_id"], {"debit": ZERO, "credit": ZERO})
        acc["debit"] += line["debit"]
        acc["credit"] += line["credit"]

    rows = []
    for row in original["rows"]:
        adj = adj_totals.get(row["account_id"])
        adj_debit = money(adj["debit"]) if adj else ZERO
        adj_credit = money(adj["credit"]) if adj else ZERO
        # adjusted balance = original balance + adjustments, shown on ONE side only
        net = money(row["debit"] - row["credit"] + adj_debit - adj_credit)
        adjusted_debit = net if net > 0 else ZERO
        adjusted_credit = money(-net) if net < 0 else ZERO
        # keep only rows that still carry a balance or an adjustment
        if (row["debit"] == 0 and row["credit"] == 0
                and adj_debit == 0 and adj_credit == 0):
            continue
        rows.append({**row,
                     "adj_debit": adj_debit,
                     "adj_credit": adj_credit,
                     "adjusted_debit": adjusted_debit,
                     "adjusted_credit": adjusted_credit})

    totals = {
        "orig_debit": money(sum((r["debit"] for r in rows), ZERO)),
        "orig_credit": money(sum((r["credit"] for r in rows), ZERO)),
        "adj_debit": money(sum((r["adj_debit"] for r in rows), ZERO)),
        "adj_credit": money(sum((r["adj_credit"] for r in rows), ZERO)),
        "adjusted_debit": money(sum((r["adjusted_debit"] for r in rows), ZERO)),
        "adjusted_credit": money(sum((r["adjusted_credit"] for r in rows), ZERO)),
    }
    totals["difference"] = money(totals["adjusted_debit"] - totals["adjusted_credit"])
    totals["is_balanced"] = totals["adjusted_debit"] == totals["adjusted_credit"]
    return {"rows": rows, "totals": totals, "start": start, "end": end}


def post_closing_trial_balance(business_id, start=None, end=None):
    """Only permanent (asset / liability / equity) accounts after closing."""
    tb = trial_balance(business_id, start, end, include_adjustments=True,
                       include_closing=True,
                       categories=("Assets", "Liabilities", "Equity"),
                       include_zero=False)
    # Temporary accounts must be zero; list any that are not
    temp = trial_balance(business_id, start, end, include_adjustments=True,
                         include_closing=True,
                         categories=("Revenue", "Expenses"), include_zero=False)
    tb["unclosed_temporary"] = temp["rows"]
    tb["temporary_total"] = money(sum((r["debit"] for r in temp["rows"]), ZERO)
                                  + sum((r["credit"] for r in temp["rows"]), ZERO))
    return tb
