"""
Import Center processing: parse -> map -> validate -> preview -> confirm.

Nothing is written to the database until :func:`apply_import` is called with
validated rows that the user has confirmed.
"""
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from extensions import db
from models import (Account, JournalEntry, Adjustment, Product, money, ZERO,
                    CATEGORIES, SUBCATEGORIES, NORMAL_BALANCE, ADJUSTMENT_KINDS)
from accounting.journal import (JournalError, create_entry, parse_date,
                                build_lines, validate_balance)
from accounting.adjustments import create_adjustment
from imports.excel_import import read_excel
from imports.csv_import import read_csv

IMPORT_KINDS = {
    "accounts": {
        "title": "Chart of Accounts",
        "fields": [("code", "Account Code", True),
                   ("name", "Account Name", True),
                   ("category", "Category (Assets/Liabilities/Equity/Revenue/Expenses)", True),
                   ("subcategory", "Subcategory key", False),
                   ("opening_debit", "Opening Debit", False),
                   ("opening_credit", "Opening Credit", False),
                   ("description", "Description", False)],
    },
    "opening_balances": {
        "title": "Opening Balances",
        "fields": [("code", "Account Code", True),
                   ("opening_debit", "Opening Debit", False),
                   ("opening_credit", "Opening Credit", False)],
    },
    "journal": {
        "title": "Journal Entries",
        "fields": [("voucher_no", "Voucher No", False),
                   ("date", "Date", True),
                   ("account_code", "Account Code", True),
                   ("debit", "Debit", False),
                   ("credit", "Credit", False),
                   ("description", "Description", False),
                   ("narration", "Narration", False)],
    },
    "adjustments": {
        "title": "Adjusting Entries",
        "fields": [("adj_no", "Adjustment No", False),
                   ("date", "Date", True),
                   ("kind", "Adjustment Type", False),
                   ("account_code", "Account Code", True),
                   ("debit", "Debit", False),
                   ("credit", "Credit", False),
                   ("description", "Description", False)],
    },
    "products": {
        "title": "Products / Inventory",
        "fields": [("code", "Product Code", True),
                   ("name", "Product Name", True),
                   ("category", "Category", False),
                   ("purchase_cost", "Purchase Cost", False),
                   ("selling_price", "Selling Price", False),
                   ("opening_qty", "Opening Quantity", False),
                   ("reorder_level", "Reorder Level", False)],
    },
}

FIELD_SPECS = IMPORT_KINDS

#: fuzzy header -> canonical field
HEADER_HINTS = {
    "code": ["code", "account code", "acct code", "acc code", "account no"],
    "name": ["name", "account name", "account", "title"],
    "category": ["category", "type", "group", "account type", "classification"],
    "subcategory": ["subcategory", "sub category", "sub-category"],
    "opening_debit": ["opening debit", "op debit", "debit opening", "opening dr"],
    "opening_credit": ["opening credit", "op credit", "credit opening", "opening cr"],
    "description": ["description", "particulars", "details", "remarks"],
    "voucher_no": ["voucher", "voucher no", "voucher number", "vch no", "entry no"],
    "date": ["date", "transaction date", "txn date", "entry date"],
    "account_code": ["account code", "code", "account", "ledger", "account name"],
    "debit": ["debit", "dr", "debit amount"],
    "credit": ["credit", "cr", "credit amount"],
    "narration": ["narration", "notes", "note", "remarks"],
    "adj_no": ["adjustment no", "adj no", "adjustment number", "voucher no"],
    "kind": ["kind", "type", "adjustment type"],
    "purchase_cost": ["purchase cost", "cost", "cost price", "purchase price"],
    "selling_price": ["selling price", "price", "sale price", "rate"],
    "opening_qty": ["opening qty", "opening quantity", "qty", "quantity", "stock"],
    "reorder_level": ["reorder", "reorder level", "min stock"],
}


def _norm_header(value):
    """Normalise a header for fuzzy matching: lowercase, underscores -> spaces."""
    text = str(value or "").strip().lower()
    for ch in ("_", "-", ".", "/"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


def auto_map(kind, headers):
    """Guess a column mapping from uploaded headers."""
    mapping = {}
    normalised = {_norm_header(h): h for h in headers}
    for field, _label, _req in IMPORT_KINDS[kind]["fields"]:
        for hint in HEADER_HINTS.get(field, []) + [field, field.replace("_", " ")]:
            key = _norm_header(hint)
            if key in normalised:
                mapping[field] = normalised[key]
                break
    return mapping


def parse_upload(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        return read_excel(path)
    if ext == ".csv":
        return read_csv(path)
    raise ValueError("Unsupported file type. Please upload .xlsx or .csv.")


def _num(value, field):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return ZERO
    if isinstance(value, (int, float, Decimal)):
        try:
            return money(Decimal(str(value)))
        except InvalidOperation:
            raise ValueError(f"{field}: invalid number '{value}'")
    text = str(value).strip().replace(",", "").replace("Rs.", "").replace("₨", "")
    text = text.replace("(", "-").replace(")", "")
    try:
        val = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{field}: invalid number '{value}'")
    if val < 0:
        raise ValueError(f"{field}: negative amounts are not allowed")
    return money(val)


def _int(value, field):
    n = _num(value, field)
    return int(n)


def _date(value, field):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return parse_date(value, field)
    except JournalError as exc:
        raise ValueError(str(exc))


def validate_rows(kind, rows, mapping, business_id):
    """
    Return ``{"valid": [...], "errors": [...], "warnings": [...], "summary": {...}}``.

    Each valid record is a normalised dict ready for :func:`apply_import`.
    """
    if kind not in IMPORT_KINDS:
        raise ValueError("Unknown import type.")

    accounts = {a.code: a for a in Account.query.filter_by(business_id=business_id).all()}
    names = {a.name.lower(): a for a in Account.query.filter_by(business_id=business_id).all()}
    existing_vouchers = {v for (v,) in db.session.query(JournalEntry.voucher_no)
                         .filter_by(business_id=business_id).all()}
    existing_adj = {v for (v,) in db.session.query(Adjustment.adj_no)
                    .filter_by(business_id=business_id).all()}
    existing_products = {p.code for p in Product.query.filter_by(business_id=business_id).all()}

    valid, errors, warnings = [], [], []
    voucher_buckets = {}

    for idx, raw in enumerate(rows, start=1):
        record = {"_row": idx}
        try:
            for field, _label, required in IMPORT_KINDS[kind]["fields"]:
                col = mapping.get(field)
                value = raw.get(col) if col else None
                record[field] = value
                if required and (value is None or str(value).strip() == ""):
                    raise ValueError(f"'{field}' is required")

            if kind == "accounts":
                code = str(record["code"]).strip()
                name = str(record["name"]).strip()
                category = str(record["category"] or "").strip()
                cat_match = next((c for c in CATEGORIES if c.lower() == category.lower()), None)
                if not cat_match:
                    raise ValueError(f"category '{category}' must be one of "
                                     + ", ".join(CATEGORIES))
                if code in accounts:
                    raise ValueError(f"account code {code} already exists")
                sub = str(record.get("subcategory") or "").strip().lower()
                if not sub or sub not in SUBCATEGORIES:
                    sub = _guess_subcategory(name, cat_match)
                    if not sub:
                        raise ValueError("could not determine subcategory; "
                                         "please provide a valid subcategory key")
                if SUBCATEGORIES[sub][1] != cat_match:
                    raise ValueError(f"subcategory '{sub}' does not belong to {cat_match}")
                od = _num(record.get("opening_debit"), "opening_debit")
                oc = _num(record.get("opening_credit"), "opening_credit")
                if od and oc:
                    raise ValueError("enter either an opening debit or credit, not both")
                valid.append({"code": code, "name": name, "category": cat_match,
                              "subcategory": sub, "opening_debit": od,
                              "opening_credit": oc,
                              "description": str(record.get("description") or "") or None,
                              "_row": idx})

            elif kind == "opening_balances":
                code = str(record["code"]).strip()
                if code not in accounts:
                    raise ValueError(f"unknown account code {code}")
                od = _num(record.get("opening_debit"), "opening_debit")
                oc = _num(record.get("opening_credit"), "opening_credit")
                if od and oc:
                    raise ValueError("enter either an opening debit or credit, not both")
                valid.append({"code": code, "opening_debit": od,
                              "opening_credit": oc, "_row": idx})

            elif kind == "products":
                code = str(record["code"]).strip()
                name = str(record["name"]).strip()
                if code in existing_products:
                    raise ValueError(f"product code {code} already exists")
                valid.append({
                    "code": code, "name": name,
                    "category": str(record.get("category") or ""),
                    "purchase_cost": _num(record.get("purchase_cost"), "purchase_cost"),
                    "selling_price": _num(record.get("selling_price"), "selling_price"),
                    "opening_qty": _int(record.get("opening_qty"), "opening_qty"),
                    "reorder_level": _int(record.get("reorder_level"), "reorder_level"),
                    "_row": idx})

            elif kind in ("journal", "adjustments"):
                no_field = "voucher_no" if kind == "journal" else "adj_no"
                key = str(record.get(no_field) or "").strip() or f"__auto_{idx}"
                code = str(record["account_code"]).strip()
                acct = accounts.get(code) or names.get(code.lower())
                if not acct:
                    raise ValueError(f"account '{code}' not found")
                debit = _num(record.get("debit"), "debit")
                credit = _num(record.get("credit"), "credit")
                if debit and credit:
                    raise ValueError("a line cannot have both debit and credit")
                if not debit and not credit:
                    raise ValueError("line has no amount")
                dt = _date(record["date"], "date")
                voucher_buckets.setdefault(key, []).append({
                    "row": idx, "date": dt, "account_id": acct.id,
                    "debit": debit, "credit": credit,
                    "description": str(record.get("description") or "")[:255],
                    "narration": str(record.get("narration") or ""),
                    "kind": str(record.get("kind") or "Other"),
                    "voucher_no": str(record.get(no_field) or "").strip(),
                })
        except ValueError as exc:
            errors.append({"row": idx, "message": str(exc)})

    if kind in ("journal", "adjustments"):
        existing = existing_vouchers if kind == "journal" else existing_adj
        for key, lines in voucher_buckets.items():
            voucher_no = lines[0]["voucher_no"]
            if voucher_no and voucher_no in existing:
                errors.append({"row": lines[0]["row"],
                               "message": f"duplicate voucher number '{voucher_no}' "
                                          "already exists in the database"})
                continue
            totals_d = sum((l["debit"] for l in lines), ZERO)
            totals_c = sum((l["credit"] for l in lines), ZERO)
            if money(totals_d) != money(totals_c):
                errors.append({"row": lines[0]["row"],
                               "message": f"voucher '{key}' is unbalanced "
                                          f"(Dr {money(totals_d):,} / Cr {money(totals_c):,})"})
                continue
            if len(lines) < 2:
                errors.append({"row": lines[0]["row"],
                               "message": f"voucher '{key}' needs at least two lines"})
                continue
            if len({l["date"] for l in lines}) > 1:
                warnings.append({"row": lines[0]["row"],
                                 "message": f"voucher '{key}' has mixed dates; "
                                            "the first date will be used"})
            valid.append({"voucher_no": voucher_no or None, "date": lines[0]["date"],
                          "kind": lines[0]["kind"], "lines": lines, "_row": lines[0]["row"]})

    elif kind == "products":
        existing_products = {p.code for p in
                             Product.query.filter_by(business_id=business_id).all()}
        for rec in list(valid):
            if rec["code"] in existing_products:
                valid.remove(rec)
                errors.append({"row": rec["_row"],
                               "message": f"product code {rec['code']} already exists"})

    summary = {
        "total_rows": len(rows),
        "valid": len(valid),
        "errors": len(errors),
        "warnings": len(warnings),
    }
    return {"valid": valid, "errors": errors, "warnings": warnings,
            "summary": summary, "kind": kind}


def _guess_subcategory(name, category):
    n = name.lower()
    rules = [
        (("cash",), "cash"), (("bank",), "bank"),
        (("receivable", "debtor"), "accounts_receivable"),
        (("closing stock", "inventory", "stock"), "inventory"),
        (("opening stock",), "opening_stock"),
        (("payable", "creditor"), "accounts_payable"),
        (("outstanding", "accrued"), "outstanding_expenses"),
        (("loan",), "loan"),
        (("capital",), "capital"), (("drawing",), "drawings"),
        (("retained",), "retained_earnings"),
        (("sales return", "return inward"), "sales_returns"),
        (("purchase return", "return outward"), "purchase_returns"),
        (("sales", "revenue"), "sales"),
        (("income", "interest received"), "other_income"),
        (("purchase",), "purchases"),
        (("depreciation",), "depreciation"),
        (("bad debt",), "bad_debts"),
    ]
    for words, sub in rules:
        if any(w in n for w in words) and SUBCATEGORIES[sub][1] == category:
            return sub
    defaults = {"Assets": "other_asset", "Liabilities": "other_liability",
                "Equity": "capital", "Revenue": "other_income",
                "Expenses": "indirect_expenses"}
    return defaults.get(category)


def apply_import(kind, valid_rows, business_id, user_id, period_id=None,
                 post_drafts=False, overwrite=False):
    """Persist validated rows. Returns a summary dict."""
    created = updated = skipped = 0
    messages = []

    if kind == "accounts":
        for rec in valid_rows:
            if Account.query.filter_by(business_id=business_id, code=rec["code"]).first():
                skipped += 1
                continue
            db.session.add(Account(business_id=business_id, code=rec["code"],
                                   name=rec["name"], category=rec["category"],
                                   subcategory=rec["subcategory"],
                                   normal_balance=NORMAL_BALANCE[rec["category"]],
                                   opening_debit=rec["opening_debit"],
                                   opening_credit=rec["opening_credit"],
                                   description=rec.get("description")))
            created += 1
        db.session.commit()
        return {"created": created, "updated": updated, "skipped": skipped,
                "messages": messages}

    if kind == "opening_balances":
        for rec in valid_rows:
            acc = Account.query.filter_by(business_id=business_id,
                                          code=rec["code"]).first()
            if not acc:
                skipped += 1
                continue
            acc.opening_debit = rec["opening_debit"]
            acc.opening_credit = rec["opening_credit"]
            updated += 1
        db.session.commit()
        return {"created": created, "updated": updated, "skipped": skipped,
                "messages": messages}

    if kind == "journal":
        for rec in valid_rows:
            try:
                create_entry(business_id, user_id,
                             {"voucher_no": rec["voucher_no"] or "",
                              "entry_date": rec["date"],
                              "entry_type": "General Journal",
                              "description": rec["lines"][0]["description"] or "Imported voucher",
                              "narration": rec["lines"][0]["narration"]},
                             [{"account_id": str(l["account_id"]),
                               "debit": l["debit"], "credit": l["credit"],
                               "description": l["description"]} for l in rec["lines"]],
                             status="posted" if not post_drafts else "draft",
                             period_id=period_id)
                created += 1
            except JournalError as exc:
                skipped += 1
                messages.append(str(exc))
        return {"created": created, "updated": updated, "skipped": skipped,
                "messages": messages}

    if kind == "adjustments":
        for rec in valid_rows:
            try:
                create_adjustment(business_id, user_id,
                                  {"adj_no": rec["voucher_no"] or "",
                                   "adj_date": rec["date"],
                                   "kind": rec["kind"] if rec["kind"] in ADJUSTMENT_KINDS else "Other",
                                   "description": rec["lines"][0]["description"] or "Imported adjustment",
                                   "narration": rec["lines"][0]["narration"]},
                                  [{"account_id": str(l["account_id"]),
                                    "debit": l["debit"], "credit": l["credit"],
                                    "description": l["description"]} for l in rec["lines"]],
                                  status="posted", period_id=period_id)
                created += 1
            except JournalError as exc:
                skipped += 1
                messages.append(str(exc))
        return {"created": created, "updated": updated, "skipped": skipped,
                "messages": messages}

    if kind == "products":
        for rec in valid_rows:
            code = str(rec["code"]).strip()
            name = str(rec["name"]).strip()
            existing = Product.query.filter_by(business_id=business_id, code=code).first()
            if existing and not overwrite:
                skipped += 1
                continue
            product = existing or Product(business_id=business_id, code=code)
            product.name = name
            product.category = str(rec.get("category") or "")[:80] or None
            product.purchase_cost = _num(rec.get("purchase_cost"), "purchase_cost")
            product.selling_price = _num(rec.get("selling_price"), "selling_price")
            product.opening_qty = _int(rec.get("opening_qty"), "opening_qty")
            product.reorder_level = _int(rec.get("reorder_level"), "reorder_level")
            if not existing:
                db.session.add(product)
                created += 1
            else:
                updated += 1
        db.session.commit()
        return {"created": created, "updated": updated, "skipped": skipped,
                "messages": messages}

    raise ValueError("Unknown import type.")
