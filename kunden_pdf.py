"""PDFs fuer Kunden: monatlicher Wartungsbericht und Angebot - gleicher Look wie die Website-Analyse
(audit_pdf: Farben, Fusszeile mit ChefBlick-Kontakt)."""
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from audit_pdf import (ACCENT, AMBER, BORDER, GREEN, GREEN_BG, GREY, LIGHT_BG, MARGIN, NAVY, RED, RED_BG, TEXT_DARK,
                       _footer, _wrap_text)

PAGE_W, PAGE_H = A4
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, 'report_logo.png')
CONTACT = {'name': 'ChefBlick', 'contact_person': 'Stefan Mutter', 'email': 'team@chefblick.de',
           'phone': '+49 155 65505025', 'web': 'www.chefblick.de'}
INNER_W = PAGE_W - 2 * MARGIN


def _de(n, digits=0):
    return f'{n:,.{digits}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _eur(n):
    return f'{_de(n, 2)} €'


def _datum(iso):
    try:
        return datetime.fromisoformat(iso).strftime('%d.%m.%Y %H:%M')
    except (TypeError, ValueError):
        return str(iso or '')


def _small_logo():
    """Das Original-Logo hat 2 MB - im Kopf reicht eine kleine Kopie (haelt die Mail-Anhaenge klein)."""
    small = os.path.join(BASE_DIR, 'report_logo_small.jpg')
    if not os.path.exists(small) and os.path.exists(LOGO_PATH):
        try:
            from PIL import Image
            im = Image.open(LOGO_PATH).convert('RGB')
            im.thumbnail((600, 400))
            im.save(small, 'JPEG', quality=85)
        except Exception as e:
            print(f'[PDF] Logo nicht verkleinert: {e}')
            return LOGO_PATH
    return small if os.path.exists(small) else None


def _header(c, title, subtitle):
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 34 * mm, PAGE_W, 34 * mm, fill=1, stroke=0)
    logo = _small_logo()
    if logo:
        try:  # Logo rechts im Balken, Seitenverhaeltnis bleibt
            c.drawImage(logo, PAGE_W - MARGIN - 45 * mm, PAGE_H - 32 * mm, width=45 * mm, height=30 * mm,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 20)
    c.drawString(MARGIN, PAGE_H - 17 * mm, title)
    c.setFont('Helvetica', 10.5)
    c.setFillColor(colors.HexColor('#c7d2fe'))
    c.drawString(MARGIN, PAGE_H - 25 * mm, subtitle)
    _footer(c, CONTACT)
    return PAGE_H - 46 * mm


def _text(c, text, x, y, size=10, font='Helvetica', color=TEXT_DARK, width=INNER_W, line_h=None):
    line_h = line_h or size * 0.5 * mm
    c.setFont(font, size)
    c.setFillColor(color)
    for para in str(text or '').split('\n'):
        for line in (_wrap_text(para, font, size, width) if para.strip() else ['']):
            c.drawString(x, y, line)
            y -= line_h
    return y


def _new_page_if(c, y, need, title, subtitle):
    if y - need < 24 * mm:
        c.showPage()
        return _header(c, title, subtitle)
    return y


# ─── Wartungsbericht ──────────────────────────────────────────────────

def _tile(c, x, y, w, h, label, value, sub, tone):
    fg, bg = {'good': (GREEN, GREEN_BG), 'warn': (AMBER, colors.HexColor('#fffbeb')),
              'bad': (RED, RED_BG)}.get(tone, (ACCENT, LIGHT_BG))
    c.setFillColor(bg)
    c.setStrokeColor(BORDER)
    c.roundRect(x, y - h, w, h, 3 * mm, fill=1, stroke=1)
    c.setFillColor(fg)
    c.rect(x, y - h, 1.6 * mm, h, fill=1, stroke=0)
    c.setFillColor(GREY)
    c.setFont('Helvetica', 8.5)
    c.drawString(x + 5 * mm, y - 6.5 * mm, label)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 17)
    c.drawString(x + 5 * mm, y - 15 * mm, value)
    c.setFillColor(TEXT_DARK)
    c.setFont('Helvetica', 8)
    for i, line in enumerate(_wrap_text(sub, 'Helvetica', 8, w - 9 * mm)[:2]):
        c.drawString(x + 5 * mm, y - 20.5 * mm - i * 3.8 * mm, line)


def wartungsbericht(d, path):
    """d = kunden_engine.bericht_daten(...)"""
    st = d['stats']
    title, sub = 'Wartungsbericht', f"{d['monat']} · {d['url'].replace('https://', '')}"
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(f"Wartungsbericht {d['monat']} – {d['kunde']}")
    y = _header(c, title, sub)
    y = _text(c, f"für {d['kunde']}", MARGIN, y, 13, 'Helvetica-Bold', NAVY)
    y = _text(c, f"Erstellt am {d['erstellt']} von ChefBlick – Ihre Website wird rund um die Uhr alle 15 Minuten geprüft.",
              MARGIN, y - 1 * mm, 9, color=GREY)
    y -= 6 * mm

    has = bool(st.get('checks'))
    n_out = len(st.get('outages') or [])
    ssl = st.get('ssl_days')
    tiles = [
        ('Erreichbarkeit', f"{_de(st['uptime'], 2)} %" if has else '–',
         f"{_de(st['checks'])} Prüfungen im Monat" if has else 'Überwachung läuft seit Kurzem',
         'neutral' if not has else 'good' if st['uptime'] >= 99.5 else 'warn' if st['uptime'] >= 98 else 'bad'),
        ('Ausfälle', str(n_out) if has else '–',
         (f"zusammen ca. {st['down_minutes']} Minuten, alle behoben" if n_out else 'kein Ausfall erkannt') if has else '',
         'neutral' if not has else 'good' if not n_out else 'warn'),
        ('Ø Ladezeit', f"{_de(st['avg_ms'] / 1000, 1)} s" if st.get('avg_ms') else '–',
         'Antwortzeit des Servers' if st.get('avg_ms') else '',
         'neutral' if not st.get('avg_ms') else 'good' if st['avg_ms'] < 1500 else 'warn' if st['avg_ms'] < 3000 else 'bad'),
        ('SSL-Zertifikat', f'{ssl} Tage' if ssl is not None else '–',
         'gültig, verlängert sich automatisch' if ssl and ssl > 14 else 'Verlängerung steht an' if ssl is not None else '',
         'neutral' if ssl is None else 'good' if ssl > 14 else 'bad'),
    ]
    tw, th, gap = (INNER_W - 3 * 4 * mm) / 4, 30 * mm, 4 * mm
    for i, t in enumerate(tiles):
        _tile(c, MARGIN + i * (tw + gap), y, tw, th, *t)
    y -= th + 10 * mm

    # Was geprueft wurde
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(MARGIN, y, 'Was wir für Sie geprüft haben')
    y -= 7 * mm
    domain = (f"Domain registriert bis {datetime.strptime(d['domain_expiry'], '%Y-%m-%d').strftime('%d.%m.%Y')}"
              if d.get('domain_expiry') else (d.get('domain_note') or 'Domain: noch nicht geprüft'))
    rows = [
        ('ok' if has else 'info', 'Erreichbarkeit rund um die Uhr, alle 15 Minuten' +
         (f" – überwacht seit {_datum(st['since'])[:10]}" if has and st['since'][:7] == d['month'] else '')),
        ('ok' if ssl and ssl > 14 else 'warn' if ssl is not None else 'info',
         'Verschlüsselung (SSL/HTTPS) aktiv' + (f', Zertifikat noch {ssl} Tage gültig' if ssl is not None else '')),
        ('ok' if d.get('form_status') == 'ok' else 'warn' if d.get('form_status') else 'info',
         'Kontaktformular: ' + d['form_label']),
        ('ok' if d.get('domain_expiry') else 'info', domain),
    ]
    for state, label in rows:
        col = {'ok': GREEN, 'warn': AMBER}.get(state, ACCENT)
        c.setFillColor(col)
        c.circle(MARGIN + 2 * mm, y + 1.2 * mm, 1.8 * mm, fill=1, stroke=0)
        if state == 'ok':  # Haken als Linien - Helvetica hat kein Haken-Zeichen
            c.setStrokeColor(colors.white)
            c.setLineWidth(0.8)
            p = c.beginPath()
            p.moveTo(MARGIN + 1.1 * mm, y + 1.2 * mm)
            p.lineTo(MARGIN + 1.8 * mm, y + 0.5 * mm)
            p.lineTo(MARGIN + 3 * mm, y + 2 * mm)
            c.drawPath(p, stroke=1, fill=0)
            c.setLineWidth(1)
        else:
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 7)
            c.drawCentredString(MARGIN + 2 * mm, y + 0.3 * mm, '!' if state == 'warn' else 'i')
        y = _text(c, label, MARGIN + 7 * mm, y, 9.5, width=INNER_W - 7 * mm) - 2 * mm
    y -= 4 * mm

    # Ausfaelle im Detail
    if n_out:
        y = _new_page_if(c, y, 30 * mm, title, sub)
        c.setFillColor(NAVY)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(MARGIN, y, 'Erkannte Ausfälle')
        y -= 7 * mm
        for o in st['outages'][:15]:
            y = _new_page_if(c, y, 8 * mm, title, sub)
            y = _text(c, f"• {_datum(o['von'])} – {_datum(o['bis'])[11:]} Uhr (ca. {o['checks'] * 15} Min.) – erkannt, "
                         f"Seite ist wieder erreichbar", MARGIN, y, 9.5) - 1 * mm
        if n_out > 15:
            y = _text(c, f'… und {n_out - 15} weitere', MARGIN, y, 9, color=GREY)
        y -= 4 * mm

    # Hinweise
    hints = []
    if ssl is not None and ssl <= 14:
        hints.append(f'Das SSL-Zertifikat läuft in {ssl} Tagen ab – wir kümmern uns um die Verlängerung.')
    if d.get('domain_expiry'):
        days = (datetime.strptime(d['domain_expiry'], '%Y-%m-%d') - datetime.now()).days
        if days <= 60:
            hints.append(f'Die Domain läuft in {days} Tagen aus – bitte die Verlängerung prüfen.')
    if d.get('form_status') and d['form_status'] != 'ok':
        hints.append('Das Kontaktformular war bei der letzten Prüfung nicht auffindbar – wir sehen uns das an.')
    if st.get('avg_ms') and st['avg_ms'] >= 3000:
        hints.append('Die Seite lädt langsam. Schnellere Seiten werden bei Google besser gefunden – sprechen Sie uns an.')
    y = _new_page_if(c, y, 30 * mm, title, sub)
    box_h = (12 + 5 * max(1, len(hints))) * mm
    c.setFillColor(LIGHT_BG if not hints else colors.HexColor('#fffbeb'))
    c.setStrokeColor(ACCENT if not hints else AMBER)
    c.roundRect(MARGIN, y - box_h, INNER_W, box_h, 3 * mm, fill=1, stroke=1)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 10.5)
    c.drawString(MARGIN + 5 * mm, y - 7 * mm, 'Hinweise' if hints else 'Alles in Ordnung')
    yy = y - 13 * mm
    ok_text = ('Erkannte Ausfälle sind behoben, Ihre Website läuft wieder stabil. Es ist nichts zu tun.' if n_out
               else 'Ihre Website lief in diesem Monat ohne Auffälligkeiten. Es ist nichts zu tun.')
    for h in hints or [ok_text]:
        yy = _text(c, h, MARGIN + 5 * mm, yy, 9.5, width=INNER_W - 10 * mm) - 1 * mm
    c.save()
    return path


# ─── Angebot ──────────────────────────────────────────────────────────

def angebot(a, path):
    """a: nummer, datum, gueltig_bis, kunde{name, firma, email}, betreff, einleitung,
    positionen[{titel, beschreibung, einmalig, monatlich}], hinweise[str], analyse_angehaengt"""
    title, sub = 'Angebot', f"Nr. {a['nummer']} · {a['datum']}"
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(f"Angebot {a['nummer']} – ChefBlick")
    y = _header(c, title, sub)

    k = a['kunde']
    left = [x for x in (k.get('firma'), k.get('name'), k.get('email')) if x]
    yl = y
    for i, line in enumerate(left or ['–']):
        yl = _text(c, line, MARGIN, yl, 10.5 if i == 0 else 9.5, 'Helvetica-Bold' if i == 0 else 'Helvetica',
                   NAVY if i == 0 else TEXT_DARK, width=95 * mm)
    c.setFont('Helvetica', 9)
    c.setFillColor(GREY)
    meta = [('Angebotsnummer', a['nummer']), ('Datum', a['datum']), ('Gültig bis', a['gueltig_bis']),
            ('Ansprechpartner', 'Stefan Mutter')]
    for i, (lbl, val) in enumerate(meta):
        c.setFillColor(GREY)
        c.drawRightString(PAGE_W - MARGIN - 38 * mm, y - i * 5 * mm, lbl)
        c.setFillColor(NAVY)
        c.drawRightString(PAGE_W - MARGIN, y - i * 5 * mm, val)
    y = min(yl, y - len(meta) * 5 * mm) - 8 * mm

    y = _text(c, a['betreff'], MARGIN, y, 13, 'Helvetica-Bold', NAVY) - 2 * mm
    y = _text(c, a['einleitung'], MARGIN, y, 10, line_h=5 * mm) - 5 * mm

    # Positionen
    col_e, col_m = PAGE_W - MARGIN - 34 * mm, PAGE_W - MARGIN
    c.setFillColor(NAVY)
    c.rect(MARGIN, y - 7 * mm, INNER_W, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(MARGIN + 3 * mm, y - 4.8 * mm, 'Leistung')
    c.drawRightString(col_e, y - 4.8 * mm, 'Einmalig')
    c.drawRightString(col_m - 3 * mm, y - 4.8 * mm, 'Monatlich')
    y -= 12 * mm
    sum_e = sum_m = 0
    for p in a['positionen']:
        y = _new_page_if(c, y, 22 * mm, title, sub)
        top = y
        y = _text(c, p['titel'], MARGIN + 3 * mm, y, 10, 'Helvetica-Bold', NAVY, width=105 * mm)
        y = _text(c, p.get('beschreibung', ''), MARGIN + 3 * mm, y - 0.5 * mm, 8.7, color=TEXT_DARK, width=105 * mm,
                  line_h=4.2 * mm)
        c.setFont('Helvetica-Bold', 10)
        c.setFillColor(NAVY)
        e, m = p.get('einmalig'), p.get('monatlich')
        c.drawRightString(col_e, top, _eur(e) if isinstance(e, (int, float)) else (e or '–'))
        c.drawRightString(col_m - 3 * mm, top, _eur(m) if isinstance(m, (int, float)) else (m or '–'))
        sum_e += e if isinstance(e, (int, float)) else 0
        sum_m += m if isinstance(m, (int, float)) else 0
        y -= 3 * mm
        c.setStrokeColor(BORDER)
        c.line(MARGIN, y + 1.5 * mm, PAGE_W - MARGIN, y + 1.5 * mm)
        y -= 3 * mm

    # Summe (Preise sind brutto inkl. 19 % MwSt)
    y = _new_page_if(c, y, 30 * mm, title, sub)
    c.setFillColor(LIGHT_BG)
    c.roundRect(PAGE_W - MARGIN - 92 * mm, y - 22 * mm, 92 * mm, 22 * mm, 2 * mm, fill=1, stroke=0)
    rows = [('Gesamt einmalig', _eur(sum_e)), ('Gesamt monatlich', _eur(sum_m)),
            ('enthaltene MwSt. (19 %) einmalig', _eur(sum_e - sum_e / 1.19))]
    for i, (lbl, val) in enumerate(rows):
        c.setFont('Helvetica-Bold' if i < 2 else 'Helvetica', 10 if i < 2 else 8.5)
        c.setFillColor(NAVY if i < 2 else GREY)
        c.drawString(PAGE_W - MARGIN - 88 * mm, y - 6.5 * mm - i * 6 * mm, lbl)
        c.drawRightString(PAGE_W - MARGIN - 4 * mm, y - 6.5 * mm - i * 6 * mm, val)
    y -= 30 * mm

    hinweise = list(a.get('hinweise') or []) + [
        'Alle Preise sind Endpreise inklusive 19 % MwSt.',
        'Monatliche Leistungen (Hosting, SSL, Pflege) werden monatlich abgerechnet.',
        f"Dieses Angebot ist gültig bis {a['gueltig_bis']}. Bei Fragen erreichen Sie mich unter {CONTACT['phone']} "
        f"(auch WhatsApp) oder {CONTACT['email']}.",
    ]
    if a.get('analyse_angehaengt'):
        hinweise.insert(0, 'Die kostenlose Analyse Ihrer bisherigen Website liegt dieser Mail als eigenes PDF bei.')
    y = _new_page_if(c, y, 40 * mm, title, sub)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN, y, 'Gut zu wissen')
    y -= 6 * mm
    for h in hinweise:
        y = _new_page_if(c, y, 10 * mm, title, sub)
        y = _text(c, f'• {h}', MARGIN, y, 9, width=INNER_W, line_h=4.4 * mm) - 1.2 * mm
    y -= 6 * mm
    y = _new_page_if(c, y, 20 * mm, title, sub)
    y = _text(c, 'Ich freue mich auf Ihre Rückmeldung.', MARGIN, y, 10)
    _text(c, 'Stefan Mutter · ChefBlick', MARGIN, y - 2 * mm, 10, 'Helvetica-Bold', NAVY)
    c.save()
    return path
