"""Core double-entry accounting tests."""
from decimal import Decimal

import pytest

from conftest import load_accounts, post_entry, register, setup_business


def test_account_creation_and_duplicate_code(client, app):
    register(client)
    setup_business(client)
    resp = client.post("/accounts/new", data={
        "code": "1110", "name": "Petty Cash", "category": "Assets",
        "subcategory": "cash", "opening_debit": "5000", "opening_credit": "0",
        "is_active": "on"}, follow_redirects=True)
    assert b"created" in resp.data
    resp = client.post("/accounts/new", data={
        "code": "1110", "name": "Duplicate", "category": "Assets",
        "subcategory": "cash"}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_load_standard_chart(client, app):
    from models import Account
    register(client)
    setup_business(client)
    client.post("/accounts/load-default", follow_redirects=True)
    with app.app_context():
        assert Account.query.count() >= 30
        codes = {a.code for a in Account.query.all()}
        assert {"1000", "4000", "5000", "3000"}.issubset(codes)


def test_opening_balance_only_debit_or_credit(client, app):
    register(client)
    setup_business(client)
    resp = client.post("/accounts/new", data={
        "code": "1200", "name": "Both Sides", "category": "Assets",
        "subcategory": "other_asset", "opening_debit": "100",
        "opening_credit": "100"}, follow_redirects=True)
    assert b"either an opening debit or an opening credit" in resp.data


def test_transaction_requires_balanced_entry(client, app):
    from accounting.journal import JournalError, create_entry
    from models import Business, JournalEntry, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        with pytest.raises(JournalError) as exc:
            create_entry(business.id, user.id,
                         {"entry_date": "2026-01-05"},
                         [{"account_id": str(accounts["1000"]), "debit": "1000", "credit": "0"},
                          {"account_id": str(accounts["4000"]), "debit": "0", "credit": "900"}],
                         status="posted")
        assert "not balanced" in str(exc.value)
        assert JournalEntry.query.count() == 0


def test_transaction_rejects_negative_amounts(client, app):
    from accounting.journal import JournalError, create_entry
    from models import Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        with pytest.raises(JournalError):
            create_entry(business.id, user.id, {"entry_date": "2026-01-05"},
                         [{"account_id": str(accounts["1000"]), "debit": "-50", "credit": "0"},
                          {"account_id": str(accounts["4000"]), "debit": "0", "credit": "-50"}],
                         status="posted")


def test_duplicate_voucher_number_rejected(client, app):
    from accounting.journal import JournalError
    from models import Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        post_entry(app, business.id, user.id, "JV-0001",
                   [(accounts["1000"], "100", "0", ""), (accounts["3000"], "0", "100", "")])
        with pytest.raises(JournalError) as exc:
            post_entry(app, business.id, user.id, "JV-0001",
                       [(accounts["1000"], "100", "0", ""),
                        (accounts["3000"], "0", "100", "")])
        assert "already exists" in str(exc.value)


def test_posted_voucher_is_immutable(client, app):
    from accounting.journal import JournalError, delete_draft, update_draft
    from models import Business, JournalEntry, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        entry = post_entry(app, business.id, user.id, "JV-0001",
                           [(accounts["1000"], "100", "0", ""),
                            (accounts["3000"], "0", "100", "")])
        with pytest.raises(JournalError):
            delete_draft(entry)
        with pytest.raises(JournalError):
            update_draft(entry, {"voucher_no": "X"}, [])


def test_compound_journal_entry(client, app):
    from accounting.ledger import ledger_rows
    from models import Account, Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        entry = post_entry(app, business.id, user.id, "JV-0001",
                           [(accounts["1000"], "60000", "0", "Cash"),
                            (accounts["1050"], "50000", "0", "Furniture"),
                            (accounts["2000"], "0", "10000", "Payable"),
                            (accounts["3000"], "0", "100000", "Capital")],
                           entry_type="General Journal")
        assert entry.total_debit == Decimal("110000.00")
        assert entry.total_credit == Decimal("110000.00")
        assert entry.is_balanced
        ledger = ledger_rows(business.id, Account.query.filter_by(code="1000").first(),
                             None, None)
        assert ledger["closing"] == Decimal("60000.00")


def test_credit_purchase_and_credit_sale(client, app):
    from accounting.trial_balance import trial_balance
    from models import Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        post_entry(app, business.id, user.id, "JV-0001",
                   [(accounts["5000"], "40000", "0", "Credit purchase"),
                    (accounts["2000"], "0", "40000", "Credit purchase")],
                   entry_type="Credit Purchase")
        post_entry(app, business.id, user.id, "JV-0002",
                   [(accounts["1020"], "70000", "0", "Credit sale"),
                    (accounts["4000"], "0", "70000", "Credit sale")],
                   entry_type="Credit Sale")
        tb = trial_balance(business.id)
        assert tb["is_balanced"]
        rows = {r["code"]: r for r in tb["rows"]}
        assert rows["1020"]["debit"] == Decimal("70000.00")
        assert rows["2000"]["credit"] == Decimal("40000.00")
        assert rows["4000"]["credit"] == Decimal("70000.00")


def test_expense_and_capital_entries(client, app):
    from accounting.trial_balance import trial_balance
    from models import Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        post_entry(app, business.id, user.id, "JV-0001",
                   [(accounts["1000"], "100000", "0", "Capital"),
                    (accounts["3000"], "0", "100000", "Capital")], entry_type="Capital")
        post_entry(app, business.id, user.id, "JV-0002",
                   [(accounts["5050"], "15000", "0", "Rent"),
                    (accounts["1000"], "0", "15000", "Rent")], entry_type="Expense")
        tb = trial_balance(business.id)
        rows = {r["code"]: r for r in tb["rows"]}
        assert rows["1000"]["debit"] == Decimal("85000.00")
        assert rows["5050"]["debit"] == Decimal("15000.00")
        assert tb["total_debit"] == tb["total_credit"]


def test_draft_and_post_workflow(client, app):
    from models import Business, JournalEntry, User
    from conftest import load_accounts
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        from accounting.journal import create_entry
        entry = create_entry(business.id, user.id,
                            {"voucher_no": "JV-0009", "entry_date": "2026-01-20",
                             "entry_type": "General Journal", "description": "Draft test"},
                            [{"account_id": str(accounts["1000"]), "debit": "500", "credit": "0"},
                             {"account_id": str(accounts["3000"]), "debit": "0", "credit": "500"}],
                            status="draft")
        draft_id = entry.id
    resp = client.post(f"/transactions/{draft_id}/post", follow_redirects=True)
    assert b"posted" in resp.data
    with app.app_context():
        assert JournalEntry.query.get(draft_id).status == "posted"


def test_reversal_entry(client, app):
    from accounting.trial_balance import trial_balance
    from models import Business, JournalEntry, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        user = User.query.first()
        business = Business.query.first()
        entry = post_entry(app, business.id, user.id, "JV-0001",
                           [(accounts["5050"], "5000", "0", "Rent"),
                            (accounts["1000"], "0", "5000", "Rent")])
        entry_id = entry.id
        business_id = business.id
    resp = client.post(f"/transactions/{entry_id}/reverse",
                       data={"reason": "wrong amount"}, follow_redirects=True)
    assert b"Reversal voucher" in resp.data
    with app.app_context():
        entry = JournalEntry.query.get(entry_id)
        assert entry.status == "reversed"
        reversal = JournalEntry.query.filter_by(reversal_of_id=entry_id).first()
        assert reversal is not None
        assert reversal.status == "posted"
        tb = trial_balance(business_id)
        assert tb["total_debit"] == tb["total_credit"]
        # net effect of both entries is zero
        assert all(r["debit"] == 0 and r["credit"] == 0 for r in tb["rows"]) or tb["rows"] == []


def test_ledger_running_balance(client, seeded):
    from models import Account, Business
    from accounting.ledger import ledger_rows
    with seeded["client"].application.app_context():
        business = Business.query.first()
        cash = Account.query.filter_by(code="1000").first()
        report = ledger_rows(business.id, cash, None, None)
        # 500000 - 100000 + 250000 - 20000
        assert report["closing"] == Decimal("630000.00")
        assert report["closing_side"] == "Dr"
        # running balance is monotonic in the order of entry dates
        assert report["rows"][0]["balance"] == Decimal("500000.00")


def test_general_journal_totals(client, seeded):
    resp = seeded["client"].get("/journal")
    assert resp.status_code == 200
    assert b"JV-0001" in resp.data
    assert b"Balanced" in resp.data


def test_accounts_page_and_search(client, seeded):
    resp = seeded["client"].get("/accounts?q=cash")
    assert resp.status_code == 200
    assert b"Cash in Hand" in resp.data
    resp = seeded["client"].get("/accounts?category=Expenses")
    assert b"Purchases" in resp.data


def test_opening_balances_in_trial_balance(client, app):
    from accounting.trial_balance import trial_balance
    from models import Account, Business, User
    register(client)
    setup_business(client)
    accounts = load_accounts(app)
    with app.app_context():
        cash = Account.query.filter_by(code="1000").first()
        capital = Account.query.filter_by(code="3000").first()
        cash.opening_debit = Decimal("25000")
        capital.opening_credit = Decimal("25000")
        from extensions import db
        db.session.commit()
        tb = trial_balance(Business.query.first().id)
        assert tb["is_balanced"]
        rows = {r["code"]: r for r in tb["rows"]}
        assert rows["1000"]["debit"] == Decimal("25000.00")
        assert rows["3000"]["credit"] == Decimal("25000.00")
