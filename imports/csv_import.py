"""CSV parsing for the Import Center."""
import csv
import io
import os


def read_csv(path, encoding="utf-8-sig"):
    if not os.path.exists(path):
        raise ValueError("File not found.")
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in (encoding, "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise ValueError("Could not decode the CSV file.")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    rows = []
    for raw_row in reader:
        row = {(k or "").strip(): v for k, v in raw_row.items()}
        if all((v or "").strip() == "" for v in row.values()):
            continue
        rows.append(row)
    return headers, rows
