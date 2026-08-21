"""LinkedIn-Content-Pipeline für Siggi.

Erstellt taeglich einen LinkedIn-Post-Entwurf im Stil von Stefans bisherigen
Posts (Chefblick / E-Commerce-Beratung), abgeleitet aus linkedin_posts.json.
Die ersten 50 Entwuerfe muessen manuell im "LinkedIn Pipeline"-Tab freigegeben
werden (Freigeben = posten via linkedin_engine.post_share, Ablehnen = verwerfen).
Ab der 50. Freigabe schaltet die Pipeline auf automatisches Posten ohne
manuelle Pruefung um (analog zum Wissensluecken-Lernmechanismus bei Mails).
"""
import json
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
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    conn.commit()
    conn.close()


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
            'model': 'claude-opus-5',
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


def _build_system_prompt(examples):
    examples_block = '\n\n---\n\n'.join(examples) if examples else '(noch keine Beispiel-Posts vorhanden)'
    return f"""Du schreibst LinkedIn-Posts fuer Stefan Mutter, Inhaber der Agentur ChefBlick
(Website-Erstellung, E-Commerce-Beratung, Digitalisierung fuer Unternehmer).

THEMENFOKUS:
{TOPIC_FOCUS}

STIL (aus Stefans bisherigen Posts abgeleitet, halte dich eng daran):
- Direkter, persoenlicher Ton, oft ausgehend von einem echten Alltags-Erlebnis
- Einstieg mit einer Hook-Frage oder einer ueberraschenden Aussage
- Klare Bullet-Point-Listen fuer Probleme/Symptome (mit Emoji wie ❌ oder –)
- Ehrlich, kein Verkaufs-Sprech, auch mal selbstkritisch oder nachdenklich
- Endet meist mit einer offenen Frage an die Leser, die zur Diskussion einlaedt
- 3-5 passende Hashtags am Ende
- Laenge: 100-250 Woerter

BEISPIELE VON STEFANS BISHERIGEN POSTS (Referenz fuer Ton/Stil, NICHT kopieren):
{examples_block}

AUFGABE: Schreibe GENAU EINEN neuen, eigenstaendigen LinkedIn-Post zu einem
aktuell relevanten Thema aus dem Themenfokus. Kein neues Thema doppeln, das
in den Beispielen schon vorkommt. Gib NUR den fertigen Post-Text zurueck,
keine Erklaerung, keine Anfuehrungszeichen drumherum."""


def generate_draft():
    settings = load_settings()
    # env-Key (funktionierendes Hauptkonto) hat Vorrang vor settings.json,
    # falls dort ein separater/veralteter Key hinterlegt ist
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    if not api_key or api_key == 'HIER_API_KEY_EINTRAGEN':
        return None

    examples = _load_example_posts()
    system_prompt = _build_system_prompt(examples)
    text = _call_claude(system_prompt, 'Schreibe jetzt den Post.', api_key)
    text = re.sub(r'^["\']|["\']$', '', text.strip())
    text = f"{text}\n\n(Hinweis: Dieser Text wurde mit KI-Unterstützung erstellt.)"

    approved_count = settings.get('linkedin_approved_count', 0)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if approved_count >= AUTO_POST_THRESHOLD:
        result = linkedin_engine.post_share(text)
        status = 'auto_posted' if 'veröffentlicht' in result else 'auto_post_failed'
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, status, decided_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            ('auto', text, status)
        )
    else:
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, status) VALUES (?, ?, 'pending')",
            ('manual-review', text)
        )

    conn.commit()
    draft_id = c.lastrowid
    conn.close()
    return draft_id


def get_drafts(status=None, limit=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if status:
        c.execute("SELECT id, topic, text, status, created_at FROM linkedin_drafts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        c.execute("SELECT id, topic, text, status, created_at FROM linkedin_drafts ORDER BY created_at DESC LIMIT ?", (limit,))
    cols = ['id', 'topic', 'text', 'status', 'created_at']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows


def approve_draft(draft_id):
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
    conn.commit()
    conn.close()

    if ok:
        settings = load_settings()
        settings['linkedin_approved_count'] = settings.get('linkedin_approved_count', 0) + 1
        save_settings(settings)

    return {'success': ok, 'message': result}


def reject_draft(draft_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE linkedin_drafts SET status='rejected', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (draft_id,)
    )
    conn.commit()
    affected = c.rowcount
    conn.close()
    return {'success': affected > 0}


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
