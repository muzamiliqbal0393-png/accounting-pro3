"""Import Center, Export Center and backup/restore tests."""
import io
import os
from decimal import Decimal

from conftest import load_accounts, register, setup_business


def _csv_bytes(text):
    return io.BytesIO(text.encode("utf-8"))


def test_import_accounts_from_csv(client, app):
    from models import Account
    register(client)
    setup_business(client)
    csv_text = ("code,name,category,subcategory,opening_debit,opening_credit\n"
                "1100,Petty Cash,Assets,cash,5000,0\n"
                "3100,Partner Capital,Equity,capital,0,5000\n")
    resp = client.post("/import/upload", data={
        "kind": "accounts",
        "file": ( _csv_bytes(csv_text), "accounts.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Import Preview" in resp.data
    resp = client.post("/import/confirm", follow_redirects=True)
    assert b"Import complete" in resp.data
    with app.app_context():
        assert Account.query.filter_by(code="1100").count() == 1
        assert Account.query.filter_by(code="3100").count() == 1


def test_import_journal_rejects_unbalanced_voucher(client, app):
    from models import JournalEntry
    register(client)
    setup_business(client)
    load_accounts(app)
    csv_text = ("voucher_no,date,account_code,debit,credit,description\n"
                "IMP-1,2026-01-05,1000,1000,0,Cash\n"
                "IMP-1,2026-01-05,4000,0,900,Sales\n")
    client.post("/import/upload", data={
        "kind": "journal", "file": (_csv_bytes(csv_text), "journal.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    resp = client.get("/import/preview")
    assert b"unbalanced" in resp.data
    client.post("/import/confirm", follow_redirects=True)
    with app.app_context():
        assert JournalEntry.query.count() == 0


def test_import_journal_balanced_voucher(client, app):
    from models import JournalEntry
    register(client)
    setup_business(client)
    load_accounts(app)
    csv_text = ("voucher_no,date,account_code,debit,credit,description\n"
                "IMP-2,2026-01-05,1000,1000,0,Cash\n"
                "IMP-2,2026-01-05,4000,0,1000,Sales\n")
    client.post("/import/upload", data={
        "kind": "journal", "file": (_csv_bytes(csv_text), "journal.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    resp = client.post("/import/confirm", follow_redirects=True)
    assert b"Import complete" in resp.data
    with app.app_context():
        entry = JournalEntry.query.filter_by(voucher_no="IMP-2").first()
        assert entry is not None
        assert entry.status == "posted"
        assert entry.total_debit == Decimal("1000.00")


def test_import_detects_duplicate_voucher(client, app):
    register(client)
    setup_business(client)
    load_accounts(app)
    csv_text = ("voucher_no,date,account_code,debit,credit\n"
                "DUP-1,2026-01-05,1000,500,0\n"
                "DUP-1,2026-01-05,4000,0,500\n")
    for _ in range(2):
        client.post("/import/upload", data={
            "kind": "journal", "file": (_csv_bytes(csv_text), "j.csv")},
            content_type="multipart/form-data", follow_redirects=True)
        client.post("/import/confirm", follow_redirects=True)
    resp = client.post("/import/upload", data={
        "kind": "journal", "file": (_csv_bytes(csv_text), "j.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"duplicate voucher number" in resp.data


def test_import_products(client, app):
    from models import Product
    register(client)
    setup_business(client)
    csv_text = ("code,name,category,purchase_cost,selling_price,opening_qty,reorder_level\n"
                "P-1,Notebook,Stationery,80,120,50,10\n")
    client.post("/import/upload", data={"kind": "products",
                                        "file": (_csv_bytes(csv_text), "p.csv")},
                content_type="multipart/form-data", follow_redirects=True)
    client.post("/import/confirm", follow_redirects=True)
    with app.app_context():
        p = Product.query.filter_by(code="P-1").first()
        assert p is not None
        assert p.purchase_cost == Decimal("80.00")
        assert p.available_stock == 50


def test_export_center_renders(client, seeded):
    resp = seeded["client"].get("/export")
    assert resp.status_code == 200
    assert b"Complete accounting file" in resp.data


def test_pdf_export(client, seeded):
    resp = seeded["client"].get("/export/pdf/trial_balance")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF")
    assert len(resp.data) > 2000


def test_word_export(client, seeded):
    resp = seeded["client"].get("/export/word/balance_sheet")
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.mimetype
    assert resp.data[:2] == b"PK"
    assert len(resp.data) > 5000


def test_excel_export_single_report(client, seeded):
    from openpyxl import load_workbook
    resp = seeded["client"].get("/export/excel/trial_balance")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.mimetype
    wb = load_workbook(io.BytesIO(resp.data))
    assert "Trial Balance" in wb.sheetnames


def test_full_excel_workbook_has_17_sheets(client, seeded):
    from openpyxl import load_workbook
    resp = seeded["client"].get("/export/excel/full-project")
    assert resp.status_code == 200
    wb = load_workbook(io.BytesIO(resp.data))
    expected = ["Business Information", "Chart of Accounts", "Opening Balances",
                "General Journal", "General Ledger", "Trial Balance",
                "Adjusting Entries", "Adjusted Trial Balance", "Trading Account",
                "Profit & Loss", "Balance Sheet", "Closing Entries",
                "Post-Closing TB", "Annual Analysis", "Product Sales", "Inventory",
                "Budget Reports"]
    for name in expected:
        assert name in wb.sheetnames, f"missing sheet {name}"
    assert len(wb.sheetnames) == 17


def test_full_pdf_and_word_exports(client, seeded):
    resp = seeded["client"].get("/export/pdf/full-project")
    assert resp.status_code == 200 and resp.data.startswith(b"%PDF")
    resp = seeded["client"].get("/export/word/full-project")
    assert resp.status_code == 200 and resp.data[:2] == b"PK"


def test_backup_download(client, seeded):
    resp = seeded["client"].get("/backup/download")
    assert resp.status_code == 200
    assert len(resp.data) > 1000


def test_restore_rejects_non_sqlite_file(client, seeded):
    resp = seeded["client"].post("/backup/restore", data={
        "confirm": "yes",
        "file": (io.BytesIO(b"not a database at all"), "bad.db")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"not a valid SQLite database" in resp.data


def test_restore_requires_confirmation(client, seeded):
    resp = seeded["client"].post("/backup/restore", data={
        "file": (io.BytesIO(b"SQLite format 3\x00" + b"0" * 200), "x.db")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"tick the confirmation" in resp.data


def test_audit_log_records_actions(client, seeded):
    from models import AuditLog
    seeded["client"].get("/audit-log")
    with seeded["client"].application.app_context():
        actions = {a.action for a in AuditLog.query.all()}
        assert "Transaction created" in actions or "User registered" in actions


def test_products_and_inventory_flow(client, app):
    from models import InventoryTransaction, JournalEntry, Product
    register(client)
    setup_business(client)
    load_accounts(app)
    resp = client.post("/products", data={
        "code": "SKU-1", "name": "Pen", "category": "Stationery",
        "purchase_cost": "20", "selling_price": "35", "opening_qty": "10",
        "reorder_level": "5", "discount_percent": "0"}, follow_redirects=True)
    assert b"created" in resp.data
    with app.app_context():
        product = Product.query.filter_by(code="SKU-1").first()
        product_id = product.id
    resp = client.post("/inventory", data={
        "kind": "sale", "product_id": product_id, "quantity": "4", "rate": "35",
        "txn_date": "2026-02-01", "post_journal": "on"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        product = Product.query.get(product_id)
        assert product.sold_qty == 4
        assert product.available_stock == 6
        assert product.revenue == Decimal("140.00")
        assert InventoryTransaction.query.count() == 1
        assert JournalEntry.query.count() == 1


def test_budget_analysis(client, seeded):
    from accounting.analytics import budget_analysis
    from models import Budget
    resp = seeded["client"].post("/budgets", data={
        "name": "Q1 Budget", "kind": "Quarterly",
        "start_date": "2026-01-01", "end_date": "2026-03-31",
        "planned_income": "300000", "planned_expenses": "100000"},
        follow_redirects=True)
    assert resp.status_code == 200
    with seeded["client"].application.app_context():
        budget = Budget.query.first()
        analysis = budget_analysis(seeded["business_id"], budget)
        assert analysis["planned_income"] == Decimal("300000.00")
        assert analysis["actual_income"] == Decimal("250000.00")
        assert analysis["income_variance"] == Decimal("-50000.00")
