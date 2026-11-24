"""Excel (.xlsx/.xls) parsing for the Import Center."""
import os

from openpyxl import load_workbook


def read_excel(path, sheet=None):
    """Return ``(headers, rows)`` where rows is a list of dicts keyed by header."""
    if not os.path.exists(path):
        raise ValueError("File not found.")
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    data = list(ws.iter_rows(values_only=True))
    wb.close()
    if not data:
        return [], []
    headers = [str(h).strip() if h is not None else f"col{i+1}"
               for i, h in enumerate(data[0])]
    rows = []
    for raw in data[1:]:
        if raw is None or all(v is None or str(v).strip() == "" for v in raw):
            continue
        row = {}
        for i, key in enumerate(headers):
            row[key] = raw[i] if i < len(raw) else None
        rows.append(row)
    return headers, rows


def sheet_names(path):
    wb = load_workbook(path, read_only=True)
    names = list(wb.sheetnames)
    wb.close()
    return names
