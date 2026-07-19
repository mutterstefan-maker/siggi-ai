# -*- coding: utf-8 -*-
"""
Instagram Engine für SIGGI
Verwaltet einen lokalen "Flyer-Pool" (Bilder-Warteschlange) und postet daraus über die
Meta Graph API (Instagram Business/Creator Account nötig).

Ablauf pro Post (Graph API Standardverfahren für Bild-Posts):
  1. Bild liegt lokal im pool_path-Ordner.
  2. Server stellt das Bild unter einer öffentlichen URL bereit (/api/instagram/media/<filename>).
  3. POST /{ig-user-id}/media  mit image_url + caption  -> creation_id
  4. POST /{ig-user-id}/media_publish  mit creation_id   -> veröffentlicht
  5. Datei wird in posted_path verschoben, Ergebnis in ig_posts-Tabelle protokolliert.
"""
import os
import re
import sqlite3
from datetime import datetime

import requests as req

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_MODEL = 'claude-haiku-4-5-20251001'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IG_DB_PATH = os.path.join(BASE_DIR, 'instagram.db')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
GRAPH_API_VERSION = 'v19.0'
GRAPH_BASE = f'https://graph.facebook.com/{GRAPH_API_VERSION}'

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


def init_ig_db():
    conn = sqlite3.connect(IG_DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS ig_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        caption TEXT,
        status TEXT,
        error TEXT,
        posted_at TEXT
    )''')
    conn.commit()
    conn.close()


init_ig_db()


# ─── Settings / Pfade ───────────────────────────────────────────────

def _load_settings():
    import json
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(settings):
    import json
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _ig_settings():
    return _load_settings().get('instagram_settings', {})


def _resolve_dir(rel_path, create=True):
    """Löst pool_path/posted_path relativ zu BASE_DIR auf (oder nutzt absoluten Pfad)."""
    path = rel_path if os.path.isabs(rel_path) else os.path.join(BASE_DIR, rel_path)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def pool_dir():
    s = _ig_settings()
    return _resolve_dir(s.get('pool_path', 'flyer_pool'))


def posted_dir():
    s = _ig_settings()
    return _resolve_dir(s.get('posted_path', 'flyer_pool/gepostet'))


def _safe_filename(filename):
    """Verhindert Path-Traversal - nur der reine Dateiname wird verwendet."""
    return os.path.basename(filename)


# ─── Queue-Verwaltung ───────────────────────────────────────────────

def get_ig_queue():
    """Liste der Dateinamen im Flyer-Pool (unterhalb von pool_dir, nicht rekursiv)."""
    d = pool_dir()
    try:
        files = [
            f for f in sorted(os.listdir(d))
            if os.path.isfile(os.path.join(d, f)) and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        ]
        return files
    except Exception as e:
        print(f'[Instagram] Queue-Fehler: {e}')
        return []


def save_uploaded_flyer(filename, raw_bytes):
    filename = _safe_filename(filename)
    if os.path.splitext(filename)[1].lower() not in IMAGE_EXTENSIONS:
        return {'success': False, 'error': 'Nur Bilddateien erlaubt (jpg, jpeg, png, webp).'}
    path = os.path.join(pool_dir(), filename)
    with open(path, 'wb') as f:
        f.write(raw_bytes)
    return {'success': True, 'filename': filename}


def delete_flyer(filename):
    filename = _safe_filename(filename)
    path = os.path.join(pool_dir(), filename)
    if os.path.exists(path):
        os.remove(path)
        return {'success': True}
    return {'success': False, 'error': 'Datei nicht gefunden.'}


def resolve_path_by_filename(filename):
    """Best-effort: sucht in gängigen Nutzerordnern nach einer Datei mit diesem Namen,
    um den Quellordner für den 'Ordner auswählen'-Trick im Frontend zu ermitteln."""
    filename = _safe_filename(filename)
    candidates = [
        os.path.join(os.path.expanduser('~'), 'Pictures'),
        os.path.join(os.path.expanduser('~'), 'Downloads'),
        os.path.join(os.path.expanduser('~'), 'Desktop'),
        os.path.join(os.path.expanduser('~'), 'Documents'),
    ]
    for root in candidates:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if filename in files:
                return dirpath
    return None


def media_file_path(filename):
    filename = _safe_filename(filename)
    path = os.path.join(pool_dir(), filename)
    return path if os.path.exists(path) else None


# ─── History ─────────────────────────────────────────────────────────

def _log_post(filename, caption, status, error=None):
    conn = sqlite3.connect(IG_DB_PATH)
    conn.execute(
        'INSERT INTO ig_posts (filename, caption, status, error, posted_at) VALUES (?, ?, ?, ?, ?)',
        (filename, caption, status, error, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_ig_history(limit=30):
    conn = sqlite3.connect(IG_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM ig_posts ORDER BY id DESC LIMIT ?', (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Graph API ──────────────────────────────────────────────────────

def _graph_config():
    s = _ig_settings()
    return {
        'access_token': s.get('access_token', ''),
        'ig_user_id': s.get('ig_user_id', ''),
        'fb_page_id': s.get('fb_page_id', ''),
    }


def is_configured():
    cfg = _graph_config()
    return bool(cfg['access_token'] and cfg['ig_user_id'])


def _publish_to_facebook_page(image_url, caption):
    """Postet dasselbe Bild als Foto-Post auf die verknüpfte Facebook-Seite.
    Nutzt denselben Page-Access-Token wie das Instagram-Business-Konto."""
    cfg = _graph_config()
    if not cfg['fb_page_id']:
        return {'success': False, 'error': 'Keine fb_page_id in den Instagram-Einstellungen hinterlegt.'}
    try:
        resp = req.post(
            f"{GRAPH_BASE}/{cfg['fb_page_id']}/photos",
            data={'url': image_url, 'caption': caption, 'access_token': cfg['access_token']},
            timeout=30
        )
        data = resp.json()
        if not resp.ok or 'id' not in data:
            return {'success': False, 'error': f'Facebook-Post fehlgeschlagen: {data}'}
        return {'success': True, 'post_id': data.get('post_id', data['id'])}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _publish_image(image_url, caption):
    """Führt den zweistufigen Graph-API-Veröffentlichungsprozess aus."""
    cfg = _graph_config()
    if not is_configured():
        return {'success': False, 'error': 'Instagram Access-Token oder Business-Account-ID fehlt in den Einstellungen.'}

    try:
        create_resp = req.post(
            f"{GRAPH_BASE}/{cfg['ig_user_id']}/media",
            data={'image_url': image_url, 'caption': caption, 'access_token': cfg['access_token']},
            timeout=30
        )
        create_data = create_resp.json()
        if not create_resp.ok or 'id' not in create_data:
            return {'success': False, 'error': f'Media-Erstellung fehlgeschlagen: {create_data}'}
        creation_id = create_data['id']

        publish_resp = req.post(
            f"{GRAPH_BASE}/{cfg['ig_user_id']}/media_publish",
            data={'creation_id': creation_id, 'access_token': cfg['access_token']},
            timeout=30
        )
        publish_data = publish_resp.json()
        if not publish_resp.ok or 'id' not in publish_data:
            return {'success': False, 'error': f'Veröffentlichung fehlgeschlagen: {publish_data}'}

        return {'success': True, 'media_id': publish_data['id']}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def post_ig(content):
    """Kompatibilität mit altem Stub-Aufruf - nicht mehr aktiv genutzt."""
    return {'success': False, 'message': 'Nutze post_next_in_queue() stattdessen.'}


def _filename_to_topic(filename):
    """Wandelt einen Dateinamen wie 'AutomatisiereDeineAblaeufe.png' in ein Thema
    für die Caption-Generierung um: Endung weg, camelCase/snake_case/Bindestriche in Wörter."""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'[_\-]+', ' ', name)
    name = re.sub(r'(?<=[a-zäöüß])(?=[A-ZÄÖÜ])', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _generate_caption_from_filename(filename, default_caption=''):
    """Erzeugt per Claude einen Instagram-Text zum Thema, das aus dem Dateinamen abgeleitet
    wird. Gibt bei Fehlern (kein API-Key, Netzwerkfehler, ...) default_caption zurück."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    topic = _filename_to_topic(filename)
    if not api_key or not topic:
        return default_caption

    prompt = f"""Schreib einen kurzen, ansprechenden Instagram-Beitragstext auf Deutsch fuer ChefBlick \
(Webdesign- und Software-Agentur aus Haag an der Amper, Oberbayern) zum Thema: "{topic}".

Vorgaben:
- 2-4 Saetze, locker und direkt, kein Marketing-Geschwaetz
- Am Ende 3-5 passende Hashtags
- Antworte NUR mit dem fertigen Text, keine Erklaerungen, keine Anfuehrungszeichen drumherum"""

    try:
        response = req.post(
            ANTHROPIC_API_URL,
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': ANTHROPIC_MODEL,
                'max_tokens': 300,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=30
        )
        result = response.json()
        text = result['content'][0]['text'].strip()
        return text or default_caption
    except Exception as e:
        print(f'[Instagram] Caption-Generierung fehlgeschlagen: {e}')
        return default_caption


def post_next_in_queue(public_base_url):
    """Postet das erste Bild aus der Warteschlange. public_base_url ist die von außen
    erreichbare Basis-URL des Servers (z.B. https://www.stean.info), damit Meta das Bild
    per HTTP abrufen kann."""
    queue = get_ig_queue()
    if not queue:
        return {'success': False, 'error': 'Keine Bilder in der Warteschlange.'}

    filename = queue[0]
    s = _ig_settings()
    caption = _generate_caption_from_filename(filename, s.get('default_caption', ''))
    image_url = f"{public_base_url.rstrip('/')}/api/instagram/media/{filename}"

    result = _publish_image(image_url, caption)

    if result['success']:
        src = os.path.join(pool_dir(), filename)
        dst = os.path.join(posted_dir(), filename)
        try:
            os.replace(src, dst)
        except Exception as e:
            print(f'[Instagram] Konnte Datei nicht archivieren: {e}')
        _log_post(filename, caption, 'posted')

        fb_result = _publish_to_facebook_page(image_url, caption)
        if not fb_result['success']:
            print(f"[Facebook] Post fehlgeschlagen: {fb_result.get('error')}")

        settings = _load_settings()
        ig_settings = settings.setdefault('instagram_settings', {})
        ig_settings['last_posted'] = datetime.now().isoformat()
        _save_settings(settings)
        return {'success': True, 'filename': filename, 'facebook_posted': fb_result['success']}
    else:
        _log_post(filename, caption, 'error', result.get('error'))
        return {'success': False, 'error': result.get('error')}


# ─── Auto-Scheduler ─────────────────────────────────────────────────

def maybe_auto_post(public_base_url):
    """Wird periodisch (z.B. jede Minute) aufgerufen. Postet automatisch, wenn Auto-Post
    aktiv ist und die aktuelle Zeit einem konfigurierten Post-Slot entspricht - dedupliziert
    über 'auto_post_last_slot' in settings.json, damit nicht doppelt gepostet wird."""
    s = _ig_settings()
    if s.get('auto_enabled') != '1':
        return

    now = datetime.now()
    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    today = day_map[now.weekday()]
    active_days = (s.get('post_days') or 'mon,tue,wed,thu,fri,sat,sun').split(',')
    if today not in active_days:
        return

    times = (s.get('post_times') or '09:00').split(',')
    current_hm = now.strftime('%H:%M')
    if current_hm not in [t.strip() for t in times]:
        return

    slot_key = f'{now.strftime("%Y-%m-%d")}_{current_hm}'
    if s.get('auto_post_last_slot') == slot_key:
        return  # in dieser Minute schon gepostet

    settings = _load_settings()
    settings.setdefault('instagram_settings', {})['auto_post_last_slot'] = slot_key
    _save_settings(settings)

    if get_ig_queue():
        post_next_in_queue(public_base_url)
