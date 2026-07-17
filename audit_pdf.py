# -*- coding: utf-8 -*-
"""PDF-Report-Generator fuer Website-Audits - modernes Layout mit Logo & Icons"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

NAVY = colors.HexColor('#0b1220')
ACCENT = colors.HexColor('#2f8bff')
GREEN = colors.HexColor('#22c55e')
RED = colors.HexColor('#ef4444')
GREY = colors.HexColor('#6b7280')
LIGHT_BG = colors.HexColor('#f4f6fb')

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

CATEGORY_ICONS = {
    'Indexierung': 'ⓘ', 'Technik': '⚙', 'SEO': '↗', 'Inhalte': '✎',
    'Rechtlich': '⚖', 'Performance': '⚡',
}


def _footer(c, contact):
    c.setFillColor(GREY)
    c.setFont('Helvetica', 8)
    line = f"{contact['name']} | {contact['contact_person']} | {contact['email']} | {contact['phone']} | {contact['web']}"
    c.drawCentredString(PAGE_W / 2, 10 * mm, line)
    c.setStrokeColor(colors.HexColor('#e5e7eb'))
    c.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)


def _new_page_header(c, title, contact):
    c.showPage()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 20 * mm, PAGE_W, 20 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(MARGIN, PAGE_H - 13 * mm, title)
    _footer(c, contact)
    return PAGE_H - 30 * mm


def _status_icon(c, x, y, status):
    r = 3.2 * mm
    if status is True:
        c.setFillColor(GREEN)
        c.circle(x, y, r, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(x, y - 2.6, '✓')
    elif status is False:
        c.setFillColor(RED)
        c.circle(x, y, r, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(x, y - 2.6, '✕')
    else:
        c.setFillColor(GREY)
        c.circle(x, y, r, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(x, y - 2.6, '?')


def _wrap_text(text, font, size, max_width):
    words = text.split(' ')
    lines, cur = [], ''
    for w in words:
        test = (cur + ' ' + w).strip()
        if stringWidth(test, font, size) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def generate_audit_pdf(result, logo_path, contact, output_path):
    c = canvas.Canvas(output_path, pagesize=A4)

    # ── Deckblatt ──────────────────────────────────────────────
    if logo_path and os.path.exists(logo_path):
        try:
            c.drawImage(logo_path, 0, PAGE_H - 90 * mm, width=PAGE_W, height=90 * mm,
                        preserveAspectRatio=False, mask='auto')
        except Exception:
            c.setFillColor(NAVY)
            c.rect(0, PAGE_H - 90 * mm, PAGE_W, 90 * mm, fill=1, stroke=0)
    else:
        c.setFillColor(NAVY)
        c.rect(0, PAGE_H - 90 * mm, PAGE_W, 90 * mm, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 24)
    c.drawString(MARGIN, PAGE_H - 110 * mm, "Website-Analyse")
    c.setFont('Helvetica', 13)
    c.setFillColor(GREY)
    c.drawString(MARGIN, PAGE_H - 118 * mm, result.get('url', ''))
    ts = result.get('timestamp', '')[:10]
    c.drawString(MARGIN, PAGE_H - 125 * mm, f"Erstellt am {ts}")

    summary = result.get('summary', {})
    score = summary.get('score', 0)
    score_color = GREEN if score >= 75 else (colors.HexColor('#f59e0b') if score >= 50 else RED)

    c.setFillColor(score_color)
    c.circle(PAGE_W - 45 * mm, PAGE_H - 118 * mm, 22 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 22)
    c.drawCentredString(PAGE_W - 45 * mm, PAGE_H - 116 * mm, f"{score}")
    c.setFont('Helvetica', 9)
    c.drawCentredString(PAGE_W - 45 * mm, PAGE_H - 123 * mm, "SCORE")

    y = PAGE_H - 150 * mm
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(MARGIN, y, "Zusammenfassung")
    y -= 8 * mm
    c.setFont('Helvetica', 10.5)
    c.setFillColor(colors.HexColor('#374151'))
    lines_summary = [
        f"Gesamtzahl geprueften Punkte: {summary.get('total_findings', 0)}",
        f"Gefundene Mängel/Optimierungsbedarf: {summary.get('issues_count', 0)}",
        f"Davon kritisch (dringender Handlungsbedarf): {summary.get('critical_issues', 0)}",
    ]
    for line in lines_summary:
        c.drawString(MARGIN, y, "•  " + line)
        y -= 6.5 * mm

    y -= 4 * mm
    c.setFont('Helvetica-Oblique', 9.5)
    c.setFillColor(GREY)
    intro = ("Dieser Report zeigt Ihnen auf einen Blick, wo Ihre Website bereits gut aufgestellt ist "
             "und wo konkreter Handlungsbedarf besteht - inklusive praktischer Empfehlungen.")
    for l in _wrap_text(intro, 'Helvetica-Oblique', 9.5, PAGE_W - 2 * MARGIN):
        c.drawString(MARGIN, y, l)
        y -= 5 * mm

    _footer(c, contact)

    # ── Kategorie-Seiten ───────────────────────────────────────
    findings = result.get('findings', {})
    for cat_name, items in findings.items():
        y = _new_page_header(c, f"{CATEGORY_ICONS.get(cat_name, '•')}  {cat_name}", contact)

        for f in items:
            if y < 35 * mm:
                y = _new_page_header(c, f"{CATEGORY_ICONS.get(cat_name, '•')}  {cat_name} (Fortsetzung)", contact)

            box_top = y
            _status_icon(c, MARGIN + 4 * mm, y - 3 * mm, f.get('status'))

            c.setFillColor(NAVY)
            c.setFont('Helvetica-Bold', 10.5)
            c.drawString(MARGIN + 12 * mm, y, f.get('signal', ''))

            prio = f.get('priority', '')
            prio_color = RED if prio == 'KRITISCH' else (colors.HexColor('#f59e0b') if prio == 'HOCH' else GREY)
            c.setFillColor(prio_color)
            c.setFont('Helvetica-Bold', 7.5)
            c.drawRightString(PAGE_W - MARGIN, y, prio)

            y -= 5.5 * mm
            c.setFont('Helvetica', 9)
            c.setFillColor(colors.HexColor('#374151'))
            for l in _wrap_text(f.get('description', ''), 'Helvetica', 9, PAGE_W - 2 * MARGIN - 12 * mm):
                c.drawString(MARGIN + 12 * mm, y, l)
                y -= 4.8 * mm

            rec = f.get('recommendation', '')
            if rec:
                c.setFont('Helvetica-Oblique', 8.5)
                c.setFillColor(ACCENT)
                for l in _wrap_text("Empfehlung: " + rec, 'Helvetica-Oblique', 8.5, PAGE_W - 2 * MARGIN - 12 * mm):
                    c.drawString(MARGIN + 12 * mm, y, l)
                    y -= 4.6 * mm

            y -= 3 * mm
            c.setStrokeColor(colors.HexColor('#eef0f5'))
            c.line(MARGIN, y, PAGE_W - MARGIN, y)
            y -= 6 * mm

    # ── Abschlussseite: Call-to-Action ────────────────────────
    y = _new_page_header(c, "Naechste Schritte", contact)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(MARGIN, y, "Wir helfen Ihnen, das umzusetzen.")
    y -= 10 * mm
    c.setFont('Helvetica', 10.5)
    c.setFillColor(colors.HexColor('#374151'))
    cta_text = (f"{contact['name']} hat alle oben genannten Punkte bereits fuer viele Kunden erfolgreich "
                f"umgesetzt. Melden Sie sich gerne fuer ein unverbindliches Gespraech - wir zeigen Ihnen, "
                f"wie Ihre Website moderner, schneller und rechtssicher wird.")
    for l in _wrap_text(cta_text, 'Helvetica', 10.5, PAGE_W - 2 * MARGIN):
        c.drawString(MARGIN, y, l)
        y -= 6 * mm

    y -= 8 * mm
    c.setFillColor(ACCENT)
    c.roundRect(MARGIN, y - 22 * mm, PAGE_W - 2 * MARGIN, 22 * mm, 3 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 12)
    c.drawCentredString(PAGE_W / 2, y - 9 * mm, contact['contact_person'])
    c.setFont('Helvetica', 10.5)
    c.drawCentredString(PAGE_W / 2, y - 15 * mm, f"{contact['email']}  |  {contact['phone']}  |  {contact['web']}")

    c.save()
    return output_path
