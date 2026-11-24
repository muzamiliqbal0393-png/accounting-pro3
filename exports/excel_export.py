"""
Excel export using openpyxl.

``render_excel`` produces a workbook for a single report; ``render_full_workbook``
produces the complete 17-worksheet accounting file.
"""
import io
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import (Account, Product, Budget, ClosingEntry, money, ZERO)
from accounting.trial_balance import (trial_balance, adjusted_trial_balance,
                                      post_closing_trial_balance)
from accounting.financial_statements import (trading_account, profit_and_loss,
                                             balance_sheet, cash_flow)
from accounting.reports import journal_rows, ledger_report
from accounting import analytics
from accounting.inventory import inventory_summary

NAVY = "101D3A"
ROYAL = "2457D6"
LIGHT = "F5F7FB"
BORDER_CLR = "D6DCE8"
MONEY_FMT = '#,##0.00'
DATE_FMT = 'dd-mm-yyyy'

thin = Side(style="thin", color=BORDER_CLR)
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)


def _money(value):
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return value


def _sheet(wb, title, headers, rows, money_cols=(), date_cols=(), title_text=None,
           subtitle=None, total_row=None, widths=None, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]

    ws["A1"] = title_text or title
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    ws.row_dimensions[1].height = 22
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = Font(size=9, italic=True, color="6B7280")
        header_row = 3
    else:
        header_row = 2

    for i, head in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=i, value=head)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX
    ws.row_dimensions[header_row].height = 26

    for r, row in enumerate(rows, start=header_row + 1):
        for c, head in enumerate(headers, start=1):
            value = row.get(head)
            if c in money_cols:
                value = _money(value)
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BOX
            cell.font = Font(size=9)
            if c in money_cols:
                cell.number_format = MONEY_FMT
                cell.alignment = Alignment(horizontal="right")
            elif c in date_cols:
                cell.number_format = DATE_FMT
            if r % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=LIGHT)

    if total_row:
        r = header_row + len(rows) + 1
        for c, head in enumerate(headers, start=1):
            value = total_row.get(head)
            if c in money_cols:
                value = _money(value)
                if value is None:
                    continue
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = Font(bold=True, size=9)
            cell.fill = PatternFill("solid", fgColor="E8EDF9")
            cell.border = BOX
            if c in money_cols:
                cell.number_format = MONEY_FMT

    for c in range(1, len(headers) + 1):
        letter = get_column_letter(c)
        if widths and c - 1 < len(widths):
            ws.column_dimensions[letter].width = widths[c - 1]
        else:
            longest = max([len(str(h)) for h in headers] + [12])
            ws.column_dimensions[letter].width = min(max(12, longest + 2), 42)

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = (f"A{header_row}:"
                              f"{get_column_letter(len(headers))}{header_row + len(rows)}")
    return ws


# ---------------------------------------------------------------------------
# Sheet builders
# ---------------------------------------------------------------------------
def _business_sheet(wb, business, start, end, period_label, first=True):
    ws = wb.active if first else wb.create_sheet()
    ws.title = "Business Information"
    ws["A1"] = business.name or "Business"
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = "Complete Computerized Accounting & Business Management System"
    ws["A2"].font = Font(size=9, italic=True, color="6B7280")
    rows = [("Business Name", business.name), ("Owner Name", business.owner_name),
            ("Address", business.address), ("Phone", business.phone),
            ("Email", business.email), ("Business Type", business.business_type),
            ("Currency", f"{business.currency} (Rs.)"),
            ("Financial Year", f"{business.fy_start} to {business.fy_end}"),
            ("Accounting Method", business.accounting_method),
            ("Report Period", period_label),
            ("Generated On", datetime.now().strftime("%d-%b-%Y %H:%M")),
            ("Description", business.description)]
    r = 4
    for label, value in rows:
        ws.cell(row=r, column=1, value=label).font = Font(bold=True, size=10)
        ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor=LIGHT)
        ws.cell(row=r, column=1).border = BOX
        cell = ws.cell(row=r, column=2, value=value)
        cell.border = BOX
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 60


def _accounts_sheet(wb, business):
    rows = []
    accounts = Account.query.filter_by(business_id=business.id).order_by(Account.code).all()
    for a in accounts:
        rows.append({"Code": a.code, "Account Name": a.name, "Category": a.category,
                     "Subcategory": a.subcategory_label,
                     "Normal Balance": a.normal_balance,
                     "Opening Debit": a.opening_debit, "Opening Credit": a.opening_credit,
                     "Status": "Active" if a.is_active else "Inactive",
                     "Description": a.description})
    _sheet(wb, "Chart of Accounts",
           ["Code", "Account Name", "Category", "Subcategory", "Normal Balance",
            "Opening Debit", "Opening Credit", "Status", "Description"],
           rows, money_cols=(6, 7), title_text="Chart of Accounts",
           widths=[10, 34, 14, 30, 14, 15, 15, 10, 40])


def _opening_sheet(wb, business):
    rows = []
    td = tc = ZERO
    for a in Account.query.filter_by(business_id=business.id).order_by(Account.code).all():
        if a.opening_debit == 0 and a.opening_credit == 0:
            continue
        td += a.opening_debit
        tc += a.opening_credit
        rows.append({"Code": a.code, "Account Name": a.name,
                     "Debit": a.opening_debit, "Credit": a.opening_credit})
    _sheet(wb, "Opening Balances", ["Code", "Account Name", "Debit", "Credit"], rows,
           money_cols=(3, 4), title_text="Opening Balances",
           total_row={"Code": "", "Account Name": "Total", "Debit": td, "Credit": tc},
           widths=[12, 40, 18, 18])


def _journal_sheet(wb, business, start, end, title="General Journal", source="journal"):
    data = journal_rows(business.id, start, end, source=source)
    rows = [{"Date": r["date"], "Voucher No": r["voucher_no"],
             "Account Code": r["account_code"], "Account Name": r["account_name"],
             "Description": r["description"], "Debit": r["debit"],
             "Credit": r["credit"], "Narration": r["narration"]} for r in data["rows"]]
    _sheet(wb, title,
           ["Date", "Voucher No", "Account Code", "Account Name", "Description",
            "Debit", "Credit", "Narration"], rows, money_cols=(6, 7), date_cols=(1,),
           title_text=title,
           total_row={"Date": "", "Voucher No": "", "Account Code": "",
                      "Account Name": "Total", "Description": "",
                      "Debit": data["total_debit"], "Credit": data["total_credit"]},
           widths=[12, 14, 14, 32, 38, 16, 16, 32])


def _ledger_sheet(wb, business, start, end):
    ws = wb.create_sheet("General Ledger")
    ws["A1"] = "General Ledger"
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    row = 3
    for acc in Account.query.filter_by(business_id=business.id).order_by(Account.code).all():
        rep = ledger_report(business.id, acc, start, end)
        if not rep["rows"] and rep["opening"] == 0:
            continue
        ws.cell(row=row, column=1,
                value=f"{acc.code} - {acc.name} ({acc.category})").font = Font(bold=True, size=11)
        row += 1
        for i, head in enumerate(["Date", "Particulars", "Voucher", "Debit", "Credit",
                                  "Balance", "Dr/Cr"], start=1):
            cell = ws.cell(row=row, column=i, value=head)
            cell.font = Font(bold=True, color="FFFFFF", size=9)
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.border = BOX
        row += 1
        ws.cell(row=row, column=2, value="Opening Balance")
        ws.cell(row=row, column=6, value=_money(rep["opening"])).number_format = MONEY_FMT
        ws.cell(row=row, column=7, value=rep["opening_side"])
        row += 1
        for r in rep["rows"]:
            ws.cell(row=row, column=1, value=r["date"]).number_format = DATE_FMT
            ws.cell(row=row, column=2, value=r["particulars"])
            ws.cell(row=row, column=3, value=r["voucher_no"])
            ws.cell(row=row, column=4, value=_money(r["debit"])).number_format = MONEY_FMT
            ws.cell(row=row, column=5, value=_money(r["credit"])).number_format = MONEY_FMT
            ws.cell(row=row, column=6, value=_money(r["balance"])).number_format = MONEY_FMT
            ws.cell(row=row, column=7, value=r["balance_side"])
            for c in range(1, 8):
                ws.cell(row=row, column=c).border = BOX
                ws.cell(row=row, column=c).font = Font(size=9)
            row += 1
        ws.cell(row=row, column=2, value="Closing Balance").font = Font(bold=True)
        ws.cell(row=row, column=4, value=_money(rep["total_debit"])).number_format = MONEY_FMT
        ws.cell(row=row, column=5, value=_money(rep["total_credit"])).number_format = MONEY_FMT
        ws.cell(row=row, column=6, value=_money(rep["closing"])).number_format = MONEY_FMT
        ws.cell(row=row, column=7, value=rep["closing_side"]).font = Font(bold=True)
        row += 2
    for letter, w in zip("ABCDEFG", (12, 40, 14, 15, 15, 16, 8)):
        ws.column_dimensions[letter].width = w
    ws.freeze_panes = "A3"


def _trial_balance_sheet(wb, business, start, end):
    tb = trial_balance(business.id, start, end)
    rows = [{"Code": r["code"], "Account Name": r["name"], "Category": r["category"],
             "Debit": r["debit"], "Credit": r["credit"]} for r in tb["rows"]]
    _sheet(wb, "Trial Balance", ["Code", "Account Name", "Category", "Debit", "Credit"],
           rows, money_cols=(4, 5), title_text="Trial Balance",
           total_row={"Code": "", "Account Name": "Total", "Category": "",
                      "Debit": tb["total_debit"], "Credit": tb["total_credit"]},
           widths=[12, 40, 16, 18, 18])
    return tb


def _adjusted_tb_sheet(wb, business, start, end):
    data = adjusted_trial_balance(business.id, start, end)
    rows = [{"Account Name": r["name"], "TB Debit": r["debit"], "TB Credit": r["credit"],
             "Adj Debit": r["adj_debit"], "Adj Credit": r["adj_credit"],
             "Adjusted Debit": r["adjusted_debit"],
             "Adjusted Credit": r["adjusted_credit"]} for r in data["rows"]]
    t = data["totals"]
    _sheet(wb, "Adjusted Trial Balance",
           ["Account Name", "TB Debit", "TB Credit", "Adj Debit", "Adj Credit",
            "Adjusted Debit", "Adjusted Credit"], rows, money_cols=(2, 3, 4, 5, 6, 7),
           title_text="Adjusted Trial Balance",
           total_row={"Account Name": "Total", "TB Debit": t["orig_debit"],
                      "TB Credit": t["orig_credit"], "Adj Debit": t["adj_debit"],
                      "Adj Credit": t["adj_credit"],
                      "Adjusted Debit": t["adjusted_debit"],
                      "Adjusted Credit": t["adjusted_credit"]},
           widths=[38, 15, 15, 15, 15, 16, 16])


def _two_side_sheet(wb, title, debit_items, credit_items, total_debit, total_credit):
    rows = []
    for i in range(max(len(debit_items), len(credit_items))):
        d = debit_items[i] if i < len(debit_items) else None
        c = credit_items[i] if i < len(credit_items) else None
        rows.append({"Debit Side": d["label"] if d else "",
                     "Debit Amount": d["amount"] if d else None,
                     "Credit Side": c["label"] if c else "",
                     "Credit Amount": c["amount"] if c else None})
    _sheet(wb, title, ["Debit Side", "Debit Amount", "Credit Side", "Credit Amount"],
           rows, money_cols=(2, 4), title_text=title,
           total_row={"Debit Side": "Total", "Debit Amount": total_debit,
                      "Credit Side": "Total", "Credit Amount": total_credit},
           widths=[40, 18, 40, 18])


def _pl_sheet(wb, business, start, end):
    pl = profit_and_loss(business.id, start, end)
    _two_side_sheet(wb, "Profit & Loss", pl["debit_items"], pl["credit_items"],
                    pl["total_debit"], pl["total_credit"])


def _bs_sheet(wb, business, start, end):
    bs = balance_sheet(business.id, start, end)
    rows = []
    for item in bs["current_assets"]:
        rows.append({"Section": "Current Assets", "Particulars": item["name"],
                     "Amount": item["amount"]})
    for item in bs["fixed_assets"]:
        rows.append({"Section": "Fixed Assets", "Particulars": item["name"],
                     "Amount": item["amount"]})
    rows.append({"Section": "Total", "Particulars": "Total Assets",
                 "Amount": bs["total_assets"]})
    for item in bs["current_liabilities"]:
        rows.append({"Section": "Current Liabilities", "Particulars": item["name"],
                     "Amount": item["amount"]})
    for item in bs["long_term_liabilities"]:
        rows.append({"Section": "Long Term Liabilities", "Particulars": item["name"],
                     "Amount": item["amount"]})
    rows.append({"Section": "Total", "Particulars": "Total Liabilities",
                 "Amount": bs["total_liabilities"]})
    for item in bs["capital_lines"]:
        rows.append({"Section": "Owner's Equity", "Particulars": item["name"],
                     "Amount": item.get("display_amount", item["amount"])})
    rows.append({"Section": "Total", "Particulars": "Total Owner's Equity",
                 "Amount": bs["total_equity"]})
    rows.append({"Section": "Total", "Particulars": "Liabilities + Equity",
                 "Amount": bs["total_liabilities_equity"]})
    rows.append({"Section": "Check", "Particulars": "Difference",
                 "Amount": bs["difference"]})
    _sheet(wb, "Balance Sheet", ["Section", "Particulars", "Amount"], rows,
           money_cols=(3,), title_text="Balance Sheet",
           widths=[24, 46, 20])


def _closing_sheet(wb, business):
    accounts = {a.id: a for a in Account.query.filter_by(business_id=business.id).all()}
    rows = []
    td = tc = ZERO
    for entry in ClosingEntry.query.filter_by(business_id=business.id).order_by(
            ClosingEntry.entry_date).all():
        for line in entry.lines:
            acc = accounts.get(line.account_id)
            td += line.debit
            tc += line.credit
            rows.append({"Date": entry.entry_date, "Entry No": entry.entry_no,
                         "Status": entry.status,
                         "Account": f"{acc.code} - {acc.name}" if acc else "",
                         "Description": line.description, "Debit": line.debit,
                         "Credit": line.credit})
    _sheet(wb, "Closing Entries",
           ["Date", "Entry No", "Status", "Account", "Description", "Debit", "Credit"],
           rows, money_cols=(6, 7), date_cols=(1,), title_text="Closing Entries",
           total_row={"Date": "", "Entry No": "", "Status": "", "Account": "Total",
                      "Description": "", "Debit": td, "Credit": tc},
           widths=[12, 12, 10, 40, 34, 16, 16])


def _post_closing_sheet(wb, business, start, end):
    tb = post_closing_trial_balance(business.id, start, end)
    rows = [{"Code": r["code"], "Account Name": r["name"], "Category": r["category"],
             "Debit": r["debit"], "Credit": r["credit"]} for r in tb["rows"]]
    rows.append({"Code": "", "Account Name": "Total Debit", "Category": "",
                 "Debit": tb["total_debit"], "Credit": None})
    rows.append({"Code": "", "Account Name": "Total Credit", "Category": "",
                 "Debit": None, "Credit": tb["total_credit"]})
    rows.append({"Code": "", "Account Name": "Difference", "Category": "",
                 "Debit": tb["difference"], "Credit": None})
    rows.append({"Code": "", "Account Name": "Balanced status", "Category": "",
                 "Debit": "Balanced" if tb["is_balanced"] else "Not Balanced",
                 "Credit": None})
    rows.append({"Code": "", "Account Name": "Temporary accounts still open", "Category": "",
                 "Debit": (", ".join(f"{r['code']} {r['name']}"
                                     for r in tb["unclosed_temporary"])
                           or "0 (all closed)"),
                 "Credit": None})
    _sheet(wb, "Post-Closing TB",
           ["Account Code", "Account Name", "Category", "Debit (Rs.)", "Credit (Rs.)"],
           rows, money_cols=(4, 5), title_text="Post-Closing Trial Balance",
           total_row={"Code": "", "Account Name": "Total", "Category": "",
                      "Debit": tb["total_debit"], "Credit": tb["total_credit"]},
           widths=[14, 40, 16, 18, 18])


def _annual_sheet(wb, business, start, end):
    data = analytics.annual_analysis(business.id, start, end)
    cur, prev = data["current"], data["previous"]
    rows = []
    for key, label in (("total_income", "Total Revenue"), ("total_expenses", "Total Expenses"),
                       ("gross_profit", "Gross Profit"), ("net_profit", "Net Profit"),
                       ("total_assets", "Total Assets"),
                       ("total_liabilities", "Total Liabilities"),
                       ("owner_capital", "Owner's Equity"),
                       ("cash_balance", "Cash & Bank"),
                       ("receivable", "Accounts Receivable"),
                       ("payable", "Accounts Payable")):
        rows.append({"Metric": label, "Current Period": cur[key],
                     "Previous Period": prev[key] if data["previous_has_data"] else None,
                     "Growth %": analytics.growth(cur[key], prev[key])
                     if data["previous_has_data"] else None})
    _sheet(wb, "Annual Analysis",
           ["Metric", "Current Period", "Previous Period", "Growth %"], rows,
           money_cols=(2, 3, 4), title_text=f"Annual Analysis - {data['label']}",
           subtitle=None if data["previous_has_data"]
           else "No comparison data available for the previous year.",
           widths=[34, 20, 20, 14])


def _product_sheets(wb, business):
    summary = inventory_summary(business.id)
    rows = [{"Code": p.code, "Product": p.name, "Category": p.category,
             "Purchase Cost": p.purchase_cost, "Selling Price": p.selling_price,
             "Sold Qty": p.net_sold, "Revenue": p.revenue, "COGS": p.cogs,
             "Gross Profit": p.gross_profit, "Margin %": p.margin,
             "Stock": p.available_stock, "Stock Value": p.stock_value}
            for p in summary["products"]]
    _sheet(wb, "Product Sales",
           ["Code", "Product", "Category", "Purchase Cost", "Selling Price", "Sold Qty",
            "Revenue", "COGS", "Gross Profit", "Margin %", "Stock", "Stock Value"],
           rows, money_cols=(4, 5, 7, 8, 9, 10, 12), title_text="Product Sales Performance",
           total_row={"Code": "", "Product": "Total", "Category": "",
                      "Purchase Cost": None, "Selling Price": None,
                      "Sold Qty": sum((p.net_sold for p in summary["products"]), 0),
                      "Revenue": summary["total_revenue"],
                      "COGS": summary["total_cogs"],
                      "Gross Profit": summary["gross_profit"],
                      "Margin %": summary["margin"], "Stock": None,
                      "Stock Value": summary["total_value"]},
           widths=[10, 30, 16, 14, 13, 10, 14, 14, 14, 10, 10, 14])

    inv_rows = []
    for p in summary["products"]:
        inv_rows.append({"Product": p.name, "Opening Qty": p.opening_qty,
                         "Purchased": p.purchase_qty, "Sold": p.sold_qty,
                         "Sales Returns": p.sales_returns,
                         "Purchase Returns": p.purchase_returns,
                         "Available Stock": p.available_stock,
                         "Reorder Level": p.reorder_level,
                         "Stock Value": p.stock_value,
                         "Alert": "LOW STOCK" if p.is_low_stock else ""})
    _sheet(wb, "Inventory",
           ["Product", "Opening Qty", "Purchased", "Sold", "Sales Returns",
            "Purchase Returns", "Available Stock", "Reorder Level", "Stock Value", "Alert"],
           inv_rows, money_cols=(9,), title_text="Inventory Status",
           widths=[30, 12, 12, 10, 13, 15, 15, 13, 14, 12])


def _budget_sheet(wb, business):
    rows = []
    for b in Budget.query.filter_by(business_id=business.id).order_by(Budget.start_date).all():
        a = analytics.budget_analysis(business.id, b)
        rows.append({"Budget": b.name, "Type": b.kind,
                     "From": b.start_date, "To": b.end_date,
                     "Planned Income": a["planned_income"],
                     "Actual Income": a["actual_income"],
                     "Planned Expenses": a["planned_expenses"],
                     "Actual Expenses": a["actual_expenses"],
                     "Variance (Income)": a["income_variance"],
                     "Variance (Expenses)": a["expense_variance"],
                     "Income Utilisation %": a["income_utilization"],
                     "Expense Utilisation %": a["expense_utilization"]})
    _sheet(wb, "Budget Reports",
           ["Budget", "Type", "From", "To", "Planned Income", "Actual Income",
            "Planned Expenses", "Actual Expenses", "Variance (Income)",
            "Variance (Expenses)", "Income Utilisation %", "Expense Utilisation %"],
           rows, money_cols=(5, 6, 7, 8, 9, 10, 11, 12), date_cols=(3, 4),
           title_text="Budget vs Actual",
           widths=[26, 12, 12, 12, 16, 15, 17, 15, 17, 19, 20, 21])


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------
def render_excel(report, business=None):
    """Single-report workbook."""
    wb = Workbook()
    business = business or report["business"]
    first = True
    for sec in report["sections"]:
        heads = [c["label"] for c in sec["columns"]]
        rows = []
        for r in sec["rows"]:
            rows.append({c["label"]: r.get(c["key"]) for c in sec["columns"]})
        money_idx = tuple(i + 1 for i, c in enumerate(sec["columns"])
                          if c["type"] == "money")
        date_idx = tuple(i + 1 for i, c in enumerate(sec["columns"])
                         if c["type"] == "date")
        totals = sec.get("totals_multi") or sec.get("totals")
        total_row = None
        if totals:
            total_row = {}
            for c in sec["columns"]:
                if c["type"] == "money":
                    total_row[c["label"]] = totals.get(c["key"])
                else:
                    total_row[c["label"]] = totals.get("label", "Total") \
                        if c is sec["columns"][0] else ""
        _sheet(wb, sec.get("title", "Report")[:31], heads, rows,
               money_cols=money_idx, date_cols=date_idx,
               title_text=sec.get("title"), subtitle=report["period_label"],
               total_row=total_row, first=first)
        first = False
    if first:
        ws = wb.active
        ws.title = "Report"
        ws["A1"] = "No data available for this period."
    return wb


def render_full_workbook(business, start, end, period_label):
    wb = Workbook()
    _business_sheet(wb, business, start, end, period_label, first=True)
    _accounts_sheet(wb, business)
    _opening_sheet(wb, business)
    _journal_sheet(wb, business, start, end, "General Journal", "journal")
    _ledger_sheet(wb, business, start, end)
    _trial_balance_sheet(wb, business, start, end)
    _journal_sheet(wb, business, start, end, "Adjusting Entries", "adjustments")
    _adjusted_tb_sheet(wb, business, start, end)
    t = trading_account(business.id, start, end)
    _two_side_sheet(wb, "Trading Account", t["debit_items"], t["credit_items"],
                    t["total_debit"], t["total_credit"])
    _pl_sheet(wb, business, start, end)
    _bs_sheet(wb, business, start, end)
    _closing_sheet(wb, business)
    _post_closing_sheet(wb, business, start, end)
    _annual_sheet(wb, business, start, end)
    _product_sheets(wb, business)
    _budget_sheet(wb, business)
    return wb


def to_bytes(wb):
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
