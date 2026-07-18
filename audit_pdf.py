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
GREEN_BG = colors.HexColor('#f0fdf4')
RED = colors.HexColor('#ef4444')
RED_BG = colors.HexColor('#fef2f2')
AMBER = colors.HexColor('#f59e0b')
GREY = colors.HexColor('#6b7280')
LIGHT_BG = colors.HexColor('#f4f6fb')
TEXT_DARK = colors.HexColor('#374151')
BORDER = colors.HexColor('#e5e7eb')

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

CATEGORY_ICONS = {
    'Indexierung': 'ⓘ', 'Technik': '⚙', 'SEO': '↗', 'Inhalte': '✎',
    'Rechtlich': '⚖', 'Performance': '⚡',
}

KATEGORIE_LABELS = {
    'design': ('Design & UX', 'ⓘ'),
    'seo': ('SEO', '↗'),
    'technik': ('Technik', '⚙'),
    'mobile': ('Mobile', '⚡'),
    'inhalte': ('Inhalte', '✎'),
    'rechtliches': ('Recht', '⚖'),
}

PRIORITY_EXPLAIN = {
    'KRITISCH': 'Sehr wichtig fuer jede Website - sollte immer erfuellt sein',
    'HOCH': 'Wichtig fuer Sichtbarkeit, Nutzererlebnis oder Rechtssicherheit',
    'MITTEL': 'Sinnvoll, aber nicht dringend',
    'INFO': 'Nur zur Information, hier nicht verpflichtend',
}

ELEMENT_LABELS = [
    ('has_ssl', 'SSL/HTTPS'), ('has_contact_form', 'Kontaktformular'),
    ('has_phone', 'Telefonnummer'), ('has_email', 'E-Mail-Adresse'),
    ('has_address', 'Adresse/Standort'), ('has_impressum', 'Impressum'),
    ('has_datenschutz', 'Datenschutz'), ('has_cookie_banner', 'Cookie-Banner'),
    ('has_google_maps', 'Google Maps'), ('has_social_links', 'Social Media Links'),
    ('has_reviews', 'Kundenbewertungen'), ('has_cta', 'Call-to-Action'),
    ('has_newsletter', 'Newsletter'), ('has_viewport', 'Mobile Viewport'),
    ('has_structured_data', 'Strukturierte Daten'),
]

LEGAL_UPDATES = [
    ("Barrierefreiheitsstaerkungsgesetz (BFSG)",
     "Seit 28.06.2025 muessen viele Online-Shops und digitale Dienstleistungen fuer "
     "Verbraucher barrierefrei nutzbar sein (u.a. gut lesbar, per Tastatur bedienbar, "
     "mit Screenreadern kompatibel). Betrifft insbesondere E-Commerce-Anbieter."),
    ("EU-Produktsicherheitsverordnung (GPSR)",
     "Seit Dezember 2024 muessen Online-Haendler, die Produkte an Verbraucher in der EU "
     "verkaufen, eine verantwortliche Person benennen und Sicherheits-/Warnhinweise klar "
     "auf der Produktseite angeben."),
    ("Digital Services Act (DSA)",
     "EU-weite Pflichten fuer Online-Plattformen und Marktplaetze: transparente AGB, "
     "Beschwerdemanagement und Kennzeichnung von Werbung/Algorithmen."),
    ("TTDSG & Cookie-Einwilligung",
     "Technisch nicht notwendige Cookies (z.B. Tracking, Werbung) duerfen weiterhin nur "
     "nach aktiver, informierter Einwilligung gesetzt werden - Kontrollen und Bussgelder "
     "haben zuletzt zugenommen."),
    ("Verpackungsgesetz (LUCID-Registrierung)",
     "Online-Shops, die Waren versenden, muessen sich vor dem ersten Verkauf im "
     "Verpackungsregister LUCID registrieren und sich an einem Rueknahmesystem beteiligen."),
    ("Widerrufsrecht bei Online-Shops",
     "Verbraucher haben bei Online-Kaeufen ein 14-taegiges Widerrufsrecht (Paragraph 355 BGB). "
     "Die Widerrufsbelehrung muss vor Vertragsschluss klar zugaenglich sein - siehe "
     "Abschnitt 'Widerrufsbutton' in diesem Report."),
]


def _footer(c, contact):
    c.setFillColor(GREY)
    c.setFont('Helvetica', 8)
    line = f"{contact['name']} | {contact['contact_person']} | {contact['email']} | {contact['phone']} | {contact['web']}"
    c.drawCentredString(PAGE_W / 2, 10 * mm, line)
    c.setStrokeColor(BORDER)
    c.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)


def _new_page_header(c, title, contact, subtitle=None):
    c.showPage()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 20 * mm, PAGE_W, 20 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(MARGIN, PAGE_H - 13 * mm, title)
    _footer(c, contact)
    y = PAGE_H - 30 * mm
    if subtitle:
        c.setFillColor(GREY)
        c.setFont('Helvetica-Oblique', 9)
        for l in _wrap_text(subtitle, 'Helvetica-Oblique', 9, PAGE_W - 2 * MARGIN):
            c.drawString(MARGIN, y, l)
            y -= 4.8 * mm
        y -= 3 * mm
    return y


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


def _grade_color(note):
    if not note:
        return GREY
    note = note.upper()[:1]
    if note in ('A', 'B'):
        return GREEN
    if note == 'C':
        return AMBER
    return RED


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


def _draw_wrapped(c, text, x, y, font, size, max_width, color, line_h):
    c.setFillColor(color)
    c.setFont(font, size)
    for l in _wrap_text(text, font, size, max_width):
        c.drawString(x, y, l)
        y -= line_h
    return y


def _bullet_list(c, items, x, y, max_width, bullet, color, size=8.7, line_h=4.4):
    c.setFont('Helvetica', size)
    for item in items:
        c.setFillColor(color)
        c.setFont('Helvetica-Bold', size)
        c.drawString(x, y, bullet)
        lines = _wrap_text(item, 'Helvetica', size, max_width - 5 * mm)
        c.setFillColor(TEXT_DARK)
        c.setFont('Helvetica', size)
        for i, l in enumerate(lines):
            c.drawString(x + 5 * mm, y, l)
            y -= line_h
    return y


def _info_box(c, x, y, w, title, body_lines, border_color=ACCENT, bg=LIGHT_BG):
    """Zeichnet eine hellhinterlegte Box mit Titel + mehreren Textzeilen, gibt neue y zurueck."""
    pad = 4 * mm
    line_h = 4.6 * mm
    total_h = pad * 2 + 5 * mm + len(body_lines) * line_h
    c.setFillColor(bg)
    c.roundRect(x, y - total_h, w, total_h, 2.5 * mm, fill=1, stroke=0)
    c.setStrokeColor(border_color)
    c.setLineWidth(1.2)
    c.line(x, y - total_h, x, y)
    ty = y - pad - 3 * mm
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(x + pad, ty, title)
    ty -= 6 * mm
    c.setFont('Helvetica', 8.7)
    c.setFillColor(TEXT_DARK)
    for l in body_lines:
        c.drawString(x + pad, ty, l)
        ty -= line_h
    return y - total_h - 5 * mm


def generate_audit_pdf(result, logo_path, contact, output_path):
    c = canvas.Canvas(output_path, pagesize=A4)
    ai = result.get('ai_result') or {}
    kategorien = ai.get('kategorien') or {}
    elements = (result.get('html_analysis') or {}).get('elements') or {}

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
    score = ai.get('gesamtpunkte', summary.get('score', 0))
    note = ai.get('gesamtnote', '')
    score_color = _grade_color(note) if note else (GREEN if score >= 75 else (AMBER if score >= 50 else RED))

    c.setFillColor(score_color)
    c.circle(PAGE_W - 45 * mm, PAGE_H - 118 * mm, 22 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 22)
    c.drawCentredString(PAGE_W - 45 * mm, PAGE_H - 116 * mm, f"{score}")
    c.setFont('Helvetica', 9)
    c.drawCentredString(PAGE_W - 45 * mm, PAGE_H - 123 * mm, f"SCORE  ({note or '-'})" if note else "SCORE")

    y = PAGE_H - 148 * mm

    if ai.get('ist_altbacken') is not None:
        pill_text = "✓ Design wirkt modern" if not ai['ist_altbacken'] else "✕ Design wirkt veraltet"
        pill_color = GREEN if not ai['ist_altbacken'] else RED
        pill_bg = GREEN_BG if not ai['ist_altbacken'] else RED_BG
        pw = stringWidth(pill_text, 'Helvetica-Bold', 9) + 8 * mm
        c.setFillColor(pill_bg)
        c.roundRect(MARGIN, y - 6 * mm, pw, 8 * mm, 4 * mm, fill=1, stroke=0)
        c.setFillColor(pill_color)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(MARGIN + 4 * mm, y - 3.6 * mm, pill_text)
        y -= 13 * mm

    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(MARGIN, y, "Zusammenfassung")
    y -= 7 * mm

    if ai.get('gesamtbewertung'):
        c.setFont('Helvetica', 10)
        y = _draw_wrapped(c, ai['gesamtbewertung'], MARGIN, y, 'Helvetica', 10,
                           PAGE_W - 2 * MARGIN, TEXT_DARK, 5 * mm)
        y -= 4 * mm

    c.setFont('Helvetica', 10.5)
    c.setFillColor(TEXT_DARK)
    lines_summary = [
        f"Gesamtzahl geprueften Punkte: {summary.get('total_findings', 0)}",
        f"Gefundene Maengel/Optimierungsbedarf: {summary.get('issues_count', 0)}",
        f"Davon kritisch (dringender Handlungsbedarf): {summary.get('critical_issues', 0)}",
    ]
    for line in lines_summary:
        c.drawString(MARGIN, y, "•  " + line)
        y -= 6.5 * mm

    y -= 4 * mm
    intro = ("Dieser Report zeigt Ihnen auf einen Blick, wo Ihre Website bereits gut aufgestellt ist "
             "und wo konkreter Handlungsbedarf besteht - inklusive praktischer Empfehlungen. Ein gruener "
             "Haken bedeutet immer: dieser Punkt ist bei Ihnen bereits erfuellt.")
    c.setFillColor(GREY)
    for l in _wrap_text(intro, 'Helvetica-Oblique', 9.5, PAGE_W - 2 * MARGIN):
        c.setFont('Helvetica-Oblique', 9.5)
        c.drawString(MARGIN, y, l)
        y -= 5 * mm

    _footer(c, contact)

    # ── Detailbewertung (KI-Kategorien) ───────────────────────
    if kategorien:
        y = _new_page_header(c, "Detailbewertung nach Themenbereich", contact,
                              "Jeder Bereich wird mit einer Note (A=sehr gut bis F=mangelhaft) "
                              "bewertet - inklusive konkreter Probleme und Empfehlungen.")
        order = ['design', 'seo', 'technik', 'mobile', 'inhalte', 'rechtliches']
        for key in order:
            kat = kategorien.get(key)
            if not kat:
                continue
            label, icon = KATEGORIE_LABELS.get(key, (key.title(), '•'))

            if y < 55 * mm:
                y = _new_page_header(c, "Detailbewertung nach Themenbereich (Fortsetzung)", contact)

            box_top = y
            c.setFillColor(_grade_color(kat.get('note')))
            c.circle(MARGIN + 6 * mm, y - 4 * mm, 6 * mm, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 11)
            c.drawCentredString(MARGIN + 6 * mm, y - 6.2 * mm, str(kat.get('note', '-')))

            c.setFillColor(NAVY)
            c.setFont('Helvetica-Bold', 12)
            c.drawString(MARGIN + 16 * mm, y - 2 * mm, f"{icon}  {label}")
            c.setFillColor(GREY)
            c.setFont('Helvetica', 9)
            c.drawRightString(PAGE_W - MARGIN, y - 2 * mm, f"{kat.get('punkte', '-')}/100 Punkte")

            y -= 10 * mm
            if kat.get('bewertung'):
                y = _draw_wrapped(c, kat['bewertung'], MARGIN + 16 * mm, y, 'Helvetica', 9,
                                   PAGE_W - 2 * MARGIN - 16 * mm, TEXT_DARK, 4.5 * mm)
            y -= 2 * mm

            probleme = kat.get('probleme') or []
            if probleme:
                c.setFillColor(RED)
                c.setFont('Helvetica-Bold', 8.7)
                c.drawString(MARGIN + 16 * mm, y, "Probleme:")
                y -= 5 * mm
                y = _bullet_list(c, probleme, MARGIN + 16 * mm, y,
                                  PAGE_W - 2 * MARGIN - 16 * mm, '✕', RED)
                y -= 2 * mm

            empfehlungen = kat.get('empfehlungen') or []
            if empfehlungen:
                c.setFillColor(GREEN)
                c.setFont('Helvetica-Bold', 8.7)
                c.drawString(MARGIN + 16 * mm, y, "Empfehlungen:")
                y -= 5 * mm
                y = _bullet_list(c, empfehlungen, MARGIN + 16 * mm, y,
                                  PAGE_W - 2 * MARGIN - 16 * mm, '✓', GREEN)

            y -= 3 * mm
            c.setStrokeColor(BORDER)
            c.line(MARGIN, y, PAGE_W - MARGIN, y)
            y -= 8 * mm

    # ── Legende: Was bedeuten Status & Prioritaet ─────────────
    y = _new_page_header(c, "Wie ist dieser Report zu lesen?", contact)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN, y, "Status-Symbole")
    y -= 8 * mm
    for status, label in [(True, 'Dieser Punkt ist bei Ihnen bereits erfuellt - hier besteht kein Handlungsbedarf.'),
                           (False, 'Dieser Punkt ist bei Ihnen noch nicht erfuellt - siehe Empfehlung.'),
                           (None, 'Wird gerade noch geprueft bzw. war zum Zeitpunkt der Analyse nicht ermittelbar.')]:
        _status_icon(c, MARGIN + 4 * mm, y - 3 * mm, status)
        y = _draw_wrapped(c, label, MARGIN + 12 * mm, y, 'Helvetica', 9.5,
                           PAGE_W - 2 * MARGIN - 12 * mm, TEXT_DARK, 4.6 * mm)
        y -= 4 * mm

    y -= 4 * mm
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN, y, "Prioritaets-Stufen (HOCH / MITTEL / KRITISCH)")
    y -= 7 * mm
    c.setFont('Helvetica', 9.5)
    y = _draw_wrapped(c,
        "Wichtig: Die Prioritaet zeigt, wie bedeutsam ein Pruefpunkt fuer Webseiten allgemein ist - "
        "sie sagt nichts darueber aus, ob Ihre Seite ihn erfuellt. Ein gruener Haken mit der Markierung "
        "'KRITISCH' bedeutet also: ein sehr wichtiger Punkt, den Ihre Seite bereits erfuellt.",
        MARGIN, y, 'Helvetica', 9.5, PAGE_W - 2 * MARGIN, TEXT_DARK, 4.8 * mm)
    y -= 5 * mm

    for prio, color in [('KRITISCH', RED), ('HOCH', AMBER), ('MITTEL', GREY)]:
        c.setFillColor(color)
        c.roundRect(MARGIN, y - 5.5 * mm, 24 * mm, 6.5 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 8)
        c.drawCentredString(MARGIN + 12 * mm, y - 3.5 * mm, prio)
        c.setFillColor(TEXT_DARK)
        c.setFont('Helvetica', 9.5)
        c.drawString(MARGIN + 28 * mm, y - 3.5 * mm, PRIORITY_EXPLAIN[prio])
        y -= 10 * mm

    # ── Inhalts-Checkliste ─────────────────────────────────────
    if elements:
        y = _new_page_header(c, "Inhalts-Checkliste", contact,
                              "Diese Bausteine sind auf vielen guten Websites vorhanden. Nicht jeder "
                              "Punkt ist fuer jede Branche zwingend - sehen Sie es als Anregung.")
        col_w = (PAGE_W - 2 * MARGIN - 6 * mm) / 2
        col = 0
        row_y = y
        for key, label in ELEMENT_LABELS:
            ok = bool(elements.get(key))
            x = MARGIN + col * (col_w + 6 * mm)
            box_h = 9 * mm
            c.setFillColor(GREEN_BG if ok else RED_BG)
            c.roundRect(x, row_y - box_h, col_w, box_h, 2 * mm, fill=1, stroke=0)
            c.setFillColor(GREEN if ok else RED)
            c.setFont('Helvetica-Bold', 10)
            c.drawString(x + 3 * mm, row_y - 6 * mm, '✓' if ok else '✕')
            c.setFont('Helvetica', 9)
            c.drawString(x + 9 * mm, row_y - 6 * mm, label)
            col += 1
            if col == 2:
                col = 0
                row_y -= box_h + 3 * mm
                if row_y < 30 * mm:
                    row_y = _new_page_header(c, "Inhalts-Checkliste (Fortsetzung)", contact)

    # ── Empfohlene Massnahmen ──────────────────────────────────
    massnahmen = ai.get('top_massnahmen') or []
    if massnahmen:
        y = _new_page_header(c, "Empfohlene Massnahmen", contact,
                              "Die wichtigsten naechsten Schritte, priorisiert nach Dringlichkeit.")
        prio_color = {'KRITISCH': RED, 'HOCH': AMBER, 'MITTEL': GREY}
        for i, m in enumerate(massnahmen, 1):
            if y < 40 * mm:
                y = _new_page_header(c, "Empfohlene Massnahmen (Fortsetzung)", contact)
            color = prio_color.get(m.get('prioritaet'), GREY)
            c.setFillColor(color)
            c.roundRect(MARGIN, y - 5.5 * mm, 22 * mm, 6.5 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 7.5)
            c.drawCentredString(MARGIN + 11 * mm, y - 3.5 * mm, m.get('prioritaet', ''))
            c.setFillColor(NAVY)
            c.setFont('Helvetica-Bold', 10)
            c.drawString(MARGIN + 26 * mm, y - 3.5 * mm, f"{i}. {m.get('massnahme', '')}")
            y -= 9 * mm
            if m.get('begruendung'):
                y = _draw_wrapped(c, m['begruendung'], MARGIN + 26 * mm, y, 'Helvetica', 8.7,
                                   PAGE_W - 2 * MARGIN - 26 * mm, TEXT_DARK, 4.4 * mm)
            y -= 5 * mm

    # ── Kategorie-Detailseiten (technische Einzel-Pruefungen) ──
    findings = result.get('findings', {})
    for cat_name, items in findings.items():
        y = _new_page_header(c, f"{CATEGORY_ICONS.get(cat_name, '•')}  {cat_name}", contact)

        for f in items:
            if y < 35 * mm:
                y = _new_page_header(c, f"{CATEGORY_ICONS.get(cat_name, '•')}  {cat_name} (Fortsetzung)", contact)

            _status_icon(c, MARGIN + 4 * mm, y - 3 * mm, f.get('status'))

            c.setFillColor(NAVY)
            c.setFont('Helvetica-Bold', 10.5)
            c.drawString(MARGIN + 12 * mm, y, f.get('signal', ''))

            prio = f.get('priority', '')
            prio_color = RED if prio == 'KRITISCH' else (AMBER if prio == 'HOCH' else GREY)
            c.setFillColor(prio_color)
            c.setFont('Helvetica-Bold', 7.5)
            c.drawRightString(PAGE_W - MARGIN, y, prio)

            y -= 5.5 * mm
            c.setFont('Helvetica', 9)
            c.setFillColor(TEXT_DARK)
            for l in _wrap_text(f.get('description', ''), 'Helvetica', 9, PAGE_W - 2 * MARGIN - 12 * mm):
                c.drawString(MARGIN + 12 * mm, y, l)
                y -= 4.8 * mm

            # Erklaerender Hinweis, warum Status und Prioritaet nicht widersprechen
            if prio in ('KRITISCH', 'HOCH'):
                note_txt = ("(Wichtiger Pruefpunkt - bei Ihnen bereits erfuellt)" if f.get('status') is True
                             else "(Wichtiger Pruefpunkt - hier besteht Handlungsbedarf)" if f.get('status') is False
                             else "")
                if note_txt:
                    c.setFont('Helvetica-Oblique', 7.7)
                    c.setFillColor(GREY)
                    c.drawString(MARGIN + 12 * mm, y, note_txt)
                    y -= 4.4 * mm

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

        # Widerrufsrecht-Erklaerbox direkt im Anschluss an "Rechtlich"
        if cat_name == 'Rechtlich':
            widerruf = next((f for f in items if 'Widerruf' in f.get('signal', '')), None)
            if y < 65 * mm:
                y = _new_page_header(c, "⚖  Rechtlich (Fortsetzung)", contact)
            box_w = PAGE_W - 2 * MARGIN
            status_txt = ""
            if widerruf:
                if widerruf.get('status') is True and 'Kein Online-Shop' in widerruf.get('description', ''):
                    status_txt = "Bei Ihnen: kein Online-Shop mit Kauffunktion erkannt - daher hier nicht verpflichtend."
                elif widerruf.get('status') is True:
                    status_txt = "Bei Ihnen: Widerrufsbelehrung wurde gefunden."
                else:
                    status_txt = "Bei Ihnen: Ihre Seite wirkt wie ein Online-Shop, aber es wurde keine Widerrufsbelehrung gefunden."
            y = _info_box(c, MARGIN, y, box_w, "Was ist ein Widerrufsbutton?", [
                "Verbraucher duerfen einen online geschlossenen Kaufvertrag innerhalb von 14 Tagen",
                "ohne Angabe von Gruenden widerrufen (Fernabsatzrecht, Paragraph 355 BGB). Online-Shops",
                "muessen dieses Recht vor Vertragsschluss klar und leicht auffindbar erklaeren -",
                "ueblicherweise ueber eine eigene 'Widerruf'-Seite oder einen Link/Button im Checkout.",
                status_txt,
            ], border_color=ACCENT)

    # ── Neue gesetzliche Anforderungen ─────────────────────────
    y = _new_page_header(c, "Neue gesetzliche Anforderungen fuer Websites & Online-Shops", contact,
                          "Ein kurzer Ueberblick ueber aktuelle Regelungen, die fuer Betreiber von "
                          "Websites und Online-Shops in Deutschland/EU relevant sein koennen.")
    for title, body in LEGAL_UPDATES:
        if y < 45 * mm:
            y = _new_page_header(c, "Neue gesetzliche Anforderungen (Fortsetzung)", contact)
        c.setFillColor(NAVY)
        c.setFont('Helvetica-Bold', 10.5)
        c.drawString(MARGIN, y, title)
        y -= 5.5 * mm
        y = _draw_wrapped(c, body, MARGIN, y, 'Helvetica', 9, PAGE_W - 2 * MARGIN, TEXT_DARK, 4.6 * mm)
        y -= 6 * mm
    c.setFont('Helvetica-Oblique', 8)
    c.setFillColor(GREY)
    y = _draw_wrapped(c,
        "Hinweis: Dies ist eine allgemeine Uebersicht und ersetzt keine Rechtsberatung. Ob und welche "
        "Regelungen konkret gelten, haengt von Ihrem Geschaeftsmodell ab.",
        MARGIN, y, 'Helvetica-Oblique', 8, PAGE_W - 2 * MARGIN, GREY, 4 * mm)

    # ── Abschlussseite: Call-to-Action ────────────────────────
    y = _new_page_header(c, "Naechste Schritte", contact)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 13)
    c.drawString(MARGIN, y, "Wir helfen Ihnen, das umzusetzen.")
    y -= 10 * mm
    c.setFont('Helvetica', 10.5)
    c.setFillColor(TEXT_DARK)
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
