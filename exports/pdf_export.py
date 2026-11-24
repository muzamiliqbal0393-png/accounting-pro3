"""
PDF export using ReportLab (A4).

Features: cover page, business logo & details, repeated table headers, page
numbers, headers/footers, Rs. currency formatting and safe column widths so no
amount is ever cut off.
"""
import io
import os
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle, Image, PageBreak,
                                KeepTogether)

NAVY = colors.HexColor("#101D3A")
ROYAL = colors.HexColor("#2457D6")
LIGHT = colors.HexColor("#F5F7FB")
GREY = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#D6DCE8")

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def fmt_money(value, symbol="Rs."):
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    if number == 0:
        return f"{symbol} 0.00"
    return f"{symbol} {number:,.2f}"


def fmt_date(value):
    if not value:
        return ""
    try:
        return value.strftime("%d-%b-%Y")
    except AttributeError:
        return str(value)


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=20, textColor=NAVY, spaceAfter=6),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontName="Helvetica",
                                   fontSize=11, textColor=GREY, alignment=TA_CENTER),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12.5, textColor=NAVY, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=13, textColor=colors.HexColor("#1F2937")),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=8, leading=10, textColor=GREY),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontName="Helvetica",
                               fontSize=8, leading=10),
        "cellr": ParagraphStyle("cellr", parent=ss["Normal"], fontName="Helvetica",
                                fontSize=8, leading=10, alignment=TA_RIGHT),
        "center": ParagraphStyle("center", parent=ss["Normal"], fontName="Helvetica-Bold",
                                 fontSize=10, textColor=NAVY, alignment=TA_CENTER),
    }


def _cover(report, st):
    b = report["business"]
    flow = [Spacer(1, 30 * mm)]
    if b.logo:
        from config import Config
        path = os.path.join(Config.UPLOAD_FOLDER, "logos", b.logo)
        if not os.path.exists(path):
            path = os.path.join(Config.UPLOAD_FOLDER, b.logo)
        if os.path.exists(path):
            try:
                img = Image(path, width=34 * mm, height=34 * mm, kind="proportional")
                img.hAlign = "CENTER"
                flow += [img, Spacer(1, 6 * mm)]
            except Exception:
                pass
    flow.append(Paragraph(escape(b.name or "Business"), st["title"]))
    details = []
    for label, value in (("Owner", b.owner_name), ("Address", b.address),
                         ("Phone", b.phone), ("Email", b.email),
                         ("Business Type", b.business_type),
                         ("Currency", f"{b.currency} (Rs.)"),
                         ("Accounting Method", b.accounting_method)):
        if value:
            details.append(f"<b>{escape(label)}:</b> {escape(str(value))}")
    if details:
        flow.append(Paragraph("<br/>".join(details), st["subtitle"]))
    flow += [Spacer(1, 14 * mm),
             Paragraph(f"<b>{escape(report['title'])}</b>", st["title"]),
             Spacer(1, 4 * mm),
             Paragraph(f"Accounting Period: {escape(report['period_label'])}", st["subtitle"]),
             Paragraph(f"Generated: {report['generated'].strftime('%d %b %Y %H:%M')}",
                       st["subtitle"]),
             Spacer(1, 20 * mm),
             Paragraph("ACCOUNTING PRO", st["center"]),
             Paragraph("Complete Computerized Accounting &amp; Business Management System",
                       st["subtitle"]),
             PageBreak()]
    return flow


def _cell_text(value, col, symbol=None):
    if value is None:
        return ""
    if col["type"] == "money":
        return f"{symbol or 'Rs.'} {value:,.2f}"
    if col["type"] == "date":
        return fmt_date(value) or ""
    return str(value)


def _column_widths(cols, rows):
    """Allocate table widths so numeric cells never wrap onto a second line.

    Each money/date column is measured against the widest figure it actually
    contains, gets that as a hard minimum, and the remaining width is shared
    between the text columns in proportion to their configured weight.
    """
    total_weight = sum(c.get("width", 1.0) for c in cols) or 1
    nums = []
    for col in cols:
        if col["type"] == "text":
            nums.append(None)
            continue
        widest = 0
        for r in rows:  # measure bold too: total rows are set in Helvetica-Bold
            text = _cell_text(r.get(col["key"]), col)
            widest = max(widest,
                         stringWidth(text, "Helvetica", 8),
                         stringWidth(text, "Helvetica-Bold", 8))
        floor = {"money": 24 * mm, "date": 19 * mm}.get(col["type"], 16 * mm)
        nums.append(max(floor, widest + 10))
    # all money (and all date) columns share one width so the figures line up
    for kind in ("money", "date"):
        same = [n for c, n in zip(cols, nums) if n and c["type"] == kind]
        if same:
            widest = max(same)
            for i, c in enumerate(cols):
                if nums[i] and c["type"] == kind:
                    nums[i] = widest
    numeric_total = sum(n for n in nums if n)
    text_weight = sum(c.get("width", 1.0) for c, n in zip(cols, nums) if n is None)
    if numeric_total + 20 * mm >= CONTENT_W or not text_weight:
        # No room for comfortable text columns: fall back to pure weighting.
        return [CONTENT_W * c.get("width", 1.0) / total_weight for c in cols]
    text_pool = CONTENT_W - numeric_total
    widths = []
    for col, need in zip(cols, nums):
        if need is None:
            widths.append(text_pool * col.get("width", 1.0) / text_weight)
        else:
            widths.append(need)
    return widths


def _table(section, st, symbol):
    cols = section["columns"]
    widths = _column_widths(cols, section["rows"])

    header = [Paragraph(f"<b>{escape(c['label'])}</b>", st["cell"]) for c in cols]
    data = [header]
    for row in section["rows"]:
        cells = []
        for col in cols:
            value = row.get(col["key"])
            if col["type"] == "money":
                text = fmt_money(value, symbol)
            elif col["type"] == "date":
                text = fmt_date(value)
            else:
                text = "" if value is None else str(value)
            style = st["cellr"] if col["align"] == "right" else st["cell"]
            cells.append(Paragraph(escape(text), style))
        data.append(cells)

    if not section["rows"]:
        data.append([Paragraph(section.get("empty", "No data available for this period."),
                               st["cell"])] + [""] * (len(cols) - 1))

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    table.setStyle(TableStyle(style))
    return table


def _totals_row(section, st, symbol):
    cols = section["columns"]
    totals = section.get("totals_multi") or section.get("totals")
    if not totals:
        return None
    total_weight = sum(c.get("width", 1.0) for c in cols) or 1
    widths = [CONTENT_W * c.get("width", 1.0) / total_weight for c in cols]
    cells = []
    for col in cols:
        key = col["key"]
        if col["type"] == "money":
            value = totals.get(key)
            text = fmt_money(value, symbol) if value is not None else ""
        else:
            text = str(totals.get("label", "Total")) if col is cols[0] else ""
        style = st["cellr"] if col["align"] == "right" else st["cell"]
        cells.append(Paragraph(f"<b>{escape(text)}</b>", style))
    table = Table([cells], colWidths=widths, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E8EDF9")),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _section_flow(section, st, symbol):
    flow = [Paragraph(escape(section.get("title", "")), st["h2"])]
    flow.append(_table(section, st, symbol))
    totals = _totals_row(section, st, symbol)
    if totals:
        flow += [Spacer(1, 2), totals]
    badges = []
    if section.get("status"):
        badges.append(f"<b>Status:</b> {escape(section['status'])}")
    if section.get("result"):
        badges.append(f"<b>{escape(section['result']['label'])}:</b> "
                      f"{escape(fmt_money(section['result']['amount'], symbol))}")
    if badges:
        flow += [Spacer(1, 3), Paragraph(" &nbsp;|&nbsp; ".join(badges), st["body"])]
    if section.get("note"):
        flow += [Spacer(1, 2), Paragraph(escape(section["note"]), st["small"])]
    flow.append(Spacer(1, 8 * mm))
    return flow


class _NumberedDoc(BaseDocTemplate):
    def __init__(self, buffer, report, **kwargs):
        super().__init__(buffer, **kwargs)
        self.report = report
        frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN - 8 * mm, id="body")
        self.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                            onPage=self._decorate)])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(MARGIN, PAGE_H - MARGIN + 4 * mm,
                          f"{self.report['business'].name} | {self.report['title']}")
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 4 * mm,
                               f"Period: {self.report['period_label']}")
        canvas.setStrokeColor(BORDER)
        canvas.line(MARGIN, PAGE_H - MARGIN + 2 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN + 2 * mm)
        canvas.line(MARGIN, MARGIN - 3 * mm, PAGE_W - MARGIN, MARGIN - 3 * mm)
        canvas.drawString(MARGIN, MARGIN - 8 * mm, "ACCOUNTING PRO")
        canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 8 * mm, f"Page {doc.page}")
        canvas.restoreState()


def render_pdf(report):
    """Return a ``BytesIO`` containing the rendered PDF."""
    buffer = io.BytesIO()
    st = _styles()
    doc = _NumberedDoc(buffer, report, pagesize=A4, leftMargin=MARGIN,
                       rightMargin=MARGIN, topMargin=MARGIN + 8 * mm,
                       bottomMargin=MARGIN, title=report["title"],
                       author=report["business"].name, subject=report["title"])
    flow = [] if report.get("skip_cover") else _cover(report, st)
    symbol = "Rs."
    for section in report["sections"]:
        flow.extend(_section_flow(section, st, symbol))
    if not report["sections"]:
        flow.append(Paragraph("No data available.", st["body"]))
    doc.build(flow)
    buffer.seek(0)
    return buffer


def export_filename(business, report_name, ext="pdf"):
    safe = "".join(ch if ch.isalnum() else "_" for ch in (business.name or "business")).strip("_")
    return f"{safe or 'business'}_{report_name}.{ext}"
