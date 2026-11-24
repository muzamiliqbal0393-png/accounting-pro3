"""
Trading Account, Profit & Loss Account, Balance Sheet and Cash Flow Statement.

All statements are derived from posted vouchers plus posted adjustments.
Nothing is ever inserted to force a figure to balance.
"""
from decimal import Decimal

from models import Account, money, ZERO
from accounting.ledger import (account_balances, subcategory_items,
                               sum_subcategories, all_lines, cash_accounts)


def statement_balances(business_id, start=None, end=None):
    """Balances used by the financial statements (opening + journal + adjustments)."""
    return account_balances(business_id, start, end, include_adjustments=True,
                            include_closing=False, include_opening=True)


# ---------------------------------------------------------------------------
# Trading Account
# ---------------------------------------------------------------------------
def trading_account(business_id, start=None, end=None, bal=None):
    bal = bal or statement_balances(business_id, start, end)

    opening_stock = sum_subcategories(bal, ("opening_stock",), "debit")
    purchases = sum_subcategories(bal, ("purchases",), "debit")
    purchase_returns = sum_subcategories(bal, ("purchase_returns",), "credit")
    direct_items = subcategory_items(bal, ("direct_expenses",), "debit")
    direct_total = money(sum((i["amount"] for i in direct_items), ZERO))

    sales = sum_subcategories(bal, ("sales",), "credit")
    sales_returns = sum_subcategories(bal, ("sales_returns",), "debit")
    closing_items = subcategory_items(bal, ("inventory",), "debit")
    closing_stock = money(sum((i["amount"] for i in closing_items), ZERO))

    net_sales = money(sales - sales_returns)
    net_purchases = money(purchases - purchase_returns)
    cogs = money(opening_stock + net_purchases + direct_total - closing_stock)
    gross_profit = money(net_sales - cogs)
    is_profit = gross_profit >= 0

    debit_items = [{"label": "Opening Stock", "amount": opening_stock}]
    debit_items.append({"label": "Purchases", "amount": purchases})
    if purchase_returns:
        debit_items.append({"label": "Less: Purchase Returns", "amount": -purchase_returns})
    for item in direct_items:
        debit_items.append({"label": item["account"].name, "amount": item["amount"]})
    debit_items.append({
        "label": "Gross Profit c/d" if is_profit else "Gross Loss c/d",
        "amount": abs(gross_profit), "emphasis": True})

    credit_items = [{"label": "Sales", "amount": sales}]
    if sales_returns:
        credit_items.append({"label": "Less: Sales Returns", "amount": -sales_returns})
    for item in closing_items:
        credit_items.append({"label": f"Closing Stock - {item['account'].name}",
                             "amount": item["amount"]})
    if not is_profit:
        credit_items.append({"label": "Gross Loss c/d", "amount": abs(gross_profit),
                             "emphasis": True})

    total_debit = money(sum((i["amount"] for i in debit_items), ZERO))
    total_credit = money(sum((i["amount"] for i in credit_items), ZERO))

    return {
        "debit_items": debit_items,
        "credit_items": credit_items,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "is_balanced": total_debit == total_credit,
        "opening_stock": opening_stock,
        "purchases": purchases,
        "purchase_returns": purchase_returns,
        "direct_total": direct_total,
        "sales": sales,
        "sales_returns": sales_returns,
        "net_sales": net_sales,
        "net_purchases": net_purchases,
        "closing_stock": closing_stock,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "gross_loss": ZERO if is_profit else abs(gross_profit),
        "is_profit": is_profit,
        "has_activity": any(v != 0 for v in (sales, purchases, opening_stock,
                                             closing_stock, direct_total)),
    }


# ---------------------------------------------------------------------------
# Profit & Loss
# ---------------------------------------------------------------------------
def profit_and_loss(business_id, start=None, end=None, bal=None, trading=None):
    bal = bal or statement_balances(business_id, start, end)
    trading = trading or trading_account(business_id, start, end, bal)

    other_income_items = subcategory_items(bal, ("other_income",), "credit")
    other_income = money(sum((i["amount"] for i in other_income_items), ZERO))
    indirect_items = subcategory_items(bal, ("indirect_expenses",), "debit")
    depreciation_items = subcategory_items(bal, ("depreciation",), "debit")
    bad_debt_items = subcategory_items(bal, ("bad_debts",), "debit")

    indirect_total = money(sum((i["amount"] for i in indirect_items), ZERO))
    depreciation_total = money(sum((i["amount"] for i in depreciation_items), ZERO))
    bad_debt_total = money(sum((i["amount"] for i in bad_debt_items), ZERO))
    expense_total = money(indirect_total + depreciation_total + bad_debt_total)

    gross = trading["gross_profit"] if trading["is_profit"] else -trading["gross_loss"]
    net_profit = money(gross + other_income - expense_total)
    is_profit = net_profit >= 0

    debit_items = []
    if not trading["is_profit"]:
        debit_items.append({"label": "Gross Loss b/d", "amount": trading["gross_loss"]})
    debit_items += [{"label": i["account"].name, "amount": i["amount"]}
                    for i in indirect_items]
    debit_items += [{"label": i["account"].name, "amount": i["amount"]}
                    for i in depreciation_items]
    debit_items += [{"label": i["account"].name, "amount": i["amount"]}
                    for i in bad_debt_items]
    if is_profit:
        debit_items.append({"label": "Net Profit transferred to Capital",
                            "amount": net_profit, "emphasis": True})

    credit_items = []
    if trading["is_profit"]:
        credit_items.append({"label": "Gross Profit b/d", "amount": trading["gross_profit"]})
    credit_items += [{"label": i["account"].name, "amount": i["amount"]}
                     for i in other_income_items]
    if not is_profit:
        credit_items.append({"label": "Net Loss transferred to Capital",
                             "amount": abs(net_profit), "emphasis": True})

    total_debit = money(sum((i["amount"] for i in debit_items), ZERO))
    total_credit = money(sum((i["amount"] for i in credit_items), ZERO))

    return {
        "debit_items": debit_items,
        "credit_items": credit_items,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "is_balanced": total_debit == total_credit,
        "gross_profit": trading["gross_profit"],
        "other_income": other_income,
        "other_income_items": other_income_items,
        "indirect_total": indirect_total,
        "indirect_items": indirect_items,
        "depreciation_total": depreciation_total,
        "depreciation_items": depreciation_items,
        "bad_debt_total": bad_debt_total,
        "bad_debt_items": bad_debt_items,
        "expense_total": expense_total,
        "total_expenses": money(expense_total + (trading["direct_total"]
                                                 if trading["has_activity"] else ZERO)),
        "net_profit": net_profit,
        "net_loss": ZERO if is_profit else abs(net_profit),
        "is_profit": is_profit,
        "has_activity": trading["has_activity"] or expense_total != 0 or other_income != 0,
    }


# ---------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------
def balance_sheet(business_id, start=None, end=None, bal=None, pl=None):
    bal = bal or statement_balances(business_id, start, end)
    pl = pl or profit_and_loss(business_id, start, end, bal)

    def group(items):
        return [{"name": i["account"].name, "code": i["account"].code,
                 "amount": i["amount"]} for i in items]

    cash_items = subcategory_items(bal, ("cash",), "debit")
    bank_items = subcategory_items(bal, ("bank",), "debit")
    ar_items = subcategory_items(bal, ("accounts_receivable",), "debit")
    stock_items = subcategory_items(bal, ("inventory",), "debit")
    fixed_items = subcategory_items(bal, ("fixed_asset",), "debit")
    other_assets = subcategory_items(bal, ("other_asset",), "debit")

    current_assets = group(cash_items) + group(bank_items) + group(ar_items) + group(stock_items) + group(other_assets)
    fixed_assets = group(fixed_items)

    total_current = money(sum((i["amount"] for i in cash_items + bank_items + ar_items
                               + stock_items + other_assets), ZERO))
    total_fixed = money(sum((i["amount"] for i in fixed_items), ZERO))
    total_assets = money(total_current + total_fixed)

    ap_items = subcategory_items(bal, ("accounts_payable",), "credit")
    outstanding_items = subcategory_items(bal, ("outstanding_expenses",), "credit")
    loan_items = subcategory_items(bal, ("loan",), "credit")
    other_liab = subcategory_items(bal, ("other_liability",), "credit")

    current_liabilities = group(ap_items) + group(outstanding_items) + group(other_liab)
    long_term_liabilities = group(loan_items)
    total_current_liab = money(sum((i["amount"] for i in ap_items + outstanding_items
                                    + other_liab), ZERO))
    total_long_term = money(sum((i["amount"] for i in loan_items), ZERO))
    total_liabilities = money(total_current_liab + total_long_term)

    capital_items = subcategory_items(bal, ("capital",), "credit")
    retained_items = subcategory_items(bal, ("retained_earnings",), "credit")
    drawings_items = subcategory_items(bal, ("drawings",), "debit")

    capital_total = money(sum((i["amount"] for i in capital_items), ZERO))
    retained_total = money(sum((i["amount"] for i in retained_items), ZERO))
    drawings_total = money(sum((i["amount"] for i in drawings_items), ZERO))
    net_result = pl["net_profit"] if pl["is_profit"] else -pl["net_loss"]

    capital_lines = group(capital_items)
    capital_lines.append({"name": "Add: Net Profit for the period" if net_result >= 0
                          else "Less: Net Loss for the period", "code": "", "amount": net_result})
    if drawings_total:
        capital_lines.append({"name": "Less: Drawings", "code": "", "amount": -drawings_total})
    if retained_total:
        capital_lines.append({"name": "Add: Retained Earnings", "code": "", "amount": retained_total})
    # Presentation: rows labelled "Less: ..." are shown as positive magnitudes so the
    # statement reads "Less: Drawings   30,000.00" rather than a double negative.
    # The signed `amount` is kept for any arithmetic consumer.
    for line in capital_lines:
        amt = line.get("amount")
        if amt is not None and line["name"].startswith("Less:"):
            line["display_amount"] = money(abs(amt))
        else:
            line["display_amount"] = amt

    total_equity = money(capital_total + retained_total - drawings_total + net_result)
    total_liab_equity = money(total_liabilities + total_equity)
    difference = money(total_assets - total_liab_equity)

    return {
        "current_assets": current_assets,
        "fixed_assets": fixed_assets,
        "total_current_assets": total_current,
        "total_fixed_assets": total_fixed,
        "total_assets": total_assets,
        "current_liabilities": current_liabilities,
        "long_term_liabilities": long_term_liabilities,
        "total_current_liabilities": total_current_liab,
        "total_long_term_liabilities": total_long_term,
        "total_liabilities": total_liabilities,
        "capital_lines": capital_lines,
        "capital_total": capital_total,
        "drawings_total": drawings_total,
        "retained_total": retained_total,
        "net_result": net_result,
        "total_equity": total_equity,
        "total_liabilities_equity": total_liab_equity,
        "difference": difference,
        "is_balanced": difference == 0,
        "has_activity": any(v != 0 for v in (total_assets, total_liabilities, total_equity)),
    }


# ---------------------------------------------------------------------------
# Cash Flow
# ---------------------------------------------------------------------------
INVESTING_SUBS = ("fixed_asset",)
FINANCING_SUBS = ("loan", "capital", "drawings", "retained_earnings")


def cash_flow(business_id, start=None, end=None):
    """Direct cash flow statement from cash & bank account movements."""
    accounts = cash_accounts(business_id)
    cash_ids = {a.id for a in accounts}
    acct_map = {a.id: a for a in Account.query.filter_by(business_id=business_id).all()}

    lines = all_lines(business_id, start, end, include_adjustments=True,
                      include_closing=False)
    by_voucher = {}
    for line in lines:
        by_voucher.setdefault((line["voucher_no"], line["date"]), []).append(line)

    opening = ZERO
    for acc in accounts:
        opening += money(acc.opening_net)

    operating = financing = investing = ZERO
    details = {"Operating": [], "Investing": [], "Financing": []}
    inflow = outflow = ZERO

    for (voucher_no, dt), group in sorted(by_voucher.items(), key=lambda kv: kv[0][1]):
        cash_amount = ZERO
        for line in group:
            if line["account_id"] in cash_ids:
                cash_amount += line["debit"] - line["credit"]
        if cash_amount == 0:
            continue
        counters = [l for l in group if l["account_id"] not in cash_ids]
        subs = {acct_map[l["account_id"]].subcategory
                for l in counters if l["account_id"] in acct_map}
        if subs & set(INVESTING_SUBS):
            bucket = "Investing"
        elif subs & set(FINANCING_SUBS):
            bucket = "Financing"
        else:
            bucket = "Operating"
        label = counters[0]["description"] if counters else "Cash movement"
        details[bucket].append({"date": dt, "voucher_no": voucher_no,
                                "particulars": label or voucher_no,
                                "amount": money(cash_amount)})
        if bucket == "Operating":
            operating += cash_amount
        elif bucket == "Investing":
            investing += cash_amount
        else:
            financing += cash_amount
        if cash_amount > 0:
            inflow += cash_amount
        else:
            outflow += -cash_amount

    net_change = money(operating + investing + financing)
    closing = money(opening + net_change)
    return {
        "accounts": accounts,
        "opening": money(opening),
        "operating": money(operating),
        "investing": money(investing),
        "financing": money(financing),
        "net_change": net_change,
        "closing": closing,
        "inflow": money(inflow),
        "outflow": money(outflow),
        "details": details,
        "reconciles": closing == money(opening + net_change),
    }
