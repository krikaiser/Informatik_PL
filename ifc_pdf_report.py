"""PDF-Bericht aus den Ergebnissen einer abgeschlossenen IFC-Prüfung."""

from datetime import date
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape

from ifc_pset_check import Audit, display_percentage, publish_export


def write_pdf_report(output: Path, audit: Audit, export_date: date) -> tuple[Path, ...]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import CondPageBreak, LongTable, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError("Für den PDF-Export bitte die Abhängigkeiten aus requirements.txt installieren.") from exc

    navy = colors.HexColor("#173F61")
    ink = colors.HexColor("#1E3141")
    pale = colors.HexColor("#F0F4F7")
    regular, bold = "Helvetica", "Helvetica-Bold"
    fonts = Path(environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    if (fonts / "arial.ttf").is_file() and (fonts / "arialbd.ttf").is_file():
        regular, bold = "ReportArial", "ReportArialBold"
        pdfmetrics.registerFont(TTFont(regular, str(fonts / "arial.ttf")))
        pdfmetrics.registerFont(TTFont(bold, str(fonts / "arialbd.ttf")))
    title = ParagraphStyle("Title", fontName=bold, fontSize=23, leading=27, textColor=navy)
    subtitle = ParagraphStyle(
        "Subtitle", fontName=regular, fontSize=10, leading=14, textColor=ink, spaceBefore=5, spaceAfter=14,
    )
    section = ParagraphStyle(
        "Section", fontName=bold, fontSize=13, leading=17,
        textColor=navy, spaceBefore=16, spaceAfter=6,
    )
    cell = ParagraphStyle("Cell", fontName=regular, fontSize=9, leading=12, textColor=ink)
    header = ParagraphStyle("Header", parent=cell, fontName=bold, textColor=colors.white)

    def paragraph(text, style=cell):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    def table(headings, rows, widths):
        content = [[paragraph(value, header) for value in headings]]
        content.extend([paragraph(value) for value in row] for row in rows)
        return LongTable(content, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT", style=[
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, navy),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])

    def footer(canvas, doc):
        canvas.setStrokeColor(pale)
        canvas.line(36, 30, doc.pagesize[0] - 36, 30)
        canvas.setFont(regular, 8)
        canvas.setFillColor(ink)
        canvas.drawString(36, 17, "IFC-Prüfbericht")
        canvas.drawRightString(doc.pagesize[0] - 36, 17, f"Seite {doc.page}")

    summary, schema = audit.summary, audit.schema
    share = display_percentage(summary["Anteil_Objekte_mit_Befund_an_allen_Objekten_Prozent"])
    story = [
        paragraph("IFC-Prüfbericht", title),
        paragraph(f"{audit.path.name}  |  {schema['Schema']}  |  Export am {export_date:%d.%m.%Y}", subtitle),
        table(
            ("Objekte im Zählbereich", "Geprüfte Objekte", "Objekte mit Befund", "Befundquote", "Einzelbefunde"),
            [(summary["Objekte_im_Zählbereich"], summary["Objekte_geprüft"],
              summary["Geprüfte_Objekte_mit_Befund"], share, summary["Anzahl_Attributbefunde"])],
            (152, 152, 152, 152, 150),
        ),
        CondPageBreak(80),
        paragraph(f"Attributprüfung ({len(audit.groups)} Befundgruppen)", section),
    ]
    if audit.groups:
        story.append(table(
            ("PSet", "Attribut", "Befund", "Gefunden in", "Objekte", "Anteil (%)", "IFC-Klassen (Anzahl)"),
            audit.groups, (105, 91, 112, 101, 57, 61, 231),
        ))
    else:
        story.append(paragraph("Keine Attributbefunde."))

    story.extend([CondPageBreak(80), paragraph(f"Schema-Prüfung ({schema['Anzahl_Befunde']} Befunde)", section)])
    if schema.get("Fehler"):
        story.extend([paragraph(f"Prüfung unvollständig. {schema['Fehler']}"), Spacer(1, 6)])
    if schema["Befunde"]:
        rows = []
        for finding in schema["Befunde"]:
            step_id = f"#{finding['STEP-ID']}" if finding["STEP-ID"] else ""
            message = "\n".join(part for part in (finding["Instanz"], finding["Meldung"]) if part)
            rows.append((step_id, finding["Stufe"], finding["Attribut"], message))
        story.append(table(("STEP-ID", "Stufe", "Attribut", "Meldung"), rows, (74, 58, 132, 494)))
    elif not schema.get("Fehler"):
        story.append(paragraph("Keine Schema-Befunde."))

    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output) as directory:
        path = Path(directory) / f"{export_date:%d%m%y}_QS_{audit.path.stem}.pdf"
        document = SimpleDocTemplate(
            str(path), pagesize=landscape(A4), title=f"IFC-Prüfbericht {audit.path.name}",
            leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=42,
        )
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return publish_export(output, (path,))
