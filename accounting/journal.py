"""
Journal entry creation, validation, posting and reversal.

All financial mutation for journal vouchers goes through this module so that
validation rules (balanced entry, positive amounts, single-sided lines) can
never be bypassed by a route.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from extensions import db
from models import (Account, JournalEntry, JournalLine, money, ZERO,
                    ACTIVE_ENTRY_STATUS)


class JournalError(Exception):
    """Raised when a voucher fails validation."""


def parse_amount(raw, field="amount"):
    """Parse a user supplied amount into Decimal (never float)."""
    if raw is None:
        return ZERO
    if isinstance(raw, Decimal):
        return money(raw)
    text = str(raw).strip().replace(",", "").replace("Rs.", "").replace("₨", "")
    if text in ("", "-"):
        return ZERO
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise JournalError(f"{field} must be a valid number (got '{raw}').")
    if value < 0:
        raise JournalError(f"{field} cannot be negative.")
    return money(value)


def next_voucher_no(business_id, prefix="JV-", model=JournalEntry,
                    field="voucher_no"):
    """Next sequential number for the business, e.g. JV-0001."""
    rows = db.session.query(getattr(model, field)).filter(
        model.business_id == business_id).all()
    highest = 0
    for (value,) in rows:
        if not value:
            continue
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"{prefix}{highest + 1:04d}"


def parse_date(raw, field="date"):
    if isinstance(raw, date):
        return raw
    if not raw:
        raise JournalError(f"{field} is required.")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(raw).strip()[:10], fmt).date()
        except ValueError:
            continue
    raise JournalError(f"{field} must be a valid date (yyyy-mm-dd).")


def build_lines(raw_lines):
    """Normalise posted form lines into ``[{account_id, debit, credit, description}]``."""
    cleaned = []
    for idx, raw in enumerate(raw_lines, start=1):
        acc_id = str(raw.get("account_id") or "").strip()
        debit = parse_amount(raw.get("debit"), f"Line {idx} debit")
        credit = parse_amount(raw.get("credit"), f"Line {idx} credit")
        if not acc_id and debit == 0 and credit == 0:
            continue  # silently skip empty rows
        if not acc_id:
            raise JournalError(f"Line {idx}: please select an account.")
        if debit > 0 and credit > 0:
            raise JournalError(f"Line {idx}: enter either a debit or a credit, not both.")
        if debit == 0 and credit == 0:
            raise JournalError(f"Line {idx}: enter a debit or credit amount.")
        cleaned.append({
            "account_id": int(acc_id),
            "debit": debit,
            "credit": credit,
            "description": (raw.get("description") or "").strip()[:255] or None,
        })
    if len(cleaned) < 2:
        raise JournalError("A journal entry needs at least two lines (double entry).")
    return cleaned


def validate_balance(lines, tolerance=ZERO):
    total_debit = sum((l["debit"] for l in lines), ZERO)
    total_credit = sum((l["credit"] for l in lines), ZERO)
    if money(total_debit) != money(total_credit):
        raise JournalError(
            "Voucher is not balanced: total debit Rs. "
            f"{money(total_debit):,} does not equal total credit Rs. {money(total_credit):,} "
            f"(difference Rs. {money(total_debit - total_credit):,})."
        )
    return money(total_debit), money(total_credit)


def validate_accounts(business_id, lines):
    ids = {l["account_id"] for l in lines}
    accounts = Account.query.filter(Account.business_id == business_id,
                                    Account.id.in_(ids)).all()
    found = {a.id: a for a in accounts}
    missing = ids - set(found)
    if missing:
        raise JournalError("One or more selected accounts do not belong to this business.")
    inactive = [a.name for a in accounts if not a.is_active]
    if inactive:
        raise JournalError("Cannot post to inactive account(s): " + ", ".join(inactive))
    return found


def create_entry(business_id, user_id, data, raw_lines, status="posted",
                 period_id=None, commit=True):
    """
    Create and (optionally) post a journal voucher.

    ``data`` expects: voucher_no, entry_date, entry_type, description,
    narration, reference, attachment.
    """
    lines = build_lines(raw_lines)
    total_debit, total_credit = validate_balance(lines)
    validate_accounts(business_id, lines)

    voucher_no = (data.get("voucher_no") or "").strip() or next_voucher_no(business_id)
    duplicate = JournalEntry.query.filter_by(business_id=business_id,
                                             voucher_no=voucher_no).first()
    if duplicate:
        raise JournalError(f"Voucher number '{voucher_no}' already exists. "
                           "Voucher numbers must be unique.")
    entry_date = parse_date(data.get("entry_date"), "Transaction date")

    entry = JournalEntry(
        business_id=business_id,
        period_id=period_id,
        voucher_no=voucher_no,
        entry_date=entry_date,
        entry_type=(data.get("entry_type") or "General Journal"),
        description=(data.get("description") or "").strip()[:255],
        narration=(data.get("narration") or "").strip(),
        reference=(data.get("reference") or "").strip()[:80],
        attachment=(data.get("attachment") or "").strip()[:255] or None,
        status=status,
        total_debit=total_debit,
        total_credit=total_credit,
        created_by=user_id,
        posted_at=datetime.utcnow() if status == "posted" else None,
    )
    for line in lines:
        entry.lines.append(JournalLine(**line))

    if commit:
        try:
            db.session.add(entry)
            db.session.commit()
        except Exception as exc:  # pragma: no cover - safety net
            db.session.rollback()
            raise JournalError(f"Could not save the voucher: {exc}")
    else:
        db.session.add(entry)
    return entry


def update_draft(entry, data, raw_lines):
    """Edit a draft voucher in place (posted vouchers are immutable)."""
    if entry.status != "draft":
        raise JournalError("Posted vouchers cannot be edited. "
                           "Use a reversal or adjustment entry instead.")
    lines = build_lines(raw_lines)
    total_debit, total_credit = validate_balance(lines)
    validate_accounts(entry.business_id, lines)

    voucher_no = (data.get("voucher_no") or entry.voucher_no).strip()
    duplicate = JournalEntry.query.filter(
        JournalEntry.business_id == entry.business_id,
        JournalEntry.voucher_no == voucher_no,
        JournalEntry.id != entry.id).first()
    if duplicate:
        raise JournalError(f"Voucher number '{voucher_no}' is already in use.")

    entry.voucher_no = voucher_no
    entry.entry_date = parse_date(data.get("entry_date"), "Transaction date")
    entry.entry_type = data.get("entry_type") or entry.entry_type
    entry.description = (data.get("description") or "")[:255]
    entry.narration = (data.get("narration") or "").strip()
    entry.reference = (data.get("reference") or "")[:80]
    entry.total_debit = total_debit
    entry.total_credit = total_credit
    entry.lines.clear()
    for line in lines:
        entry.lines.append(JournalLine(**line))
    db.session.commit()
    return entry


def post_draft(entry):
    if entry.status != "draft":
        raise JournalError("Only draft vouchers can be posted.")
    if not entry.is_balanced:
        raise JournalError("Draft voucher is not balanced and cannot be posted.")
    entry.status = "posted"
    entry.posted_at = datetime.utcnow()
    db.session.commit()
    return entry


def delete_draft(entry):
    if entry.status != "draft":
        raise JournalError("Posted vouchers cannot be deleted. "
                           "Post a reversal entry to correct it.")
    db.session.delete(entry)
    db.session.commit()


def reverse_entry(entry, user_id, reason=None, reversal_date=None):
    """Create a mirror-image posted entry and flag the original as reversed."""
    if entry.status not in ACTIVE_ENTRY_STATUS:
        raise JournalError("Only posted vouchers can be reversed.")
    if entry.reversal_of_id:
        raise JournalError("A reversal voucher itself cannot be reversed.")

    existing = JournalEntry.query.filter_by(business_id=entry.business_id,
                                            reversal_of_id=entry.id).first()
    if existing:
        raise JournalError(f"This voucher was already reversed by {existing.voucher_no}.")

    reversal = JournalEntry(
        business_id=entry.business_id,
        period_id=entry.period_id,
        voucher_no=next_voucher_no(entry.business_id),
        entry_date=reversal_date or entry.entry_date,
        entry_type="Reversal",
        description=f"Reversal of {entry.voucher_no}" + (f": {reason}" if reason else ""),
        narration=reason or f"Reversal of voucher {entry.voucher_no}",
        reference=entry.voucher_no,
        status="posted",
        reversal_of_id=entry.id,
        total_debit=money(entry.total_credit),
        total_credit=money(entry.total_debit),
        created_by=user_id,
        posted_at=datetime.utcnow(),
    )
    for line in entry.lines:
        reversal.lines.append(JournalLine(account_id=line.account_id,
                                          debit=money(line.credit),
                                          credit=money(line.debit),
                                          description=f"Reversal - {line.description or ''}"[:255]))
    entry.status = "reversed"
    try:
        db.session.add(reversal)
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        raise JournalError(f"Reversal failed: {exc}")
    return reversal
