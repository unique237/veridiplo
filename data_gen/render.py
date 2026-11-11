"""PDF / image rendering (reportlab): PV tables, graduation lists, diploma specimens.

Every page carries the diagonal watermark "SPECIMEN – SYNTHETIC DATA".
Emblems, stamps and signatures are abstract vector doodles - never real ones.
"""
from __future__ import annotations

import math
import random
from datetime import date

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

rl_config.useA85 = 0
WATERMARK = "SPECIMEN – SYNTHETIC DATA"
ROWS_PER_PAGE = 25
HEADER_ROW_H = 28
ROW_H = 18

MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
             "octobre", "novembre", "décembre"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]

PV_COLS_FR = ["N°", "Matricule", "Nom et prénoms", "Moyenne", "Mention", "Décision"]
PV_COLS_EN = ["N°", "Matricule", "Nom et prénoms<br/>Name", "Moyenne<br/>Average", "Mention<br/>Grade",
              "Décision<br/>Decision"]
PV_COL_W = [26, 72, 208, 52, 86, 71]


def fr_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d}{'er' if d == 1 else ''} {MONTHS_FR[m - 1]} {y}"


def en_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTHS_EN[m - 1]} {d}, {y}"


def num_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d:02d}/{m:02d}/{y}"


def long_date(iso: str, lang: str) -> str:
    return en_date(iso) if lang == "en" else fr_date(iso)


# ----------------------------------------------------------------------------- primitives
def draw_watermark(c: canvas.Canvas, w: float, h: float, alpha: float = 0.16, size: float | None = None) -> None:
    c.saveState()
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.setFillAlpha(alpha)
    ang = math.degrees(math.atan2(h, w))
    diag = math.hypot(w, h)
    size = size or min(54, diag * 0.062)
    c.translate(w / 2, h / 2)
    c.rotate(ang)
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(0, -size / 3, WATERMARK)
    c.restoreState()


def draw_footer(c: canvas.Canvas, w: float, page: int, pages: int, lang: str = "fr", y: float = 22) -> None:
    c.saveState()
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    note = ("Document fictif généré à des fins de démonstration - aucune valeur légale"
            if lang != "en" else "Fictional document generated for demonstration - no legal value")
    c.drawCentredString(w / 2, y, f"{WATERMARK}  |  {note}  |  Page {page}/{pages}")
    c.restoreState()


def draw_signature(c: canvas.Canvas, x: float, y: float, rng: random.Random, width: float = 90, ink=(0.1, 0.1, 0.45)) -> None:
    """Abstract fictional signature squiggle."""
    c.saveState()
    c.setStrokeColorRGB(*ink)
    c.setLineWidth(1.1)
    pts = [(x + i * width / 7, y + rng.uniform(-9, 14) * (1 if i % 2 else -1)) for i in range(8)]
    p = c.beginPath()
    p.moveTo(*pts[0])
    for i in range(1, len(pts) - 2, 3):
        p.curveTo(*pts[i], *pts[i + 1], *pts[i + 2])
    p.curveTo(pts[-2][0], pts[-2][1] + 10, pts[-1][0] + 12, pts[-1][1] - 8, x + width + 16, y - 6)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def draw_stamp(c: canvas.Canvas, cx: float, cy: float, label: str, r: float = 34, color=(0.1, 0.25, 0.6)) -> None:
    """Fictional round stamp - deliberately generic."""
    c.saveState()
    c.setStrokeColorRGB(*color)
    c.setFillColorRGB(*color)
    c.setStrokeAlpha(0.7)
    c.setFillAlpha(0.7)
    c.setLineWidth(1.4)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.setLineWidth(0.6)
    c.circle(cx, cy, r - 5, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(cx, cy + 5, "CACHET FICTIF")
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(cx, cy - 6, label[:8])
    c.setFont("Helvetica", 5.5)
    c.drawCentredString(cx, cy - 15, "SPECIMEN")
    c.restoreState()


def draw_emblem(c: canvas.Canvas, cx: float, cy: float, initials: str, r: float = 26, color=(0.15, 0.3, 0.55)) -> None:
    """Fictional abstract emblem (nested polygon + initials)."""
    c.saveState()
    c.setStrokeColorRGB(*color)
    c.setFillColorRGB(*color)
    c.setLineWidth(1.5)
    for k, rr in enumerate((r, r * 0.72)):
        p = c.beginPath()
        for i in range(6):
            a = math.radians(60 * i + (30 if k else 0))
            (p.moveTo if i == 0 else p.lineTo)(cx + rr * math.cos(a), cy + rr * math.sin(a))
        p.close()
        c.drawPath(p, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(cx, cy - 3.5, initials[:4])
    c.restoreState()


# ----------------------------------------------------------------------------- PV
def render_pv(spec: dict, out_pdf: str) -> dict:
    """Render a deliberation minute with a real text layer.

    spec keys: institution{name_fr,name_en,city,language,initials}, programme{name_fr,name_en,level},
    session_year, delib_date, academic_year, jury_president, signatory{name,title}, lang, rows[],
    key (for rng).  Returns layout info: {pages, rows: [{n, page, bbox_pt}]}.
    """
    w, h = A4
    lang = spec["lang"]
    inst, prog = spec["institution"], spec["programme"]
    rows = spec["rows"]
    pages = max(1, math.ceil(len(rows) / ROWS_PER_PAGE))
    c = canvas.Canvas(out_pdf, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(f"PV {inst['id']} {prog['id']} {spec['session_year']} - {WATERMARK}")
    c.setAuthor("VeriDiplo synthetic data generator")
    c.setSubject(WATERMARK)
    rng = random.Random(f"sig:{spec['key']}")
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10)
    cellb = ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.5, leading=10)
    cols = PV_COLS_EN if lang == "en" else PV_COLS_FR
    layout_rows = []
    session_label = long_date(spec["delib_date"], lang)
    month_label = (MONTHS_EN if lang == "en" else MONTHS_FR)[int(spec["delib_date"][5:7]) - 1]
    for pg in range(pages):
        draw_watermark(c, w, h)
        # ---- header
        y = h - 50
        if pg == 0:
            draw_emblem(c, 62, h - 62, inst["initials"])
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(w / 2 + 20, y, inst["name_fr"].upper())
            c.setFont("Helvetica-Oblique", 9)
            c.drawCentredString(w / 2 + 20, y - 13, inst["name_en"])
            c.setFont("Helvetica", 9)
            c.drawCentredString(w / 2 + 20, y - 25, f"{inst['city']} - établissement fictif / fictional institution")
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString(w / 2, y - 52, "PROCÈS-VERBAL DE DÉLIBÉRATION" + (" / MINUTES OF DELIBERATION" if lang == "en" else ""))
            c.setFont("Helvetica", 10)
            prog_name = prog["name_en"] if lang == "en" else prog["name_fr"]
            lines = [
                f"Filière / Programme : {prog_name} ({prog['level']})",
                f"Session de {month_label} {spec['session_year']} - Année académique {spec['academic_year']}",
                f"Date de délibération : {session_label}",
                f"Président du jury : {spec['jury_president']}",
                f"Signataire : {spec['signatory']['name']}, {spec['signatory']['title']}",
            ]
            yy = y - 72
            for ln in lines:
                c.drawString(45, yy, ln)
                yy -= 14
            y_top = yy - 6
        else:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(45, h - 50, f"PV {inst['id']} - {prog['name_fr']} - session {spec['session_year']} (suite)")
            y_top = h - 70
        # ---- table
        chunk = rows[pg * ROWS_PER_PAGE:(pg + 1) * ROWS_PER_PAGE]
        data = [[Paragraph(t, cellb) for t in cols]]
        for r in chunk:
            data.append([str(r["n"]), r["matricule"], r["name"], r["average"], r["mention"], r["decision"]])
        t = Table(data, colWidths=PV_COL_W, rowHeights=[HEADER_ROW_H] + [ROW_H] * len(chunk))
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 1), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.86, 0.86, 0.86)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"), ("ALIGN", (3, 1), (3, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        tw, th = t.wrapOn(c, w, h)
        t.drawOn(c, 40, y_top - th)
        for i, r in enumerate(chunk):
            top = y_top - HEADER_ROW_H - ROW_H * i
            layout_rows.append({"n": r["n"], "page": pg + 1,
                                "bbox_pt": [40, round(h - top, 1), 40 + sum(PV_COL_W), round(h - (top - ROW_H), 1)]})
        # ---- signature block on last page
        if pg == pages - 1:
            by = 112
            c.setFont("Helvetica", 9)
            c.drawString(45, by + 14, f"Fait à {inst['city']}, le {long_date(spec['delib_date'], lang)}")
            c.setFont("Helvetica-Bold", 9)
            c.drawString(45, by, "Le Président du jury" + (" / Chair of the jury" if lang == "en" else ""))
            c.setFont("Helvetica", 9)
            c.drawString(45, by - 12, spec["jury_president"])
            draw_signature(c, 60, by - 34, rng)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(330, by, spec["signatory"]["title"])
            c.setFont("Helvetica", 9)
            c.drawString(330, by - 12, spec["signatory"]["name"])
            draw_signature(c, 345, by - 34, rng)
            draw_stamp(c, 500, by - 10, inst["initials"])
        draw_footer(c, w, pg + 1, pages, lang)
        c.showPage()
    c.save()
    return {"pages": pages, "rows": layout_rows}


# ----------------------------------------------------------------------------- graduation list
def render_list(spec: dict, out_pdf: str) -> int:
    """Digital-native results list ('Liste des admis') for one institution and year."""
    w, h = A4
    inst = spec["institution"]
    items = spec["items"]  # [{programme, name, mention}]
    per_page = 38
    pages = max(1, math.ceil(len(items) / per_page))
    c = canvas.Canvas(out_pdf, pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle(f"Liste des admis {inst['id']} {spec['year']} - {WATERMARK}")
    c.setAuthor("VeriDiplo synthetic data generator")
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=10)
    for pg in range(pages):
        draw_watermark(c, w, h)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(w / 2, h - 50, inst["name_fr"].upper())
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(w / 2, h - 70, f"LISTE DES ADMIS / LIST OF SUCCESSFUL CANDIDATES - SESSION {spec['year']}")
        chunk = items[pg * per_page:(pg + 1) * per_page]
        data = [["N°", "Nom et prénoms", "Filière / Programme", "Mention"]]
        for i, it in enumerate(chunk):
            data.append([str(pg * per_page + i + 1), it["name"], Paragraph(it["programme"], cell), it["mention"]])
        t = Table(data, colWidths=[28, 200, 200, 87], rowHeights=[20] + [16] * len(chunk))
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.88, 0.88, 0.88)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        tw, th = t.wrapOn(c, w, h)
        t.drawOn(c, 40, h - 90 - th)
        draw_footer(c, w, pg + 1, pages)
        c.showPage()
    c.save()
    return pages


# ----------------------------------------------------------------------------- diploma
PALETTES = {
    "UFN": (0.10, 0.30, 0.45), "UFB": (0.45, 0.15, 0.15), "FUM": (0.15, 0.40, 0.20),
    "ISFG": (0.40, 0.30, 0.10), "IPFM": (0.30, 0.15, 0.40),
}

_STR = {
    "fr": dict(
        diploma="DIPLÔME DE", head="{title} de {name}", confers="décerne à", born="Né(e) le {d} à {p}",
        the_dipl="le diplôme de", session="Session de {m} {y}", avg="Moyenne générale : {a}/20",
        mention="Mention : {m}", serial="N° du diplôme : {s}", done="Fait à {c}, le {d}",
        fiction="Établissement fictif - document de démonstration",
    ),
    "en": dict(
        diploma="DIPLOMA OF", head="{title} of {name}", confers="hereby confers upon", born="Born on {d} in {p}",
        the_dipl="the degree of", session="Session of {m} {y}", avg="Overall average: {a}/20",
        mention="Grade: {m}", serial="Diploma No.: {s}", done="Done at {c}, on {d}",
        fiction="Fictional institution - demonstration document",
    ),
}


def render_diploma(spec: dict, out_pdf: str) -> None:
    """Diploma specimen (A4 landscape). spec: typed fields + institution/programme/signatory dicts."""
    w, h = landscape(A4)
    lang = spec["language"]
    S = _STR[lang]
    inst, prog, sig = spec["institution"], spec["programme"], spec["signatory"]
    col = PALETTES.get(inst["id"], (0.2, 0.2, 0.4))
    c = canvas.Canvas(out_pdf, pagesize=landscape(A4), invariant=1, pageCompression=1)
    c.setTitle(f"Diploma {spec['case_id']} - {WATERMARK}")
    c.setAuthor("VeriDiplo synthetic data generator")
    rng = random.Random(f"dip:{spec['case_id']}")
    # borders
    c.setStrokeColorRGB(*col)
    c.setLineWidth(5)
    c.rect(20, 20, w - 40, h - 40)
    c.setLineWidth(1.2)
    c.rect(30, 30, w - 60, h - 60)
    draw_watermark(c, w, h, alpha=0.15, size=52)
    draw_emblem(c, 90, h - 90, inst["initials"], r=34, color=col)
    c.setFillColorRGB(*col)
    c.setFont("Times-Bold", 20)
    name_main = inst["name_en"] if lang == "en" else inst["name_fr"]
    c.drawCentredString(w / 2 + 30, h - 80, name_main.upper())
    c.setFont("Times-Italic", 10)
    c.drawCentredString(w / 2 + 30, h - 96, S["fiction"])
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Bold", 26)
    level = prog["level"].upper()
    c.drawCentredString(w / 2, h - 140, f"{S['diploma']} {level}")
    c.setFont("Times-Roman", 12)
    if lang == "fr":
        head = ("La " if "trice" in sig["title"] else "Le ") + sig["title"]
    else:
        head = "The " + sig["title"]
    c.drawCentredString(w / 2, h - 168, f"{head} - {name_main}, {S['confers']}")
    c.setFont("Times-Bold", 22)
    c.drawCentredString(w / 2, h - 202, ("Nom et prénoms : " if lang == "fr" else "Name: ") + spec["holder_name"])
    c.setFont("Times-Roman", 13)
    bd = long_date(spec["birth_date"], lang)
    c.drawCentredString(w / 2, h - 228, S["born"].format(d=bd, p=spec["birth_place"]))
    prog_name = prog["name_en"] if lang == "en" else prog["name_fr"]
    c.drawCentredString(w / 2, h - 256, S["the_dipl"])
    c.setFont("Times-Bold", 17)
    c.drawCentredString(w / 2, h - 280, prog_name)
    c.setFont("Times-Roman", 13)
    months = MONTHS_EN if lang == "en" else MONTHS_FR
    avg = f"{spec['average']:.2f}".replace(".", "," if lang == "fr" else ".")
    c.drawCentredString(w / 2, h - 308, S["session"].format(m=months[spec["session_month"] - 1], y=spec["session_year"]))
    c.setFont("Times-Bold", 14)
    c.drawCentredString(w / 2, h - 330, S["avg"].format(a=avg) + "      " + S["mention"].format(m=spec["mention"]))
    c.setFont("Times-Roman", 12)
    c.drawString(60, 112, S["serial"].format(s=spec["serial"]))
    c.drawString(60, 92, S["done"].format(c=inst["city"], d=long_date(spec["issue_date"], lang)))
    c.setFont("Times-Bold", 12)
    c.drawCentredString(w - 190, 130, sig["title"])
    draw_signature(c, w - 235, 92, rng, width=100, ink=(0.05, 0.05, 0.4))
    c.setFont("Times-Roman", 12)
    c.drawCentredString(w - 190, 66, sig["name"])
    draw_stamp(c, w / 2 + 20, 98, inst["initials"], r=38, color=col)
    draw_footer(c, w, 1, 1, lang, y=40)
    c.showPage()
    c.save()
