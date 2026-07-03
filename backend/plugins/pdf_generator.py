"""
PDF Generator Plugin

Creates PDF documents from structured content using reportlab.
"""
import json
import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem, PageBreak
from reportlab.platypus.flowables import HRFlowable

PLUGIN_NAME = "pdf-generator"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Creates PDF documents from structured content"


def tool_create_pdf(title: str = "Document", sections: str = "[]", output_path: str = "") -> str:
    """
    Create a PDF document with structured content.

    Parameters:
    - title: document title (displayed as centered heading)
    - sections: JSON array of section objects. Each object has:
        {"type": "heading", "text": "...", "level": 1}
        {"type": "paragraph", "text": "..."}
        {"type": "table", "headers": [...], "rows": [[...], ...]}
        {"type": "bullet_list", "items": ["...", ...]}
        {"type": "horizontal_rule"}
    - output_path: optional absolute path to save (auto-generated if empty)
    """
    if not output_path:
        from datetime import datetime
        output_dir = Path(os.getcwd()) / "data" / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(c for c in title if c.isascii() and (c.isalnum() or c in " _-")).strip()
        if not safe_title:
            safe_title = "document"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(output_dir / f"{safe_title}_{ts}.pdf")

    if not output_path.lower().endswith(".pdf"):
        output_path = output_path.rsplit(".", 1)[0] + ".pdf"

    try:
        parsed = json.loads(sections)
    except json.JSONDecodeError as e:
        return f"Error: invalid sections JSON - {e}"

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=25*mm,
        bottomMargin=25*mm,
        leftMargin=25*mm,
        rightMargin=25*mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=24,
        leading=30,
        textColor=HexColor("#4472C4"),
        alignment=1,
        spaceAfter=40,
    )
    heading_styles = {
        1: ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18, leading=22, spaceBefore=16, spaceAfter=8),
        2: ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=12, spaceAfter=6),
        3: ParagraphStyle("H3", parent=styles["Heading3"], fontSize=12, leading=15, spaceBefore=8, spaceAfter=4),
    }
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "CustomBullet",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        leftIndent=20,
        bulletIndent=8,
        spaceAfter=3,
    )

    elements = []

    if title:
        elements.append(Paragraph(title, title_style))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#4472C4")))
        elements.append(Spacer(1, 20))

    for sec in parsed:
        sec_type = sec.get("type", "paragraph")

        if sec_type == "heading":
            level = sec.get("level", 1)
            hs = heading_styles.get(level, heading_styles[1])
            elements.append(Paragraph(sec.get("text", ""), hs))

        elif sec_type == "paragraph":
            elements.append(Paragraph(sec.get("text", ""), body_style))

        elif sec_type == "horizontal_rule":
            elements.append(Spacer(1, 6))
            elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
            elements.append(Spacer(1, 6))

        elif sec_type == "table":
            headers = sec.get("headers", [])
            rows = sec.get("rows", [])
            if headers:
                data = [headers] + rows
                col_count = len(headers)
                available_width = A4[0] - 50*mm
                col_width = available_width / max(col_count, 1)

                t = Table(data, colWidths=[col_width] * col_count)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), HexColor("#4472C4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("FONTSIZE", (0, 1), (-1, -1), 10),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, HexColor("#D9E2F3")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                elements.append(t)
                elements.append(Spacer(1, 10))

        elif sec_type == "bullet_list":
            items = sec.get("items", [])
            bullets = []
            for item in items:
                bullets.append(ListItem(Paragraph(f"• {item}", bullet_style)))
            if bullets:
                elements.append(ListFlowable(bullets, bulletType="bullet", start="•"))
                elements.append(Spacer(1, 6))

    doc.build(elements)
    return f"PDF created successfully: {output_path}"
