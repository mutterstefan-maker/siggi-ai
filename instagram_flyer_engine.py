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
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

import instagram_engine

load_dotenv('/opt/stean/config/.env')

DB_PATH = '/opt/stean/mails.db'
SETTINGS_PATH = '/opt/stean/settings.json'
LOGO_PATH = '/opt/stean/cowork/Chefblick/SocialMedia/ChefBlick Logo Quer.png'
UNICORN_REF_PATH = '/opt/stean/cowork/Chefblick/SocialMedia/ChefBlick_Einhorn_Referenz.png'
PENDING_DIR = '/opt/stean/flyer_pending'
FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

TARGET_SIZE = (1080, 1350)  # 4:5, ChefBlick-Standardformat
KI_LABEL_BAR_WIDTH = 46

FEEDBACK_TAGS = [
    'Zu viel Text', 'Layout langweilig', 'Falsche Farben', 'Logo falsch/verzerrt',
    'Motiv passt nicht', 'Fusszeile fehlt/falsch', 'KI-Kennzeichnung falsch',
    'Abgeschnitten/Rand', 'Einhorn falsch dargestellt', 'Thema wiederholt sich',
]
RATING_LABELS = {'gut': 'Gut', 'okay': 'Okay', 'schlecht': 'Schlecht', 'quatsch': 'Quatsch'}

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
kein Auge-Symbol. Logo nicht mehrfach im Bild platzieren. Standardposition:
oben links (bei manchen Layouts auch oben mittig), direkt darunter/daneben
klein die Zeile "WEBDESIGN. HOSTING. SOFTWARE." in Grossbuchstaben.

BILDSTIL (Standard, das ist Stefans etablierter Look - unbedingt einhalten):
Ganz ueberwiegend FOTOREALISTISCHE dunkle Szenen: Schreibtisch/Arbeitsplatz
von oben/schraeg mit Monitor/Laptop, auf dessen Bildschirm ein glaubwuerdiges
dunkles Dashboard/UI-Mockup (mit ChefBlick-Branding) zu sehen ist, dazu
typische Requisiten (schwarze Kaffeetasse mit ChefBlick-Logo, Pflanze,
Notizbuch, Handy, Tastatur), stimmungsvolles blaues Ambientelicht. Headline
in einer breiten, fetten, kondensierten Grotesk-Schrift in Grossbuchstaben
(wie ein Poster/Werbeplakat), farblich gemischt: ein Teil der Woerter weiss,
ein Teil (die Kernaussage) leuchtend blau. CTA meist als abgerundeter blauer
Button/Pill mit kurzem Text wie "SCHREIB MICH AN!" und/oder team@chefblick.de
darunter oder daneben. Comic-Stil, flache Illustration, Isometrie oder
Magazin-Layout sind erlaubte SELTENE Ausnahmen (max. 1 von 8-10 Bildern),
nicht der Standard.

KI-KENNZEICHNUNG: wird NICHT von dir gezeichnet, sondern automatisch danach
per Code als fester blauer Balken mit weissem Text am linken Bildrand
aufgesetzt. Lass darum den linken Rand des Bildes (die aeussersten ca. 5%
der Breite) frei von wichtigen Motiv-Elementen, Text oder Logo, damit dort
nichts verdeckt wird. Zeichne selbst KEINE eigene KI-Kennzeichnung.

TEXTMENGE: Sehr wenig Text. Kurze Headline (2-4 Woerter pro Zeile, max 3-4
Zeilen), kurze Unterzeile, keine Textwuesten. Eine starke Aussage + ein
starkes Motiv + ein CTA. Innerhalb weniger Sekunden verstaendlich.

FESTE FUSSZEILE (immer, unabhaengig vom Layout): am unteren Bildrand GENAU
EINMAL eine schmale Zeile mit 3-4 kleinen Linien-Icons (kein Emoji) mit
Haekchen-Marker, je ein kurzes Schlagwort darunter - Auswahl passend zum
Thema aus: Moderne Webseiten, Online-Terminbuchung, Google-Praesenz, Mehr
Kunden, Individuelle Software, Support & Wartung, Digitalisierung. Gleiche
Bildsprache/Farben wie der Rest (blau/weiss auf dunklem Grund). Das ist das
wiedererkennbare ChefBlick-Markenelement und darf NIE weggelassen werden,
unabhaengig davon welches Haupt-Layout sonst gewaehlt wird - aber eben nur
EIN EINZIGES MAL im ganzen Bild, niemals zweimal (weder als Wiederholung
innerhalb des Hauptmotivs noch als zusaetzliche zweite Leiste).

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

LAYOUT-ROTATION: nicht das gleiche Layout wie zuletzt, aber ueberwiegend
innerhalb des fotorealistischen Arbeitsplatz-/Dashboard-Looks variieren
(z.B. Laptop-Szene, Monitor-Szene, Smartphone-Szene, Dashboard-Grossaufnahme,
Split-Screen Vorher/Nachher). Comic-Stil, flache Illustration, Isometrie
oder Magazin-Plakat-Stil nur gelegentlich als bewusste Abwechslung (selten,
siehe BILDSTIL oben), humorvolle Einhorn-Szene nur wenn use_unicorn=true.

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
{custom_topics}

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
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(flyer_history)').fetchall()}
    for col_def in ('rating TEXT', 'feedback_tags TEXT', 'feedback_comment TEXT'):
        col_name = col_def.split()[0]
        if col_name not in existing_cols:
            conn.execute(f'ALTER TABLE flyer_history ADD COLUMN {col_def}')
    conn.commit()
    conn.close()


def _load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_topic_history(limit=100):
    """Alle bisher verwendeten/geplanten Themen (nicht nur die mit Feedback wie
    get_feedback_stats) - damit Stefan sieht, was schon dran war, bevor er neue
    Themen-Ideen einträgt."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT topic, layout, use_unicorn, status, created_at FROM flyer_history "
        "WHERE topic IS NOT NULL AND topic != '' ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return [
        {'topic': topic, 'layout': layout, 'use_unicorn': bool(use_unicorn), 'status': status, 'created_at': created_at}
        for topic, layout, use_unicorn, status, created_at in c.fetchall()
    ]


def _recent_history(limit=8):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT topic, layout, use_unicorn, created_at FROM flyer_history "
        "WHERE status IN ('pending', 'approved') ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = c.fetchall()

    c.execute(
        "SELECT topic, rating, feedback_tags, feedback_comment FROM flyer_history "
        "WHERE rating IN ('schlecht', 'quatsch') ORDER BY decided_at DESC LIMIT 6"
    )
    feedback_rows = c.fetchall()
    conn.close()

    if not rows:
        history_block = '(noch keine Historie vorhanden)'
    else:
        lines = []
        for topic, layout, use_unicorn, created_at in rows:
            einhorn = 'mit Einhorn' if use_unicorn else 'ohne Einhorn'
            lines.append(f"- {created_at}: Thema '{topic}', Layout '{layout}', {einhorn}")
        history_block = '\n'.join(lines)

    if feedback_rows:
        fb_lines = []
        for topic, rating, tags_json, comment in feedback_rows:
            try:
                tags = json.loads(tags_json) if tags_json else []
            except Exception:
                tags = []
            detail = ', '.join(tags) if tags else ''
            if comment:
                detail = f'{detail} - Kommentar: {comment}' if detail else f'Kommentar: {comment}'
            fb_lines.append(f"- Thema '{topic}' wurde als '{rating}' bewertet. {detail}".strip())
        history_block += (
            "\n\nNEGATIVES FEEDBACK VON STEFAN ZU FRUEHEREN BILDERN (unbedingt beruecksichtigen "
            "und diese Fehler bei diesem neuen Bild vermeiden):\n" + '\n'.join(fb_lines)
        )

    return history_block


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


def _store_feedback(c, entry_id, rating, tags, comment):
    if rating or tags or comment:
        c.execute(
            "UPDATE flyer_history SET rating=?, feedback_tags=?, feedback_comment=? WHERE id=?",
            (rating, json.dumps(tags or [], ensure_ascii=False), comment or '', entry_id)
        )


def approve_flyer(entry_id, rating=None, tags=None, comment=None):
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
    _store_feedback(c, entry_id, rating, tags, comment)
    conn.commit()
    conn.close()
    return {'success': True, 'message': f'{filename} in die Instagram-Warteschlange verschoben.'}


def reject_flyer(entry_id, rating=None, tags=None, comment=None):
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
    _store_feedback(c, entry_id, rating, tags, comment)
    conn.commit()
    conn.close()
    return {'success': True}


def get_feedback_stats(limit=40):
    """Fuer den Statistik-Bereich im Dashboard: Verteilung der Bewertungen +
    die letzten Kommentare/Tags, damit Stefan sieht, was der KI zurueckgemeldet wurde."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rating, COUNT(*) FROM flyer_history WHERE rating IS NOT NULL AND rating != '' GROUP BY rating")
    counts = {r: n for r, n in c.fetchall()}
    c.execute(
        "SELECT topic, filename, rating, feedback_tags, feedback_comment, decided_at FROM flyer_history "
        "WHERE rating IS NOT NULL AND rating != '' ORDER BY decided_at DESC LIMIT ?", (limit,)
    )
    recent = []
    for topic, filename, rating, tags_json, comment, decided_at in c.fetchall():
        try:
            tags = json.loads(tags_json) if tags_json else []
        except Exception:
            tags = []
        recent.append({
            'topic': topic, 'filename': filename, 'rating': rating,
            'tags': tags, 'comment': comment, 'decided_at': decided_at
        })
    conn.close()
    return {'counts': counts, 'recent': recent}


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
            'max_tokens': 4000,
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


def _generate_image(image_prompt, openai_api_key, use_unicorn=False):
    with open(LOGO_PATH, 'rb') as f:
        logo_bytes = f.read()

    files = [('image[]', ('logo.png', logo_bytes, 'image/png'))]

    if use_unicorn and os.path.exists(UNICORN_REF_PATH):
        with open(UNICORN_REF_PATH, 'rb') as f:
            unicorn_bytes = f.read()
        files.append(('image[]', ('einhorn_referenz.png', unicorn_bytes, 'image/png')))
        image_prompt = (
            'Das zweite mitgeschickte Referenzbild zeigt das ChefBlick-Einhorn-Maskottchen aus '
            'einem frueheren Bild - uebernimm sein genaues Aussehen (Fell, Regenbogenmaehne, '
            'goldenes Horn, Sonnenbrille, Koerperbau) 1:1 identisch, nur die Pose/Situation/der '
            'Hintergrund aendert sich passend zum neuen Motiv.\n\n' + image_prompt
        )

    response = requests.post(
        'https://api.openai.com/v1/images/edits',
        headers={'Authorization': f'Bearer {openai_api_key}'},
        files=files,
        data={
            'model': 'gpt-image-1',
            'prompt': image_prompt,
            'size': '1024x1536',
            'quality': 'medium',
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


def _draw_ki_label(img):
    """Zeichnet die KI-GENERIERT-Kennzeichnung fest und immer identisch als
    blauen Balken mit senkrechtem weissem Text am linken Rand - nicht der
    Bildgenerierung ueberlassen, damit Position/Optik nie variiert."""
    draw = ImageDraw.Draw(img)
    bar_h = img.height
    draw.rectangle([0, 0, KI_LABEL_BAR_WIDTH, bar_h], fill=(20, 90, 220))

    text = 'KI-GENERIERT'
    font = ImageFont.truetype(FONT_PATH, 26)
    label = Image.new('RGBA', (bar_h, KI_LABEL_BAR_WIDTH), (0, 0, 0, 0))
    label_draw = ImageDraw.Draw(label)
    bbox = label_draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    label_draw.text(
        ((bar_h - text_w) / 2 - bbox[0], (KI_LABEL_BAR_WIDTH - text_h) / 2 - bbox[1]),
        text, font=font, fill=(255, 255, 255, 255)
    )
    label = label.rotate(90, expand=True)
    img.paste(label, (0, 0), label)
    return img


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

    custom_topics = [t['text'] for t in settings.get('custom_topics', []) if t.get('text')]
    custom_topics_text = (
        'VOM NUTZER VORGESCHLAGENE THEMEN-IDEEN (bevorzugt EINS davon verwenden, '
        'sofern nicht in BISHER VERWENDET schon kuerzlich behandelt):\n- ' + '\n- '.join(custom_topics)
    ) if custom_topics else ''
    prompt = MASTER_PROMPT.format(history=_recent_history(), custom_topics=custom_topics_text)

    plan = None
    last_error = None
    for attempt in range(2):
        raw_plan = _call_claude(prompt, anthropic_key)
        try:
            plan = _parse_plan(raw_plan)
            break
        except Exception as e:
            last_error = f'{e} | Antwort: {raw_plan[:300]}'
    if plan is None:
        return {'success': False, 'error': f'Konnte Themenplan nicht lesen (auch nach Wiederholung): {last_error}'}

    filename_base = _safe_filename(plan.get('filename', plan.get('topic', 'ChefBlickBild')))
    filename = f'{filename_base}.png'

    try:
        image_bytes = _generate_image(plan['image_prompt'], openai_key)
        final_img = _fit_to_target(image_bytes)
        final_img = _draw_ki_label(final_img)
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
