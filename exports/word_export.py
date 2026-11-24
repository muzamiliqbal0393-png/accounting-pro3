"""Word (.docx) export using python-docx."""
import io
import os
from decimal import Decimal

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Cm, RGBColor

NAVY = RGBColor(0x10, 0x1D, 0x3A)
ROYAL = RGBColor(0x24, 0x57, 0xD6)
GREY = RGBColor(0x6B, 0x72, 0x80)


def _money(value):
    if value is None or value == "":
        return ""
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:
        return str(value)


def _date(value):
    if not value:
        return ""
    try:
        return value.strftime("%d-%b-%Y")
    except AttributeError:
        return str(value)


def _shade(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _add_page_field(paragraph, field="PAGE"):
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field} "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def _cover(doc, report):
    business = report["business"]
    logo_path = None
    if business.logo:
        from config import Config
        candidate = os.path.join(Config.UPLOAD_FOLDER, "logos", business.logo)
        logo_path = candidate if os.path.exists(candidate) else None

    for _ in range(2):
        doc.add_paragraph()
    if logo_path:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(logo_path, width=Cm(4))

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(business.name or "Business")
    run.font.size = Pt(26)
    run.font.bold = True
    run.font.color.rgb = NAVY

    for label, value in (("Owner", business.owner_name), ("Address", business.address),
                         ("Phone", business.phone), ("Email", business.email),
                         ("Business Type", business.business_type),
                         ("Currency", f"{business.currency} (Rs.)"),
                         ("Accounting Method", business.accounting_method)):
        if value:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r1 = p.add_run(f"{label}: ")
            r1.bold = True
            r1.font.size = Pt(10)
            r1.font.color.rgb = GREY
            r2 = p.add_run(str(value))
            r2.font.size = Pt(10)

    for _ in range(3):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(report["title"])
    run.font.size = Pt(22)
    run.font.bold = True
    run.font.color.rgb = ROYAL

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"Accounting Period: {report['period_label']}")
    run.font.size = Pt(11)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"Generated on {report['generated'].strftime('%d %b %Y %H:%M')}")
    run.font.size = Pt(10)
    run.font.color.rgb = GREY

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _header_footer(doc, report):
    section = doc.sections[0]
    header = section.header.paragraphs[0]
    header.text = f"{report['business'].name}  |  {report['title']}"
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in header.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = GREY

    footer = section.footer.paragraphs[0]
    footer.text = ""
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("ACCOUNTING PRO  |  Page ")
    run.font.size = Pt(8)
    run.font.color.rgb = GREY
    _add_page_field(footer)


def _add_table(doc, section):
    cols = section["columns"]
    table = doc.add_table(rows=1, cols=len(cols))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    hdr = table.rows[0].cells
    for i, col in enumerate(cols):
        hdr[i].text = ""
        para = hdr[i].paragraphs[0]
        run = para.add_run(col["label"])
        run.bold = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        if col["align"] == "right":
            para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _shade(hdr[i], "101D3A")

    rows = section["rows"] or [{}]
    for row in rows:
        cells = table.add_row().cells
        for i, col in enumerate(cols):
            value = row.get(col["key"])
            if col["type"] == "money":
                text = _money(value)
            elif col["type"] == "date":
                text = _date(value)
            else:
                text = "" if value is None else str(value)
            para = cells[i].paragraphs[0]
            run = para.add_run(text)
            run.font.size = Pt(8.5)
            if col["align"] == "right":
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    totals = section.get("totals_multi") or section.get("totals")
    if totals:
        cells = table.add_row().cells
        for i, col in enumerate(cols):
            if col["type"] == "money":
                text = _money(totals.get(col["key"]))
            else:
                text = totals.get("label", "Total") if i == 0 else ""
            para = cells[i].paragraphs[0]
            run = para.add_run(text)
            run.bold = True
            run.font.size = Pt(8.5)
            if col["align"] == "right":
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _shade(cells[i], "E8EDF9")

    # column widths
    total_width = sum(c.get("width", 1.0) for c in cols) or 1
    usable = 17.0  # cm (A4 portrait with 2cm margins)
    for i, col in enumerate(cols):
        width = Cm(usable * col.get("width", 1.0) / total_width)
        for cell in table.columns[i].cells:
            cell.width = width


def render_word(report):
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin = section.bottom_margin = Cm(1.8)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    _header_footer(doc, report)
    if not report.get("skip_cover"):
        _cover(doc, report)

    for sec in report["sections"]:
        heading = doc.add_heading(sec.get("title", "Report"), level=1)
        for run in heading.runs:
            run.font.color.rgb = NAVY
            run.font.size = Pt(15)
        if sec.get("status") or sec.get("result"):
            p = doc.add_paragraph()
            bits = []
            if sec.get("status"):
                bits.append(f"Status: {sec['status']}")
            if sec.get("result"):
                bits.append(f"{sec['result']['label']}: Rs. {_money(sec['result']['amount'])}")
            run = p.add_run("   |   ".join(bits))
            run.font.size = Pt(9)
            run.font.color.rgb = GREY
        _add_table(doc, sec)
        if sec.get("note"):
            p = doc.add_paragraph()
            run = p.add_run("Note: " + sec["note"])
            run.font.size = Pt(8.5)
            run.italic = True
            run.font.color.rgb = GREY
        doc.add_paragraph()

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
