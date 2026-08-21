# -*- coding: utf-8 -*-
"""Automatische ChefBlick-Bildgenerierung fuer Instagram/Facebook.

Ersetzt den bisherigen manuellen Workflow (Stefan hat Bilder einzeln in einem
ChatGPT-Projekt "Sarkastische Bilderserie" erzeugt). Laeuft taeglich per Cron:
  1. Claude waehlt Thema/Headline/Layout nach den ChefBlick-Vorgaben (MASTER_PROMPT)
     und vermeidet Wiederholungen anhand der letzten Eintraege in flyer_history.
  2. OpenAI (gpt-image-1) erzeugt das fertige Bild inkl. Logo (Referenzbild wird
     mitgeschickt) und "KI-GENERIERT"-Kennzeichnung senkrecht am linken Rand.
  3. Bild wird auf 1080x1350 (4:5) zugeschnitten und im flyer_pool abgelegt -
     von dort holt die bestehende Instagram-Auto-Post-Schleife es sich selbst.

Das Bild wird NICHT automatisch gepostet - das erledigt bereits
instagram_engine.post_next_in_queue() ueber den bestehenden Cron/Loop.
"""
import base64
import json
import os
import re
import sqlite3

import requests
from dotenv import load_dotenv
from PIL import Image
from io import BytesIO

import instagram_engine

load_dotenv('/opt/stean/config/.env')

DB_PATH = '/opt/stean/mails.db'
SETTINGS_PATH = '/opt/stean/settings.json'
LOGO_PATH = '/opt/stean/cowork/Chefblick/SocialMedia/ChefBlick Logo Quer.png'
PENDING_DIR = '/opt/stean/flyer_pending'

TARGET_SIZE = (1080, 1350)  # 4:5, ChefBlick-Standardformat

MASTER_PROMPT = """Du bist der Bild-Kreativdirektor fuer ChefBlick.de (Webdesign, Software,
Digitalisierung fuer kleine/mittelstaendische Unternehmen). Du planst EIN neues
Social-Media-Bild (Instagram/Facebook-Feed) nach folgenden festen Vorgaben:

ZIEL: Aufmerksamkeit, Vertrauen, Follower, Anfragen, ChefBlick als
Digitalisierungspartner positionieren - NICHT wie eine klassische
Webdesign-Agentur wirken, NICHT wie eine SEO-Agentur.

FORMAT: 1080x1350 (4:5), Standard fuer Instagram/Facebook-Feed. Logo, Headline
und CTA brauchen grosszuegigen Abstand zum Bildrand (mind. 5% der Bildhoehe/
-breite) - nichts darf am aeussersten Rand kleben, da sonst beim finalen
Zuschnitt Inhalte verloren gehen koennten.

FARBEN: Blau, Schwarz, Weiss dominant. Keine dominanten Fremdfarben (Ausnahme:
das ChefBlick-Einhorn darf Regenbogenhaare haben).

LOGO: Immer das originale ChefBlick-Logo verwenden (wird als Referenzbild
mitgeschickt) - niemals ein neues/anderes Logo erfinden, keine Kochmuetze,
kein Auge-Symbol. Logo nicht mehrfach im Bild platzieren.

KI-KENNZEICHNUNG: exakt der Text "KI-GENERIERT" (genau diese Schreibweise,
Gross-/Kleinschreibung und Bindestrich exakt so, sorgfaeltig auf korrekte
Buchstaben achten, keine Rechtschreibfehler) gut lesbar, senkrecht am linken
Bildrand, weiss oder passend zum Blau-Schwarz-Weiss-Design, nicht winzig,
nicht unten rechts. Ausreichend Innenabstand zum Bildrand lassen.

TEXTMENGE: Sehr wenig Text. Kurze Headline (2-4 Woerter pro Zeile, max 3-4
Zeilen), kurze Unterzeile, keine Textwuesten. Eine starke Aussage + ein
starkes Motiv + ein CTA. Innerhalb weniger Sekunden verstaendlich.

FESTE FUSSZEILE (immer, unabhaengig vom Layout): am unteren Bildrand eine
schmale Zeile mit 3-4 kleinen Linien-Icons (kein Emoji) mit Haekchen-Marker,
je ein kurzes Schlagwort darunter - Auswahl passend zum Thema aus: Moderne
Webseiten, Online-Terminbuchung, Google-Praesenz, Mehr Kunden, Individuelle
Software, Support & Wartung, Digitalisierung. Gleiche Bildsprache/Farben wie
der Rest (blau/weiss auf dunklem Grund). Das ist das wiedererkennbare
ChefBlick-Markenelement und darf NIE weggelassen werden, unabhaengig davon
welches Haupt-Layout sonst gewaehlt wird.

TEXTSTIL: kurz, direkt, modern, praxisnah, teils provokant, selbstbewusst,
nicht technisch. Beispiele im gewuenschten Ton: "Deine Kunden sind online.
Bist du es auch?", "Excel ist kein CRM.", "Dein Business. Dein Weg.",
"Warte nicht auf Applaus. Fang an."

PREISE: Niemals Preise/Zahlen auf dem Bild, ausser explizit gefordert.

EINHORN-MASKOTTCHEN (nur ca. jedes 10. Bild, nicht direkt nach dem letzten
Einsatz): weisses Einhorn, Regenbogenhaare, goldenes Horn, schwarze
Sonnenbrille, schwarzes Polo mit ChefBlick-Logo links auf der Brust, kurze
Hose, Turnschuhe, menschliche Haende mit 5 Fingern, frech/selbstbewusst,
in humorvoller Alltagssituation. NICHT: Kochmuetze, Hufe, Anzug, nackt.
Nicht automatisch Laptop/Smartphone/Dashboard dazu.

LAYOUT-ROTATION: nicht das gleiche Layout wie zuletzt (Smartphone-Mockup,
Tablet-Mockup, Webseiten-Mockup, Dashboard, Vorher/Nachher, Reality-Check-
Karte, minimalistisch, Comic-Stil, Branchenfoto, Magazin-Cover-Stil,
Plakat-Stil, cineastisches Motiv, Arbeitsplatz-Szene, symbolische Szene,
humorvolle Einhorn-Szene).

THEMENROTATION: nicht das gleiche Thema wie zuletzt. Themenbereiche:
Webseiten-Tipps, Reality Checks, Digitalisierung, Unternehmeralltag,
Handwerker/Friseur/Restaurant/Fitnessstudio/Immobilien/Arztpraxis/Elektriker/
Vereine/Catering, CRM, Ticketsystem, Buchungssystem, Google-Unternehmensprofil,
Hosting, WhatsApp-Kanal, Datenschutz/KI-Recht, Unternehmertum/Motivation,
Automatisierung. Content-Serien: "Kennst du das?" (Unternehmerprobleme),
"Das kostet Geld", "Frueher vs. Heute", "Typische Unternehmer-Saetze",
"Mythen/Reality Check", Unternehmer-Motivation, digitale Selbstbestimmung
(Domain/Hosting/Zugangsdaten gehoeren dem Unternehmer, nicht dem Dienstleister).
NICHT ueberstrapazieren: Kontaktformular, Webseite-24-7, Google-Bewertungen,
alte/mobile Webseite - diese Themen wurden schon sehr oft verwendet.

CTA: abwechselnd, z.B. "Folge uns", "Schreib uns", "Kommentiere deine
Meinung", "Kostenlos testen", "Wir beraten dich gerne". WhatsApp-Kanal
("Einhorn ChefBlick.de", https://whatsapp.com/channel/0029VbCGeNT4yltJdnbRAm1D)
nur gelegentlich bewerben, nicht bei zwei Bildern hintereinander.

Antworte NUR mit einem JSON-Objekt (keine Erklaerung, kein Markdown-Codeblock):
{{
  "topic": "kurzes Themen-Schlagwort",
  "headline": "die Headline, wie sie im Bild stehen soll (mit Zeilenumbruechen als \\n)",
  "subtext": "kurze Unterzeile oder leer",
  "cta": "Call-to-Action-Text",
  "layout": "gewaehltes Layout aus der Rotation",
  "use_unicorn": true oder false,
  "filename": "EinDateinameOhneLeerzeichenUmlauteSonderzeichenBindestriche",
  "image_prompt": "detaillierter englischer Bildgenerierungs-Prompt, der ALLE obigen Vorgaben (Format, Farben, Logo-Platzierung, KI-GENERIERT-Kennzeichnung links senkrecht, Textinhalt exakt wie headline/subtext/cta, Layout, Einhorn ja/nein) konkret fuer dieses eine Bild beschreibt"
}}

BISHER VERWENDET (nicht wiederholen, besonders nicht Thema/Layout/Einhorn-Nutzung von ganz zuletzt):
{history}
"""


def init_table():
    os.makedirs(PENDING_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS flyer_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        headline TEXT,
        layout TEXT,
        use_unicorn INTEGER,
        filename TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    conn.commit()
    conn.close()


def _load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _recent_history(limit=8):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT topic, layout, use_unicorn, created_at FROM flyer_history "
        "WHERE status IN ('pending', 'approved') ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return '(noch keine Historie vorhanden)'
    lines = []
    for topic, layout, use_unicorn, created_at in rows:
        einhorn = 'mit Einhorn' if use_unicorn else 'ohne Einhorn'
        lines.append(f"- {created_at}: Thema '{topic}', Layout '{layout}', {einhorn}")
    return '\n'.join(lines)


def _save_history(topic, headline, layout, use_unicorn, filename, status='pending'):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO flyer_history (topic, headline, layout, use_unicorn, filename, status) VALUES (?, ?, ?, ?, ?, ?)",
        (topic, headline, layout, int(bool(use_unicorn)), filename, status)
    )
    conn.commit()
    conn.close()


def get_pending(limit=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, topic, headline, layout, use_unicorn, filename, status, created_at "
        "FROM flyer_history WHERE status='pending' ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    cols = ['id', 'topic', 'headline', 'layout', 'use_unicorn', 'filename', 'status', 'created_at']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows


def approve_flyer(entry_id):
    """Verschiebt das Bild aus dem Pruef-Ordner in die echte Instagram-Warteschlange
    (flyer_pool) - von dort holt der bestehende Auto-Post-Mechanismus es sich."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename FROM flyer_history WHERE id=? AND status='pending'", (entry_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {'success': False, 'error': 'Eintrag nicht gefunden oder bereits entschieden.'}

    filename = row[0]
    src = os.path.join(PENDING_DIR, filename)
    if not os.path.exists(src):
        conn.close()
        return {'success': False, 'error': f'Datei fehlt im Pruef-Ordner: {filename}'}

    dest = os.path.join(instagram_engine.pool_dir(), filename)
    os.rename(src, dest)

    c.execute("UPDATE flyer_history SET status='approved', decided_at=CURRENT_TIMESTAMP WHERE id=?", (entry_id,))
    conn.commit()
    conn.close()
    return {'success': True, 'message': f'{filename} in die Instagram-Warteschlange verschoben.'}


def reject_flyer(entry_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename FROM flyer_history WHERE id=? AND status='pending'", (entry_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {'success': False, 'error': 'Eintrag nicht gefunden oder bereits entschieden.'}

    filename = row[0]
    src = os.path.join(PENDING_DIR, filename)
    if os.path.exists(src):
        os.remove(src)

    c.execute("UPDATE flyer_history SET status='rejected', decided_at=CURRENT_TIMESTAMP WHERE id=?", (entry_id,))
    conn.commit()
    conn.close()
    return {'success': True}


def _call_claude(prompt, api_key):
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-sonnet-5',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': prompt}]
        },
        timeout=60
    )
    result = response.json()
    if 'content' not in result:
        raise Exception(f'Claude-API-Fehler: {result}')
    for block in result['content']:
        if block.get('type') == 'text':
            return block['text'].strip()
    raise Exception(f'Claude-API-Antwort ohne Text-Block: {result}')


def _parse_plan(raw_text):
    cleaned = re.sub(r'^```(json)?|```$', '', raw_text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _generate_image(image_prompt, openai_api_key):
    with open(LOGO_PATH, 'rb') as f:
        logo_bytes = f.read()

    response = requests.post(
        'https://api.openai.com/v1/images/edits',
        headers={'Authorization': f'Bearer {openai_api_key}'},
        files={'image[]': ('logo.png', logo_bytes, 'image/png')},
        data={
            'model': 'gpt-image-1',
            'prompt': image_prompt,
            'size': '1024x1536',
        },
        timeout=180
    )
    result = response.json()
    if 'data' not in result:
        raise Exception(f'OpenAI-Bild-Fehler: {result}')
    b64 = result['data'][0]['b64_json']
    return base64.b64decode(b64)


def _fit_to_target(image_bytes):
    """Skaliert das Bild verlustfrei INS Zielformat (kein Zuschnitt), damit Logo/Text
    an den Raendern nicht abgeschnitten werden. Rand wird schwarz aufgefuellt, was zur
    ChefBlick-Farbwelt (ueberwiegend dunkler Hintergrund) passt."""
    img = Image.open(BytesIO(image_bytes)).convert('RGB')
    target_w, target_h = TARGET_SIZE
    scale = min(target_w / img.width, target_h / img.height)
    new_w, new_h = round(img.width * scale), round(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new('RGB', TARGET_SIZE, (5, 8, 15))
    canvas.paste(img, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def _safe_filename(name):
    name = re.sub(r'[^A-Za-z0-9]', '', name) or 'ChefBlickBild'
    return name[:60]


def generate_flyer():
    settings = _load_settings()
    anthropic_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    openai_key = settings.get('openai_api_key', '')
    if not anthropic_key or not openai_key:
        return {'success': False, 'error': 'API-Key fehlt (Anthropic oder OpenAI).'}
    if not os.path.exists(LOGO_PATH):
        return {'success': False, 'error': f'Logo-Datei nicht gefunden: {LOGO_PATH}'}

    prompt = MASTER_PROMPT.format(history=_recent_history())
    raw_plan = _call_claude(prompt, anthropic_key)

    try:
        plan = _parse_plan(raw_plan)
    except Exception as e:
        return {'success': False, 'error': f'Konnte Themenplan nicht lesen: {e} | Antwort: {raw_plan[:300]}'}

    filename_base = _safe_filename(plan.get('filename', plan.get('topic', 'ChefBlickBild')))
    filename = f'{filename_base}.png'

    try:
        image_bytes = _generate_image(plan['image_prompt'], openai_key)
        final_img = _fit_to_target(image_bytes)
    except Exception as e:
        _save_history(plan.get('topic', ''), plan.get('headline', ''), plan.get('layout', ''),
                       plan.get('use_unicorn', False), filename, status='failed')
        return {'success': False, 'error': f'Bildgenerierung fehlgeschlagen: {e}'}

    os.makedirs(PENDING_DIR, exist_ok=True)
    dest_path = os.path.join(PENDING_DIR, filename)
    counter = 2
    while os.path.exists(dest_path):
        dest_path = os.path.join(PENDING_DIR, f'{filename_base}{counter}.png')
        counter += 1
    final_img.save(dest_path, 'PNG')

    _save_history(plan.get('topic', ''), plan.get('headline', ''), plan.get('layout', ''),
                   plan.get('use_unicorn', False), os.path.basename(dest_path))

    return {
        'success': True,
        'filename': os.path.basename(dest_path),
        'topic': plan.get('topic'),
        'headline': plan.get('headline'),
        'layout': plan.get('layout'),
        'use_unicorn': plan.get('use_unicorn'),
    }


if __name__ == '__main__':
    init_table()
    print(generate_flyer())
