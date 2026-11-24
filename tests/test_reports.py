"""Financial statement tests: adjustments, trading, P&L, balance sheet, closing."""
from decimal import Decimal

from conftest import load_accounts, post_entry, register, setup_business


def test_adjustment_must_balance(client, app):
    import pytest
    from accounting.adjustments import create_adjustment
    from accounting.journal import JournalError
    from models import Adjustment, Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        with pytest.raises(JournalError):
            create_adjustment(business.id, user.id,
                              {"adj_date": "2026-03-31", "kind": "Depreciation"},
                              [{"account_id": str(accounts["5080"]), "debit": "5000",
                                "credit": "0"},
                               {"account_id": str(accounts["1050"]), "debit": "0",
                                "credit": "4000"}], status="posted")
        assert Adjustment.query.count() == 0


def test_adjusted_trial_balance_adds_adjustments(client, seeded):
    from accounting.trial_balance import adjusted_trial_balance
    with seeded["client"].application.app_context():
        data = adjusted_trial_balance(seeded["business_id"])
        assert data["totals"]["is_balanced"]
        rows = {r["code"]: r for r in data["rows"]}
        assert rows["1030"]["adj_debit"] == Decimal("30000.00")
        assert rows["1030"]["adjusted_debit"] == Decimal("30000.00")
        assert rows["4030"]["adj_credit"] == Decimal("30000.00")


def test_adjusted_tb_adjustment_column_holds_only_adjustments(client, seeded):
    """Regression: the Adj Dr / Adj Cr columns must show adjusting-entry amounts
    only - never the ordinary voucher turnover - and each account may appear on
    one side of the adjusted balance."""
    from accounting.trial_balance import adjusted_trial_balance
    from models import Adjustment
    with seeded["client"].application.app_context():
        data = adjusted_trial_balance(seeded["business_id"])
        rows = {r["code"]: r for r in data["rows"]}
        # the only posted adjustment is Dr 1030 Closing Stock / Cr 4030 Trading A/c
        assert data["totals"]["adj_debit"] == Decimal("30000.00")
        assert data["totals"]["adj_credit"] == Decimal("30000.00")
        # cash was never adjusted, even though it carries a large voucher turnover
        assert rows["1000"]["adj_debit"] == Decimal("0.00")
        assert rows["1000"]["adj_credit"] == Decimal("0.00")
        assert rows["1000"]["adjusted_debit"] == rows["1000"]["debit"]
        # an account is never shown on both sides of the adjusted balance
        for row in data["rows"]:
            assert not (row["adjusted_debit"] > 0 and row["adjusted_credit"] > 0), row["code"]
        # adjustments net off against the account they were posted to
        assert rows["4030"]["adjusted_credit"] == Decimal("30000.00")
        assert data["totals"]["adjusted_debit"] == data["totals"]["adjusted_credit"]
        assert Adjustment.query.filter_by(status="posted").count() == 1


def test_trading_account_calculation(client, seeded):
    from accounting.financial_statements import trading_account
    with seeded["client"].application.app_context():
        t = trading_account(seeded["business_id"])
        assert t["sales"] == Decimal("250000.00")
        assert t["purchases"] == Decimal("100000.00")
        assert t["closing_stock"] == Decimal("30000.00")
        assert t["net_sales"] == Decimal("250000.00")
        assert t["cogs"] == Decimal("70000.00")     # 0 + 100000 - 30000
        assert t["gross_profit"] == Decimal("180000.00")
        assert t["is_profit"] is True
        assert t["total_debit"] == t["total_credit"]


def test_profit_and_loss_calculation(client, seeded):
    from accounting.financial_statements import profit_and_loss
    with seeded["client"].application.app_context():
        pl = profit_and_loss(seeded["business_id"])
        assert pl["gross_profit"] == Decimal("180000.00")
        assert pl["expense_total"] == Decimal("20000.00")   # rent
        assert pl["net_profit"] == Decimal("160000.00")
        assert pl["is_profit"] is True
        assert pl["total_debit"] == pl["total_credit"]


def test_balance_sheet_balances(client, seeded):
    from accounting.financial_statements import balance_sheet
    with seeded["client"].application.app_context():
        bs = balance_sheet(seeded["business_id"])
        assert bs["total_assets"] == Decimal("660000.00")   # cash 630000 + stock 30000
        assert bs["total_liabilities"] == Decimal("0.00")
        assert bs["total_equity"] == Decimal("660000.00")   # 500000 capital + 160000 profit
        assert bs["is_balanced"] is True
        assert bs["difference"] == Decimal("0.00")


def test_balance_sheet_shows_difference_when_unbalanced(client, app):
    """An unbalanced ledger position must be reported, never silently plugged."""
    from accounting.financial_statements import balance_sheet
    from models import Account, Business
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        # Only an asset is introduced (impossible in real double entry, done directly)
        cash = Account.query.filter_by(code="1000").first()
        cash.opening_debit = Decimal("10000")
        from extensions import db
        db.session.commit()
        bs = balance_sheet(Business.query.first().id)
        assert bs["is_balanced"] is False
        assert bs["difference"] == Decimal("10000.00")


def test_closing_entries_and_post_closing_tb(client, seeded):
    from accounting.closing import (create_closing_entry, preview_closing,
                                    post_closing_entry)
    from accounting.trial_balance import post_closing_trial_balance
    with seeded["client"].application.app_context():
        preview = preview_closing(seeded["business_id"])
        assert preview["is_balanced"] is True
        assert preview["net_result"] == Decimal("160000.00")
        assert preview["reconciles"] is True

        entry = create_closing_entry(seeded["business_id"], seeded["user_id"],
                                     None, None, entry_date="2026-03-31")
        post_closing_entry(entry)

        tb = post_closing_trial_balance(seeded["business_id"])
        assert tb["is_balanced"] is True
        assert tb["total_debit"] == Decimal("660000.00")
        assert tb["total_credit"] == Decimal("660000.00")
        assert tb["unclosed_temporary"] == []
        codes = {r["code"] for r in tb["rows"]}
        assert "4000" not in codes      # sales closed
        assert "5000" not in codes      # purchases closed
        assert "3000" in codes          # capital carries 660,000


def test_cash_flow_statement(client, seeded):
    from accounting.financial_statements import cash_flow
    with seeded["client"].application.app_context():
        cf = cash_flow(seeded["business_id"])
        assert cf["opening"] == Decimal("0.00")
        assert cf["closing"] == Decimal("630000.00")
        assert cf["net_change"] == Decimal("630000.00")
        assert cf["reconciles"] is True
        # capital introduced is a financing inflow
        financing_total = sum((d["amount"] for d in cf["details"]["Financing"]),
                              Decimal("0"))
        assert financing_total == Decimal("500000.00")


def test_dashboard_and_report_pages_render(client, seeded):
    for url in ["/dashboard", "/journal", "/trial-balance", "/adjusted-trial-balance",
                "/trading-account", "/profit-loss", "/balance-sheet", "/closing-entries",
                "/post-closing-trial-balance", "/cash-flow", "/analysis", "/adjustments",
                "/ledger", "/accounts", "/transactions", "/business"]:
        resp = seeded["client"].get(url)
        assert resp.status_code == 200, f"{url} failed"
    resp = seeded["client"].get("/ledger?account_id=1")
    assert resp.status_code == 200


def test_reports_show_zero_when_empty(client, app):
    register(client)
    setup_business(client)
    load_accounts(app)
    resp = client.get("/trial-balance")
    assert resp.status_code == 200
    assert b"Rs. 0.00" in resp.data
    resp = client.get("/dashboard")
    assert b"Rs. 0.00" in resp.data
    assert b"No transactions yet" in resp.data
