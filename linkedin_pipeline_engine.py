"""LinkedIn-Content-Pipeline für Siggi.

Erstellt taeglich einen LinkedIn-Post-Entwurf im Stil von Stefans bisherigen
Posts (Chefblick / E-Commerce-Beratung), abgeleitet aus linkedin_posts.json.
Die ersten 50 Entwuerfe muessen manuell im "LinkedIn Pipeline"-Tab freigegeben
werden (Freigeben = posten via linkedin_engine.post_share, Ablehnen = verwerfen).
Ab der 50. Freigabe schaltet die Pipeline auf automatisches Posten ohne
manuelle Pruefung um (analog zum Wissensluecken-Lernmechanismus bei Mails).
"""
import json
import random
import re
import sqlite3
import datetime
import os

import requests
from dotenv import load_dotenv

import linkedin_engine

load_dotenv('/opt/stean/config/.env')

DB_PATH = '/opt/stean/mails.db'
SETTINGS_PATH = '/opt/stean/settings.json'
POSTS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linkedin_posts.json')

AUTO_POST_THRESHOLD = 50

TOPIC_FOCUS = (
    "Chefblick / E-Commerce-Beratung: Website-Erstellung, Online-Shops, "
    "Digitalisierung fuer Unternehmer, IT-Sicherheit, DSGVO/Rechtssicherheit "
    "im Netz, KI im Business (pragmatisch, nicht hypey), Alltag als "
    "Agentur-Inhaber."
)


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def init_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS linkedin_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        text TEXT,
        format_style TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    existing_cols = {row[1] for row in c.execute('PRAGMA table_info(linkedin_drafts)').fetchall()}
    for col_def in ('format_style TEXT', 'rating TEXT', 'feedback_tags TEXT', 'feedback_comment TEXT'):
        col_name = col_def.split()[0]
        if col_name not in existing_cols:
            c.execute(f'ALTER TABLE linkedin_drafts ADD COLUMN {col_def}')
    conn.commit()
    conn.close()


FORMAT_STYLES = [
    {
        'key': 'hook_bulletliste',
        'label': 'Hook-Frage + Bulletpoint-Liste',
        'desc': (
            "Einstieg mit Hook-Frage oder ueberraschender Aussage, dann eine klare "
            "Bulletpoint-Liste mit Problemen/Symptomen (Emoji wie ❌), Abschluss mit "
            "offener Frage. Das ist das klassische Format - nur nutzen wenn es lange "
            "nicht mehr dran war."
        ),
    },
    {
        'key': 'kurze_these',
        'label': 'Kurze steile These (kein Bulletpoint)',
        'desc': (
            "Kurzer, knackiger Fliesstext OHNE Bulletpoints/Listen. Eine steile, "
            "vielleicht leicht kontroverse These zum Thema aufstellen, dann in 2-3 "
            "kurzen Absaetzen begruenden. Max 120 Woerter. Wirkt wie ein spontaner "
            "LinkedIn-Gedanke, nicht wie ein Ratgeber-Post."
        ),
    },
    {
        'key': 'persoenliche_anekdote',
        'label': 'Persoenliche Alltagsgeschichte',
        'desc': (
            "Erzaehlt chronologisch eine kurze, konkrete Alltagsszene/Kundengespraech "
            "aus Stefans Agentur-Alltag (mit Datum/Situation), OHNE Bulletpoints, "
            "die dann zu einer Erkenntnis/Lehre fuehrt. Persoenlich, story-artig."
        ),
    },
    {
        'key': 'zahlen_fakten',
        'label': 'Zahlen/Fakten-Format',
        'desc': (
            "Startet mit einer konkreten Zahl oder Statistik zum Thema, erklaert kurz "
            "die Relevanz fuer Unternehmer, nennt 2-3 konkrete Handlungsempfehlungen "
            "als kurze nummerierte Liste (1. 2. 3., NICHT mit Emoji-Bullets)."
        ),
    },
    {
        'key': 'vorher_nachher',
        'label': 'Vorher/Nachher-Kontrast',
        'desc': (
            "Beschreibt kurz einen 'Vorher'-Zustand (Problem/Chaos) und dann den "
            "'Nachher'-Zustand nach der Loesung, in zwei klar getrennten kurzen "
            "Abschnitten (koennen mit 'Vorher:' / 'Nachher:' markiert sein), ohne "
            "lange Bulletlisten."
        ),
    },
    {
        'key': 'mythos_check',
        'label': 'Mythos-Check / Missverstaendnis auflösen',
        'desc': (
            "Beginnt mit einem weit verbreiteten Irrglauben/Missverstaendnis als Zitat "
            "in Anfuehrungszeichen, widerlegt ihn dann sachlich in Fliesstext (kein "
            "Bulletpoint-Zwang), schliesst mit einer klaren Handlungsempfehlung."
        ),
    },
]


def _pick_format_style(exclude_last_n=3):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT format_style FROM linkedin_drafts WHERE format_style IS NOT NULL "
        "ORDER BY created_at DESC LIMIT ?", (exclude_last_n,)
    )
    recent = {r[0] for r in c.fetchall() if r[0]}
    conn.close()
    candidates = [s for s in FORMAT_STYLES if s['key'] not in recent] or FORMAT_STYLES
    return random.choice(candidates)


def _load_example_posts(limit=6):
    if not os.path.exists(POSTS_LOG_PATH):
        return []
    try:
        with open(POSTS_LOG_PATH, encoding='utf-8') as f:
            posts = json.load(f)
        return [p['text'] for p in posts[-limit:]]
    except Exception:
        return []


def _call_claude(system_prompt, user_content, api_key):
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-sonnet-5',
            'max_tokens': 1200,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_content}]
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


def _recent_feedback_block(limit=6):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT text, rating, feedback_tags, feedback_comment FROM linkedin_drafts "
        "WHERE rating IS NOT NULL AND rating != '' ORDER BY decided_at DESC LIMIT ?", (limit,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return ''
    lines = []
    for text, rating, tags_json, comment in rows:
        try:
            tags = json.loads(tags_json) if tags_json else []
        except Exception:
            tags = []
        detail = ', '.join(tags) if tags else ''
        if comment:
            detail = f'{detail} - Kommentar: {comment}' if detail else f'Kommentar: {comment}'
        snippet = (text or '')[:120].replace('\n', ' ')
        lines.append(f"- Post \"{snippet}...\" wurde als '{rating}' bewertet. {detail}".strip())
    return (
        "\n\nWICHTIG - FEEDBACK VON STEFAN ZU FRUEHEREN POSTS (unbedingt beruecksichtigen "
        "und diese Kritikpunkte bei diesem neuen Post vermeiden):\n" + '\n'.join(lines)
    )


def _build_system_prompt(examples, format_style):
    examples_block = '\n\n---\n\n'.join(examples) if examples else '(noch keine Beispiel-Posts vorhanden)'
    feedback_block = _recent_feedback_block()
    return f"""Du schreibst LinkedIn-Posts fuer Stefan Mutter, Inhaber der Agentur ChefBlick
(Website-Erstellung, E-Commerce-Beratung, Digitalisierung fuer Unternehmer).

THEMENFOKUS:
{TOPIC_FOCUS}

GRUNDTON (aus Stefans bisherigen Posts abgeleitet, halte dich daran):
- Direkter, persoenlicher Ton, oft mit Bezug zu echten Alltags-Erlebnissen/Kundengespraechen
- Ehrlich, kein Verkaufs-Sprech, auch mal selbstkritisch oder nachdenklich
- 3-5 passende Hashtags am Ende
- Laenge: 100-250 Woerter

WICHTIG - FORMAT FUER DIESEN POST (dieses Mal GENAU dieses Format nutzen, nicht das
uebliche Hook+Bulletpoints-Schema, damit die Posts insgesamt abwechslungsreich bleiben):
{format_style['label']}: {format_style['desc']}

BEISPIELE VON STEFANS BISHERIGEN POSTS (Referenz fuer Ton, NICHT das Format/die Struktur kopieren):
{examples_block}
{feedback_block}

AUFGABE: Schreibe GENAU EINEN neuen, eigenstaendigen LinkedIn-Post zu einem
aktuell relevanten Thema aus dem Themenfokus, in dem oben vorgegebenen Format.
Kein Thema doppeln, das in den Beispielen schon vorkommt. Gib NUR den fertigen
Post-Text zurueck, keine Erklaerung, keine Anfuehrungszeichen drumherum."""


def generate_draft():
    settings = load_settings()
    # env-Key (funktionierendes Hauptkonto) hat Vorrang vor settings.json,
    # falls dort ein separater/veralteter Key hinterlegt ist
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    if not api_key or api_key == 'HIER_API_KEY_EINTRAGEN':
        return None

    examples = _load_example_posts()
    format_style = _pick_format_style()
    system_prompt = _build_system_prompt(examples, format_style)
    text = _call_claude(system_prompt, 'Schreibe jetzt den Post.', api_key)
    text = re.sub(r'^["\']|["\']$', '', text.strip())

    approved_count = settings.get('linkedin_approved_count', 0)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if approved_count >= AUTO_POST_THRESHOLD:
        result = linkedin_engine.post_share(text)
        status = 'auto_posted' if 'veröffentlicht' in result else 'auto_post_failed'
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, format_style, status, decided_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ('auto', text, format_style['key'], status)
        )
    else:
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, format_style, status) VALUES (?, ?, ?, 'pending')",
            ('manual-review', text, format_style['key'])
        )

    conn.commit()
    draft_id = c.lastrowid
    conn.close()
    return draft_id


def get_drafts(status=None, limit=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if status:
        c.execute("SELECT id, topic, text, format_style, status, created_at FROM linkedin_drafts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        c.execute("SELECT id, topic, text, format_style, status, created_at FROM linkedin_drafts ORDER BY created_at DESC LIMIT ?", (limit,))
    cols = ['id', 'topic', 'text', 'format_style', 'status', 'created_at']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows


def _store_feedback(c, draft_id, rating, tags, comment):
    if rating or tags or comment:
        c.execute(
            "UPDATE linkedin_drafts SET rating=?, feedback_tags=?, feedback_comment=? WHERE id=?",
            (rating, json.dumps(tags or [], ensure_ascii=False), comment or '', draft_id)
        )


def approve_draft(draft_id, rating=None, tags=None, comment=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT text FROM linkedin_drafts WHERE id=? AND status='pending'", (draft_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {'error': 'Entwurf nicht gefunden oder bereits entschieden.'}

    result = linkedin_engine.post_share(row[0])
    ok = 'veröffentlicht' in result
    c.execute(
        "UPDATE linkedin_drafts SET status=?, decided_at=CURRENT_TIMESTAMP WHERE id=?",
        ('approved_posted' if ok else 'approve_failed', draft_id)
    )
    _store_feedback(c, draft_id, rating, tags, comment)
    conn.commit()
    conn.close()

    if ok:
        settings = load_settings()
        settings['linkedin_approved_count'] = settings.get('linkedin_approved_count', 0) + 1
        save_settings(settings)

    return {'success': ok, 'message': result}


def reject_draft(draft_id, rating=None, tags=None, comment=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE linkedin_drafts SET status='rejected', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (draft_id,)
    )
    affected = c.rowcount
    _store_feedback(c, draft_id, rating, tags, comment)
    conn.commit()
    conn.close()
    return {'success': affected > 0}


def get_feedback_stats(limit=40):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rating, COUNT(*) FROM linkedin_drafts WHERE rating IS NOT NULL AND rating != '' GROUP BY rating")
    counts = {r: n for r, n in c.fetchall()}
    c.execute(
        "SELECT topic, format_style, rating, feedback_tags, feedback_comment, decided_at FROM linkedin_drafts "
        "WHERE rating IS NOT NULL AND rating != '' ORDER BY decided_at DESC LIMIT ?", (limit,)
    )
    recent = []
    for topic, format_style, rating, tags_json, comment, decided_at in c.fetchall():
        try:
            tags = json.loads(tags_json) if tags_json else []
        except Exception:
            tags = []
        recent.append({
            'topic': topic, 'format_style': format_style, 'rating': rating,
            'tags': tags, 'comment': comment, 'decided_at': decided_at
        })
    conn.close()
    return {'counts': counts, 'recent': recent}


def get_progress():
    settings = load_settings()
    approved = settings.get('linkedin_approved_count', 0)
    return {
        'approved_count': approved,
        'threshold': AUTO_POST_THRESHOLD,
        'auto_mode': approved >= AUTO_POST_THRESHOLD
    }


if __name__ == '__main__':
    init_table()
    new_id = generate_draft()
    print(f'Neuer Entwurf erzeugt: id={new_id}')
