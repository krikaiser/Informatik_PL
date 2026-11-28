"""PDF-Bericht aus den Ergebnissen einer abgeschlossenen IFC-Prüfung."""

from collections import defaultdict
from datetime import date
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape

from ifc_pset_check import STATUS_TEXT, Audit, display_percentage, publish_export


def checklist_rows(audit: Audit):
    groups = defaultdict(list)
    for pset, prop, finding, locations, *_ in audit.groups:
        groups[pset, prop].append((finding, locations))

    duplicate = STATUS_TEXT["DUPLICATE_PROPERTY"]
    for pset, checks in audit.coverage.items():
        for prop, coverage in checks.items():
            findings = groups[pset, prop]
            if findings:
                severity = "minor" if all(finding == duplicate for finding, _ in findings) else "error"
                labels = "\n".join(dict.fromkeys(finding for finding, _ in findings))
                locations = "\n".join(dict.fromkeys(location for _, location in findings if location))
            elif coverage.checked:
                severity, labels, locations = "ok", "", ""
                if coverage.checked < coverage.applicable:
                    labels = f"Geprüft bei {coverage.checked} von {coverage.applicable} Objekten"
            else:
                severity, locations = "", ""
                labels = "Nicht geprüft (PSet fehlt)" if coverage.applicable else "Keine passenden Objekte"
            yield (pset if not prop else "", prop, severity, labels, locations)


def write_pdf_report(output: Path, audit: Audit, export_date: date) -> tuple[Path, ...]:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            BaseDocTemplate, CondPageBreak, Frame, Image, LongTable, NextPageTemplate,
            PageBreak, PageTemplate, Paragraph, Spacer,
        )
    except ImportError as exc:
        raise RuntimeError("Für den PDF-Export bitte die Abhängigkeiten aus requirements.txt installieren.") from exc

    navy = colors.HexColor("#173F61")
    ink = colors.HexColor("#1E3141")
    pale = colors.HexColor("#F0F4F7")
    status_colours = {
        "ok": colors.HexColor("#96CF52"),
        "minor": colors.HexColor("#FFDF32"),
        "error": colors.HexColor("#DF5551"),
    }
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
    strong = ParagraphStyle("Strong", parent=cell, fontName=bold)
    marker = ParagraphStyle("Marker", parent=strong, alignment=TA_CENTER)

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
        page_width, _ = doc.pageTemplate.pagesize
        canvas.setStrokeColor(pale)
        canvas.line(36, 30, page_width - 36, 30)
        canvas.setFont(regular, 8)
        canvas.setFillColor(ink)
        canvas.drawString(36, 17, "IFC-Prüfbericht")
        canvas.drawRightString(page_width - 36, 17, f"Seite {doc.page}")

    def page(name, size):
        width, height = size
        frame = Frame(36, 42, width - 72, height - 78, id=name)
        return PageTemplate(id=name, frames=frame, pagesize=size, onPage=footer)

    from ifc_model_preview import render_model_preview

    try:
        preview = Image(render_model_preview(audit), width=510, height=510 * 540 / 1560, hAlign="CENTER")
    except OSError:
        preview = paragraph("Die geprüfte IFC-Datei steht für die Modellansicht nicht mehr unverändert zur Verfügung.")
    except (RuntimeError, ValueError):
        preview = paragraph("Aus der IFC-Datei konnte keine Modellansicht erstellt werden.")

    summary, schema = audit.summary, audit.schema
    share = display_percentage(summary["Anteil_Objekte_mit_Befund_an_allen_Objekten_Prozent"])
    story = [
        paragraph("IFC-Prüfbericht", title),
        paragraph(f"{audit.path.name}  |  {schema['Schema']}  |  Überprüft am {audit.checked_on:%d.%m.%Y}", subtitle),
        preview,
        paragraph("Attributprüfung", section),
        paragraph(f"Geprüfte PSet und Attribute in {audit.path.name}", cell),
        Spacer(1, 6),
    ]
    checklist = [[paragraph(label, strong) for label in ("PSet", "Property", "", "Befund", "Gefunden in")]]
    checklist_style = [
        ("BACKGROUND", (0, 0), (-1, 0), pale),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AAB5BE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for index, (pset, prop, severity, finding, locations) in enumerate(checklist_rows(audit), 1):
        checklist.append([
            paragraph(pset, strong), paragraph(prop), paragraph("x" if severity else "", marker),
            paragraph(finding), paragraph(locations),
        ])
        if pset:
            checklist_style.append(("BACKGROUND", (0, index), (-1, index), pale))
        if severity:
            checklist_style.append(("BACKGROUND", (2, index), (2, index), status_colours[severity]))
    story.append(LongTable(checklist, colWidths=(119, 132, 28, 130, 102), repeatRows=1, splitInRow=1,
                           style=checklist_style, hAlign="LEFT"))

    legend = (
        "Keine Korrektur erforderlich",
        "Kleine Korrektur erforderlich",
        "Korrektur erforderlich",
    )
    legend_table = LongTable(
        [[paragraph(""), paragraph(label)] for label in legend],
        colWidths=(45, 320), hAlign="LEFT",
        style=[
            ("BACKGROUND", (0, row), (0, row), colour)
            for row, colour in enumerate(status_colours.values())
        ],
    )
    story.extend([CondPageBreak(85), paragraph("Legende", section), legend_table])

    story.extend([
        NextPageTemplate("Details"),
        PageBreak(),
        paragraph("Prüfergebnisse im Detail", title),
        Spacer(1, 10),
        table(
            ("Objekte im Zählbereich", "Geprüfte Objekte", "Objekte mit Befund", "Befundquote", "Einzelbefunde"),
            [(summary["Objekte_im_Zählbereich"], summary["Objekte_geprüft"],
              summary["Geprüfte_Objekte_mit_Befund"], share, summary["Anzahl_Attributbefunde"])],
            (152, 152, 152, 152, 150),
        ),
        CondPageBreak(80),
        paragraph(f"Attributbefunde ({len(audit.groups)} Befundgruppen)", section),
    ])
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
        document = BaseDocTemplate(
            str(path), pagesize=A4, title=f"IFC-Prüfbericht {audit.path.name}",
            pageTemplates=[page("Prüfübersicht", A4), page("Details", landscape(A4))],
        )
        document.build(story)
        return publish_export(output, (path,))
