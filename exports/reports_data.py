"""
Single source of truth for exported reports.

A "report" is a plain dict of sections; the PDF, Word and Excel renderers all
consume exactly the same structure so every format shows identical figures.
"""
from datetime import date, datetime

from models import Account, money, ZERO
from accounting.trial_balance import (trial_balance, adjusted_trial_balance,
                                      post_closing_trial_balance)
from accounting.financial_statements import (trading_account, profit_and_loss,
                                             balance_sheet, cash_flow)
from accounting.reports import journal_rows, ledger_report
from accounting import closing as closing_engine
from accounting import analytics

REPORT_TITLES = {
    "general_journal": "General Journal",
    "general_ledger": "General Ledger",
    "trial_balance": "Trial Balance",
    "adjustments": "Adjusting Entries",
    "adjusted_trial_balance": "Adjusted Trial Balance",
    "trading_account": "Trading Account",
    "profit_loss": "Profit & Loss Account",
    "balance_sheet": "Balance Sheet",
    "closing_entries": "Closing Entries",
    "post_closing_trial_balance": "Post-Closing Trial Balance",
    "cash_flow": "Cash Flow Statement",
    "annual_analysis": "Annual Financial Analysis",
    "full_project": "Complete Accounting File",
}


def _money_col(key, label, width=1.0):
    return {"key": key, "label": label, "align": "right", "type": "money", "width": width}


def _text_col(key, label, width=1.4, align="left"):
    return {"key": key, "label": label, "align": align, "type": "text", "width": width}


def _date_col(key="date", label="Date", width=0.9):
    return {"key": key, "label": label, "align": "left", "type": "date", "width": width}


def _meta(business, title, start=None, end=None, subtitle=None):
    prefix = business.currency or "PKR"
    period = subtitle
    if not period:
        if start and end:
            period = f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"
        elif start:
            period = f"From {start.strftime('%d %b %Y')}"
        elif end:
            period = f"Up to {end.strftime('%d %b %Y')}"
    return {
        "title": title,
        "business": business,
        "currency": prefix,
        "period_label": period or "All periods",
        "generated": datetime.now(),
        "sections": [],
    }


# ---------------------------------------------------------------------------
# Individual reports
# ---------------------------------------------------------------------------
def _journal_section(business, start, end, limit=None, source="journal"):
    data = journal_rows(business.id, start, end, source=source)
    rows = data["rows"] if not limit else data["rows"][:limit]
    cols = [_date_col(), _text_col("voucher_no", "Voucher No", 0.9),
            _text_col("account_name", "Account", 1.8),
            _text_col("description", "Description", 1.8),
            _money_col("debit", "Debit (Rs.)"), _money_col("credit", "Credit (Rs.)")]
    return {
        "type": "table",
        "title": "General Journal" if source == "journal" else "Adjusting Entries",
        "columns": cols,
        "rows": rows,
        "totals": {"label": "Total", "debit": data["total_debit"],
                   "credit": data["total_credit"]},
        "note": None if data["difference"] == 0 else
                f"Debit and credit totals differ by Rs. {data['difference']:,}",
    }


def _ledger_section(business, start, end, account_id=None):
    sections = []
    accounts = ([Account.query.get(account_id)] if account_id
                else Account.query.filter_by(business_id=business.id)
                .order_by(Account.code).all())
    cols = [_date_col(), _text_col("particulars", "Particulars", 2.0),
            _text_col("voucher_no", "Voucher", 0.9),
            _money_col("debit", "Debit"), _money_col("credit", "Credit"),
            {"key": "balance_display", "label": "Balance", "align": "right",
             "type": "text", "width": 1.0}]
    for acc in [a for a in accounts if a]:
        rep = ledger_report(business.id, acc, start, end)
        if not rep["rows"] and rep["opening"] == 0:
            continue
        rows = [{"date": None, "particulars": "Opening Balance", "voucher_no": "",
                 "debit": ZERO, "credit": ZERO,
                 "balance_display": f"{rep['opening']:,} {rep['opening_side']}"}]
        for r in rep["rows"]:
            rows.append({**r, "balance_display": f"{r['balance']:,} {r['balance_side']}"})
        sections.append({
            "type": "table",
            "title": f"{acc.code} - {acc.name} ({acc.category})",
            "columns": cols,
            "rows": rows,
            "totals": {"label": "Closing Balance", "debit": rep["total_debit"],
                       "credit": rep["total_credit"],
                       "closing": f"{rep['closing']:,} {rep['closing_side']}"},
        })
    return sections


def _trial_balance_section(business, start, end):
    tb = trial_balance(business.id, start, end)
    cols = [_text_col("code", "Code", 0.6), _text_col("name", "Account Name", 2.2),
            _money_col("debit", "Debit (Rs.)"), _money_col("credit", "Credit (Rs.)")]
    return {"type": "table", "title": "Trial Balance", "columns": cols,
            "rows": tb["rows"],
            "totals": {"label": "Total", "debit": tb["total_debit"],
                       "credit": tb["total_credit"]},
            "status": "Balanced" if tb["is_balanced"] else
                      f"Not balanced - difference Rs. {tb['difference']:,}",
            "note": None if tb["is_balanced"] else
                    "The trial balance does not balance. Review opening balances and vouchers."}


def _adjusted_tb_section(business, start, end):
    data = adjusted_trial_balance(business.id, start, end)
    cols = [_text_col("name", "Account Name", 2.0),
            _money_col("debit", "TB Debit"), _money_col("credit", "TB Credit"),
            _money_col("adj_debit", "Adj Dr"), _money_col("adj_credit", "Adj Cr"),
            _money_col("adjusted_debit", "Adjusted Dr"),
            _money_col("adjusted_credit", "Adjusted Cr")]
    t = data["totals"]
    return {"type": "table", "title": "Adjusted Trial Balance", "columns": cols,
            "rows": data["rows"],
            "totals_multi": {"label": "Total",
                             "debit": t["orig_debit"], "credit": t["orig_credit"],
                             "adj_debit": t["adj_debit"], "adj_credit": t["adj_credit"],
                             "adjusted_debit": t["adjusted_debit"],
                             "adjusted_credit": t["adjusted_credit"]},
            "status": "Balanced" if t["is_balanced"] else "Not balanced"}


def _two_side_section(title, debit_items, credit_items, total_debit, total_credit,
                      balanced=True, note=None):
    rows = []
    max_len = max(len(debit_items), len(credit_items))
    for i in range(max_len):
        d = debit_items[i] if i < len(debit_items) else None
        c = credit_items[i] if i < len(credit_items) else None
        rows.append({
            "d_label": (d["label"] if d else ""),
            "d_amount": (d["amount"] if d else None),
            "c_label": (c["label"] if c else ""),
            "c_amount": (c["amount"] if c else None),
        })
    cols = [_text_col("d_label", "Debit Side", 2.0), _money_col("d_amount", "Amount (Rs.)"),
            _text_col("c_label", "Credit Side", 2.0), _money_col("c_amount", "Amount (Rs.)")]
    return {"type": "table", "title": title, "columns": cols, "rows": rows,
            "totals": {"label": "Total", "debit": total_debit, "credit": total_credit},
            "status": "Balanced" if balanced else "Not balanced", "note": note}


def _trading_section(business, start, end):
    t = trading_account(business.id, start, end)
    sec = _two_side_section("Trading Account for the period", t["debit_items"],
                            t["credit_items"], t["total_debit"], t["total_credit"],
                            t["is_balanced"])
    sec["result"] = {"label": "Gross Profit" if t["is_profit"] else "Gross Loss",
                     "amount": t["gross_profit"] if t["is_profit"] else t["gross_loss"]}
    return sec


def _pl_section(business, start, end):
    pl = profit_and_loss(business.id, start, end)
    sec = _two_side_section("Profit & Loss Account for the period", pl["debit_items"],
                            pl["credit_items"], pl["total_debit"], pl["total_credit"],
                            pl["is_balanced"])
    sec["result"] = {"label": "Net Profit" if pl["is_profit"] else "Net Loss",
                     "amount": pl["net_profit"] if pl["is_profit"] else pl["net_loss"]}
    return sec


def _bs_section(business, start, end):
    bs = balance_sheet(business.id, start, end)
    rows = []
    for item in bs["current_assets"]:
        rows.append({"label": f"Current Assets - {item['name']}", "amount": item["amount"]})
    for item in bs["fixed_assets"]:
        rows.append({"label": f"Fixed Assets - {item['name']}", "amount": item["amount"]})
    rows.append({"label": "Total Assets", "amount": bs["total_assets"], "bold": True})
    rows.append({"label": "", "amount": None})
    for item in bs["current_liabilities"]:
        rows.append({"label": f"Current Liabilities - {item['name']}",
                     "amount": item["amount"]})
    for item in bs["long_term_liabilities"]:
        rows.append({"label": f"Long Term Liabilities - {item['name']}",
                     "amount": item["amount"]})
    rows.append({"label": "Total Liabilities", "amount": bs["total_liabilities"], "bold": True})
    for item in bs["capital_lines"]:
        rows.append({"label": f"Owner's Equity - {item['name']}",
                     "amount": item.get("display_amount", item["amount"])})
    rows.append({"label": "Total Owner's Equity", "amount": bs["total_equity"], "bold": True})
    rows.append({"label": "Total Liabilities + Equity",
                 "amount": bs["total_liabilities_equity"], "bold": True})
    rows.append({"label": "Difference", "amount": bs["difference"]})
    cols = [_text_col("label", "Particulars", 3.0), _money_col("amount", "Amount (Rs.)")]
    return {"type": "table", "title": "Balance Sheet as at period end", "columns": cols,
            "rows": rows,
            "status": "Balanced" if bs["is_balanced"] else "Does not balance",
            "note": None if bs["is_balanced"] else
                    f"Total assets differ from liabilities + equity by Rs. {bs['difference']:,}. "
                    "No balancing figure has been inserted."}


def _closing_section(business, start, end):
    from models import ClosingEntry
    entries = ClosingEntry.query.filter_by(business_id=business.id, status="posted") \
        .order_by(ClosingEntry.entry_date).all()
    rows = []
    accounts = {a.id: a for a in Account.query.filter_by(business_id=business.id).all()}
    for entry in entries:
        for line in entry.lines:
            acc = accounts.get(line.account_id)
            rows.append({"date": entry.entry_date, "voucher_no": entry.entry_no,
                         "account_name": acc.name if acc else "",
                         "description": line.description or entry.description,
                         "debit": money(line.debit), "credit": money(line.credit)})
    total_d = sum((r["debit"] for r in rows), ZERO)
    total_c = sum((r["credit"] for r in rows), ZERO)
    cols = [_date_col(), _text_col("voucher_no", "Entry No", 0.9),
            _text_col("account_name", "Account", 1.8),
            _text_col("description", "Description", 1.8),
            _money_col("debit", "Debit"), _money_col("credit", "Credit")]
    return {"type": "table", "title": "Closing Entries", "columns": cols, "rows": rows,
            "totals": {"label": "Total", "debit": money(total_d), "credit": money(total_c)},
            "empty": "No closing entries have been posted yet."}


def _post_closing_section(business, start, end):
    tb = post_closing_trial_balance(business.id, start, end)
    cols = [_text_col("code", "Code", 0.6), _text_col("name", "Account Name", 2.2),
            _text_col("category", "Category", 1.2),
            _money_col("debit", "Debit (Rs.)"), _money_col("credit", "Credit (Rs.)")]
    return {"type": "table", "title": "Post-Closing Trial Balance", "columns": cols,
            "rows": tb["rows"],
            "totals": {"label": "Total Debit / Total Credit",
                       "debit": tb["total_debit"], "credit": tb["total_credit"]},
            "status": ("Balanced (Dr = Cr)" if tb["is_balanced"]
                       else f"Not Balanced - difference {tb['difference']}"),
            "note": (("Total Debit " + f"{tb['total_debit']:,.2f}"
                      + " / Total Credit " + f"{tb['total_credit']:,.2f}"
                      + " / Difference " + f"{tb['difference']:,.2f}"
                      + " | Temporary accounts still open: "
                      + ", ".join(f"{r['code']} {r['name']}" for r in tb["unclosed_temporary"]))
                     if tb["unclosed_temporary"] else
                     ("Total Debit " + f"{tb['total_debit']:,.2f}"
                      + " / Total Credit " + f"{tb['total_credit']:,.2f}"
                      + " / Difference " + f"{tb['difference']:,.2f}"
                      + " / Temporary accounts still open: 0 (all closed)"))}


def _cash_flow_section(business, start, end):
    cf = cash_flow(business.id, start, end)
    rows = [{"label": "Opening Cash & Bank Balance", "amount": cf["opening"], "bold": True}]
    for bucket in ("Operating", "Investing", "Financing"):
        rows.append({"label": f"Cash flows from {bucket} Activities", "amount": None})
        for d in cf["details"][bucket]:
            rows.append({"label": f"   {d['date']} - {d['voucher_no']} {d['particulars']}",
                         "amount": d["amount"]})
        rows.append({"label": f"Net cash from {bucket} activities",
                     "amount": cf[bucket.lower()], "bold": True})
    rows.append({"label": "Net Increase / (Decrease) in Cash", "amount": cf["net_change"],
                 "bold": True})
    rows.append({"label": "Closing Cash & Bank Balance", "amount": cf["closing"],
                 "bold": True})
    cols = [_text_col("label", "Particulars", 3.0), _money_col("amount", "Amount (Rs.)")]
    return {"type": "table", "title": "Cash Flow Statement", "columns": cols, "rows": rows}


def _annual_section(business, start, end):
    data = analytics.annual_analysis(business.id, start, end)
    cur, prev = data["current"], data["previous"]
    rows = []
    for key, label in (("total_income", "Total Revenue"), ("total_expenses", "Total Expenses"),
                       ("gross_profit", "Gross Profit"), ("net_profit", "Net Profit"),
                       ("total_assets", "Total Assets"), ("total_liabilities", "Total Liabilities"),
                       ("owner_capital", "Owner's Equity"), ("cash_balance", "Cash & Bank"),
                       ("receivable", "Accounts Receivable"), ("payable", "Accounts Payable")):
        rows.append({"metric": label, "current": cur[key],
                     "previous": prev[key] if data["previous_has_data"] else None,
                     "growth": analytics.growth(cur[key], prev[key])
                     if data["previous_has_data"] else None})
    cols = [_text_col("metric", "Metric", 2.0), _money_col("current", "Current Year (Rs.)"),
            _money_col("previous", "Previous Year (Rs.)"), _money_col("growth", "Growth %")]
    return {"type": "table", "title": f"Annual Analysis {data['label']}", "columns": cols,
            "rows": rows,
            "note": None if data["previous_has_data"] else
                    "No comparison data available for the previous year."}


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------
def build_report(name, business, start=None, end=None, account_id=None, **kwargs):
    report = _meta(business, REPORT_TITLES.get(name, "Report"), start, end)
    sections = report["sections"]

    if name == "general_journal":
        sections.append(_journal_section(business, start, end))
    elif name == "general_ledger":
        sections.extend(_ledger_section(business, start, end, account_id))
    elif name == "trial_balance":
        sections.append(_trial_balance_section(business, start, end))
    elif name == "adjustments":
        sections.append(_journal_section(business, start, end, source="adjustments"))
    elif name == "adjusted_trial_balance":
        sections.append(_adjusted_tb_section(business, start, end))
    elif name == "trading_account":
        sections.append(_trading_section(business, start, end))
    elif name == "profit_loss":
        sections.append(_pl_section(business, start, end))
    elif name == "balance_sheet":
        sections.append(_bs_section(business, start, end))
    elif name == "closing_entries":
        sections.append(_closing_section(business, start, end))
    elif name == "post_closing_trial_balance":
        sections.append(_post_closing_section(business, start, end))
    elif name == "cash_flow":
        sections.append(_cash_flow_section(business, start, end))
    elif name == "annual_analysis":
        sections.append(_annual_section(business, start, end))
    elif name == "full_project":
        sections.append(_trial_balance_section(business, start, end))
        sections.append(_journal_section(business, start, end))
        sections.append(_journal_section(business, start, end, source="adjustments"))
        sections.append(_adjusted_tb_section(business, start, end))
        sections.append(_trading_section(business, start, end))
        sections.append(_pl_section(business, start, end))
        sections.append(_bs_section(business, start, end))
        sections.append(_closing_section(business, start, end))
        sections.append(_post_closing_section(business, start, end))
        sections.extend(_ledger_section(business, start, end))
        sections.append(_cash_flow_section(business, start, end))
    else:
        raise ValueError(f"Unknown report '{name}'")

    report["name"] = name
    return report
