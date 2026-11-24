"""
Business intelligence: dashboard figures, time series, comparisons, growth.
Every number is computed from posted records - there are no placeholders.
"""
from datetime import date, timedelta
from decimal import Decimal

from models import Account, Budget, money, ZERO, SUBCATEGORIES
from accounting.ledger import (account_balances, all_lines, cash_balance,
                               subcategory_items, sum_subcategories)
from accounting.financial_statements import (statement_balances, trading_account,
                                             profit_and_loss, balance_sheet,
                                             cash_flow)


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def month_buckets(start, end):
    """Contiguous month buckets covering start..end."""
    buckets = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        nxt = add_months(cursor, 1)
        b_start = max(cursor, start)
        b_end = min(nxt - timedelta(days=1), end)
        if b_start <= b_end:
            buckets.append((b_start, b_end, cursor.strftime("%b %Y")))
        cursor = nxt
    return buckets


def resolve_range(grain, start=None, end=None, today=None):
    """Turn a granularity keyword into a (start, end, label) range."""
    today = today or date.today()
    if grain == "daily":
        s = e = start or today
        return s, e, s.strftime("%d %b %Y")
    if grain == "weekly":
        anchor = start or today
        s = anchor - timedelta(days=anchor.weekday())
        return s, s + timedelta(days=6), f"Week of {s.strftime('%d %b %Y')}"
    if grain == "monthly":
        anchor = start or today
        s = date(anchor.year, anchor.month, 1)
        return s, add_months(s, 1) - timedelta(days=1), s.strftime("%B %Y")
    if grain == "quarterly":
        anchor = start or today
        q = (anchor.month - 1) // 3
        s = date(anchor.year, q * 3 + 1, 1)
        return s, add_months(s, 3) - timedelta(days=1), f"Q{q + 1} {anchor.year}"
    if grain == "half_yearly":
        anchor = start or today
        s = date(anchor.year, 1 if anchor.month <= 6 else 7, 1)
        return s, add_months(s, 6) - timedelta(days=1), (
            f"H{1 if anchor.month <= 6 else 2} {anchor.year}")
    if grain == "yearly":
        anchor = start or today
        s = date(anchor.year, 1, 1)
        return s, date(anchor.year, 12, 31), str(anchor.year)
    # custom
    s = start or date(today.year, 1, 1)
    e = end or today
    return s, e, f"{s.strftime('%d %b %Y')} - {e.strftime('%d %b %Y')}"


def previous_range(grain, start, end):
    """Immediately preceding comparable range."""
    if grain == "daily":
        s = start - timedelta(days=1)
        return s, s, s.strftime("%d %b %Y")
    if grain == "weekly":
        s = start - timedelta(days=7)
        return s, s + timedelta(days=6), f"Week of {s.strftime('%d %b %Y')}"
    if grain == "monthly":
        s = add_months(start, -1)
        return s, add_months(s, 1) - timedelta(days=1), s.strftime("%B %Y")
    if grain == "quarterly":
        s = add_months(start, -3)
        return s, add_months(s, 3) - timedelta(days=1), (
            f"Q{((s.month - 1) // 3) + 1} {s.year}")
    if grain == "half_yearly":
        s = add_months(start, -6)
        return s, add_months(s, 6) - timedelta(days=1), (
            f"H{1 if s.month <= 6 else 2} {s.year}")
    if grain == "yearly":
        s = date(start.year - 1, 1, 1)
        return s, date(start.year - 1, 12, 31), str(start.year - 1)
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1), "Previous period"


# ---------------------------------------------------------------------------
# Income / expense aggregation from raw lines
# ---------------------------------------------------------------------------
def income_expense(business_id, start, end, accounts=None):
    """
    Income and expenses for a range, matched the same way as the statements:

    * income   = revenue credits - revenue debits (trading contra excluded)
    * expenses = all expense debits - credits
                 + opening stock consumed
                 - closing stock transferred through the trading contra account

    This keeps ``income - expenses`` equal to the Profit & Loss result, so charts
    and the P&L never disagree.
    """
    accounts = accounts or {a.id: a for a in
                            Account.query.filter_by(business_id=business_id).all()}
    income = expenses = ZERO
    lines = all_lines(business_id, start, end, include_adjustments=True)
    for line in lines:
        acc = accounts.get(line["account_id"])
        if not acc:
            continue
        if acc.category == "Revenue":
            if acc.subcategory == "trading_contra":
                expenses -= (line["credit"] - line["debit"])
            else:
                income += line["credit"] - line["debit"]
        elif acc.category == "Expenses":
            expenses += line["debit"] - line["credit"]
        elif acc.subcategory == "opening_stock":
            expenses += line["debit"] - line["credit"]
    return money(income), money(expenses)


def sales_expense_by_month(business_id, start, end):
    accounts = {a.id: a for a in Account.query.filter_by(business_id=business_id).all()}
    buckets = month_buckets(start, end)
    labels, sales, expenses, profit, income = [], [], [], [], []
    for b_start, b_end, label in buckets:
        inc, exp = income_expense(business_id, b_start, b_end, accounts)
        sales_v = ZERO
        for line in all_lines(business_id, b_start, b_end, include_adjustments=True):
            acc = accounts.get(line["account_id"])
            if acc and acc.subcategory == "sales":
                sales_v += line["credit"] - line["debit"]
        labels.append(label)
        sales.append(float(money(sales_v)))
        income.append(float(inc))
        expenses.append(float(exp))
        profit.append(float(money(inc - exp)))
    return {"labels": labels, "sales": sales, "income": income,
            "expenses": expenses, "profit": profit}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def dashboard_summary(business_id, start, end):
    bal = statement_balances(business_id, start, end)
    trading = trading_account(business_id, start, end, bal)
    pl = profit_and_loss(business_id, start, end, bal, trading)
    bs = balance_sheet(business_id, start, end, bal, pl)

    income = money(trading["net_sales"] + pl["other_income"])
    # cost of goods sold + operating expenses -> income - expenses = net profit
    expenses = money(trading["cogs"] + pl["expense_total"])
    ar = sum_subcategories(bal, ("accounts_receivable",), "debit")
    ap = sum_subcategories(bal, ("accounts_payable",), "credit")
    cash = money(sum_subcategories(bal, ("cash",), "debit")
                 + sum_subcategories(bal, ("bank",), "debit"))

    return {
        "total_income": income,
        "total_expenses": expenses,
        "total_assets": bs["total_assets"],
        "total_liabilities": bs["total_liabilities"],
        "owner_capital": bs["total_equity"],
        "gross_profit": trading["gross_profit"],
        "gross_loss": trading["gross_loss"],
        "is_gross_profit": trading["is_profit"],
        "net_profit": pl["net_profit"],
        "net_loss": pl["net_loss"],
        "is_profit": pl["is_profit"],
        "cash_balance": cash,
        "receivable": ar,
        "payable": ap,
        "trading": trading,
        "pl": pl,
        "bs": bs,
        "has_data": bs["has_activity"] or pl["has_activity"] or income != 0
                    or expenses != 0,
    }


def growth(current, previous):
    """Percentage growth; ``None`` when there is no comparable base."""
    if previous in (None, 0):
        return None
    return money((Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100)


def period_comparison(business_id, grain, start=None, end=None):
    s, e, label = resolve_range(grain, start, end)
    ps, pe, plabel = previous_range(grain, s, e)

    cur = dashboard_summary(business_id, s, e)
    prev = dashboard_summary(business_id, ps, pe)

    metrics = []
    for key, title in (("total_income", "Income"), ("total_expenses", "Expenses"),
                       ("gross_profit", "Gross Profit"), ("net_profit", "Net Profit"),
                       ("cash_balance", "Cash & Bank"), ("receivable", "Receivables"),
                       ("payable", "Payables"), ("total_assets", "Total Assets")):
        metrics.append({
            "key": key, "title": title,
            "current": cur[key], "previous": prev[key],
            "change": money(cur[key] - prev[key]),
            "growth": growth(cur[key], prev[key]),
            "has_previous": prev["has_data"] and prev[key] != 0,
        })

    return {"start": s, "end": e, "label": label,
            "prev_start": ps, "prev_end": pe, "prev_label": plabel,
            "current": cur, "previous": prev, "metrics": metrics,
            "previous_has_data": prev["has_data"]}


def annual_analysis(business_id, start=None, end=None):
    s, e, label = resolve_range("yearly", start or end or date.today())
    cur = dashboard_summary(business_id, s, e)
    prev_s, prev_e, prev_label = previous_range("yearly", s, e)
    prev = dashboard_summary(business_id, prev_s, prev_e)
    cf = cash_flow(business_id, s, e)
    series = sales_expense_by_month(business_id, s, e)
    return {
        "start": s, "end": e, "label": label,
        "previous": prev, "prev_label": prev_label,
        "current": cur, "cash_flow": cf, "series": series,
        "revenue_growth": growth(cur["total_income"], prev["total_income"]),
        "profit_growth": growth(cur["net_profit"], prev["net_profit"]),
        "expense_growth": growth(cur["total_expenses"], prev["total_expenses"]),
        "previous_has_data": prev["has_data"],
    }


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
def budget_analysis(business_id, budget):
    actual_income, actual_expenses = income_expense(business_id, budget.start_date,
                                                    budget.end_date)
    planned_income = money(budget.planned_income)
    planned_expenses = money(budget.planned_expenses)
    income_var = money(actual_income - planned_income)
    expense_var = money(actual_expenses - planned_expenses)
    return {
        "budget": budget,
        "actual_income": actual_income,
        "actual_expenses": actual_expenses,
        "planned_income": planned_income,
        "planned_expenses": planned_expenses,
        "income_variance": income_var,
        "expense_variance": expense_var,
        "income_utilization": (money(actual_income / planned_income * 100)
                               if planned_income else None),
        "expense_utilization": (money(actual_expenses / planned_expenses * 100)
                                if planned_expenses else None),
        "actual_profit": money(actual_income - actual_expenses),
        "planned_profit": money(planned_income - planned_expenses),
    }


def product_performance(business_id):
    from models import Product
    products = Product.query.filter_by(business_id=business_id).all()
    rows = [{"name": p.name, "revenue": p.revenue, "profit": p.gross_profit,
             "stock": p.available_stock} for p in products]
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return rows
