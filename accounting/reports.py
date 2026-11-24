"""Report data builders shared by the HTML pages and the exporters."""
from models import Account, JournalEntry, Adjustment, money, ZERO, ACTIVE_ENTRY_STATUS
from accounting.ledger import posted_journal_lines, ledger_rows


def journal_rows(business_id, start=None, end=None, account_id=None, search=None,
                 source="journal"):
    """Flat general-journal rows with running totals."""
    rows = []
    if source == "journal":
        lines = posted_journal_lines(business_id, start, end, account_id)
        entries = (JournalEntry.query
                   .filter(JournalEntry.business_id == business_id,
                           JournalEntry.status.in_(ACTIVE_ENTRY_STATUS))
                   .order_by(JournalEntry.entry_date, JournalEntry.id))
        if start:
            entries = entries.filter(JournalEntry.entry_date >= start)
        if end:
            entries = entries.filter(JournalEntry.entry_date <= end)
        entries = entries.all()
        accounts = {a.id: a for a in Account.query.filter_by(business_id=business_id).all()}
        for entry in entries:
            for line in entry.lines:
                if account_id and line.account_id != account_id:
                    continue
                acc = accounts.get(line.account_id)
                if search:
                    hay = " ".join(filter(None, [entry.voucher_no, entry.description,
                                                 entry.narration, acc.name if acc else "",
                                                 acc.code if acc else ""])).lower()
                    if search.lower() not in hay:
                        continue
                rows.append({
                    "date": entry.entry_date,
                    "voucher_no": entry.voucher_no,
                    "account": acc,
                    "account_name": acc.name if acc else "Unknown",
                    "account_code": acc.code if acc else "",
                    "description": line.description or entry.description or "",
                    "debit": money(line.debit),
                    "credit": money(line.credit),
                    "narration": entry.narration or "",
                    "entry_id": entry.id,
                    "status": entry.status,
                })
    else:  # adjustments
        adj_q = Adjustment.query.filter_by(business_id=business_id, status="posted")
        if start:
            adj_q = adj_q.filter(Adjustment.adj_date >= start)
        if end:
            adj_q = adj_q.filter(Adjustment.adj_date <= end)
        accounts = {a.id: a for a in Account.query.filter_by(business_id=business_id).all()}
        for adj in adj_q.order_by(Adjustment.adj_date, Adjustment.id).all():
            for line in adj.lines:
                if account_id and line.account_id != account_id:
                    continue
                acc = accounts.get(line.account_id)
                if search:
                    hay = " ".join(filter(None, [adj.adj_no, adj.description, adj.narration,
                                                 acc.name if acc else ""])).lower()
                    if search.lower() not in hay:
                        continue
                rows.append({
                    "date": adj.adj_date,
                    "voucher_no": adj.adj_no,
                    "account": acc,
                    "account_name": acc.name if acc else "Unknown",
                    "account_code": acc.code if acc else "",
                    "description": line.description or adj.description or "",
                    "debit": money(line.debit),
                    "credit": money(line.credit),
                    "narration": adj.narration or adj.kind,
                    "entry_id": adj.id,
                    "status": adj.status,
                })
    rows.sort(key=lambda r: (r["date"], r["voucher_no"]))
    run_d = run_c = ZERO
    for row in rows:
        run_d += row["debit"]
        run_c += row["credit"]
        row["running_debit"] = money(run_d)
        row["running_credit"] = money(run_c)
    return {
        "rows": rows,
        "total_debit": money(run_d),
        "total_credit": money(run_c),
        "difference": money(run_d - run_c),
        "source": source,
    }


def ledger_report(business_id, account, start=None, end=None):
    return ledger_rows(business_id, account, start, end,
                       include_adjustments=True, include_closing=True)
