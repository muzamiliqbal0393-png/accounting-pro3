"""Product & inventory management, optionally linked to the accounting ledger."""
from datetime import date, datetime

from extensions import db
from models import (Product, InventoryTransaction, Sale, Purchase, Account,
                    money, ZERO)
from accounting.journal import JournalError, next_voucher_no, parse_date, parse_amount
from accounting import journal as journal_engine


def _account(business_id, subcategory):
    return (Account.query
            .filter_by(business_id=business_id, subcategory=subcategory)
            .order_by(Account.code).first())


def record_sale(business_id, user_id, product, qty, rate, sale_date, customer=None,
                invoice_no=None, discount=ZERO, post_journal=True):
    """
    Record a sale: increases sold quantity, optionally posts
    Dr Cash/Bank or Receivables, Cr Sales.
    """
    qty = int(qty or 0)
    if qty <= 0:
        raise JournalError("Quantity must be greater than zero.")
    rate = parse_amount(rate, "Rate")
    discount = parse_amount(discount, "Discount")
    amount = money(rate * qty - discount)
    if amount < 0:
        raise JournalError("Discount cannot exceed the sale value.")

    sale = Sale(business_id=business_id, sale_date=parse_date(sale_date),
                invoice_no=invoice_no, customer=customer, product_id=product.id,
                quantity=qty, rate=rate, discount=discount, amount=amount)
    product.sold_qty += qty
    db.session.add(sale)
    db.session.flush()
    db.session.add(InventoryTransaction(business_id=business_id, product_id=product.id,
                                        txn_date=sale.sale_date, txn_type="Sale",
                                        quantity=qty, rate=rate, amount=amount,
                                        note=customer or invoice_no))
    if post_journal:
        debit_acc = _account(business_id, "cash") or _account(business_id, "accounts_receivable")
        credit_acc = _account(business_id, "sales")
        if debit_acc and credit_acc:
            entry = journal_engine.create_entry(
                business_id, user_id,
                {"voucher_no": next_voucher_no(business_id, prefix="SV-"),
                 "entry_date": sale.sale_date, "entry_type": "Cash Sale",
                 "description": f"Sale of {product.name} x {qty}",
                 "narration": customer or ""},
                [{"account_id": debit_acc.id, "debit": amount, "credit": ZERO,
                  "description": f"Sale - {product.name}"},
                 {"account_id": credit_acc.id, "debit": ZERO, "credit": amount,
                  "description": f"Sale - {product.name}"}],
                status="draft", commit=False)  # posted by caller after commit
            db.session.flush()
            sale.entry_id = entry.id
            db.session.commit()
            return sale, entry
    db.session.commit()
    return sale, None


def record_purchase(business_id, user_id, product, qty, rate, purchase_date,
                    supplier=None, bill_no=None, post_journal=True):
    """Record a purchase: Dr Purchases, Cr Cash/Bank or Payables."""
    qty = int(qty or 0)
    if qty <= 0:
        raise JournalError("Quantity must be greater than zero.")
    rate = parse_amount(rate, "Rate")
    amount = money(rate * qty)

    purchase = Purchase(business_id=business_id, purchase_date=parse_date(purchase_date),
                        bill_no=bill_no, supplier=supplier, product_id=product.id,
                        quantity=qty, rate=rate, amount=amount)
    product.purchase_qty += qty
    db.session.add(purchase)
    db.session.flush()
    db.session.add(InventoryTransaction(business_id=business_id, product_id=product.id,
                                        txn_date=purchase.purchase_date,
                                        txn_type="Purchase", quantity=qty, rate=rate,
                                        amount=amount, note=supplier or bill_no))
    entry = None
    if post_journal:
        debit_acc = _account(business_id, "purchases")
        credit_acc = _account(business_id, "cash") or _account(business_id, "accounts_payable")
        if debit_acc and credit_acc:
            entry = journal_engine.create_entry(
                business_id, user_id,
                {"voucher_no": next_voucher_no(business_id, prefix="PU-"),
                 "entry_date": purchase.purchase_date, "entry_type": "Credit Purchase",
                 "description": f"Purchase of {product.name} x {qty}",
                 "narration": supplier or ""},
                [{"account_id": debit_acc.id, "debit": amount, "credit": ZERO,
                  "description": f"Purchase - {product.name}"},
                 {"account_id": credit_acc.id, "debit": ZERO, "credit": amount,
                  "description": f"Purchase - {product.name}"}],
                status="draft", commit=False)
            db.session.flush()
            purchase.entry_id = entry.id
    db.session.commit()
    return purchase, entry


def inventory_summary(business_id):
    products = (Product.query.filter_by(business_id=business_id)
                .order_by(Product.code).all())
    total_value = sum((p.stock_value for p in products), ZERO)
    total_revenue = sum((p.revenue for p in products), ZERO)
    total_cogs = sum((p.cogs for p in products), ZERO)
    low_stock = [p for p in products if p.is_low_stock]
    gross = money(total_revenue - total_cogs)
    margin = money(gross / total_revenue * 100) if total_revenue else ZERO
    return {
        "products": products,
        "count": len(products),
        "total_value": money(total_value),
        "total_revenue": money(total_revenue),
        "total_cogs": money(total_cogs),
        "gross_profit": gross,
        "margin": margin,
        "low_stock": low_stock,
    }
