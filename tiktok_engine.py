# -*- coding: utf-8 -*-
import os
import json
import sqlite3
import requests
import time
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TIKTOK_DB_PATH = os.path.join(BASE_DIR, 'tiktok.db')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

CLIENT_KEY = "awualchqfeug2i2b"
CLIENT_SECRET = "uTcA93ReiIy9kuG2qPGksO5nx3yTYiSB"
REDIRECT_URI = "http://localhost:8080/tiktok/callback"
TIKTOK_POOL = r"C:\Users\Stefan Mutter\Desktop\flyer_pool\tiktok_pool"

def get_tt_pool_path():
    p = get_tt_setting("pool_path", "")
    return p if p else TIKTOK_POOL

def get_tt_posted_path():
    p = get_tt_setting("posted_path", "")
    return p if p else os.path.join(TIKTOK_POOL, "gepostet")



def init_tiktok_db():
    os.makedirs(TIKTOK_POOL, exist_ok=True)
    conn = sqlite3.connect(TIKTOK_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tiktok_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        caption TEXT,
        post_id TEXT,
        status TEXT DEFAULT 'pending',
        posted_at TEXT,
        error TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tiktok_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    defaults = [
        ('auto_enabled', '0'),
        ('post_times', '10:00'),
        ('post_days', 'mon,tue,wed,thu,fri,sat,sun'),
        ('post_times_mon', ''),
        ('post_times_tue', ''),
        ('post_times_wed', ''),
        ('post_times_thu', ''),
        ('post_times_fri', ''),
        ('post_times_sat', ''),
        ('post_times_sun', ''),
        ('per_day_enabled', '0'),
        ('last_posted', ''),
        ('access_token', ''),
        ('refresh_token', ''),
        ('token_expires', ''),
        ('open_id', ''),
        ('code_verifier', ''),
        ('pool_path', ''),
        ('posted_path', ''),
    ]
    for k, v in defaults:
        c.execute('INSERT OR IGNORE INTO tiktok_settings (key, value) VALUES (?,?)', (k, v))
    conn.commit()
    conn.close()


def get_tt_setting(key, default=''):
    conn = sqlite3.connect(TIKTOK_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM tiktok_settings WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_tt_setting(key, value):
    conn = sqlite3.connect(TIKTOK_DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO tiktok_settings (key, value) VALUES (?,?)', (key, str(value)))
    conn.commit()
    conn.close()


def get_auth_url():
    """Generiert TikTok OAuth URL mit PKCE"""
    import hashlib, base64, secrets
    # PKCE Code Verifier und Challenge generieren
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    # Verifier speichern fuer Token-Austausch
    set_tt_setting('code_verifier', code_verifier)
    scope = "user.info.basic,video.publish,video.upload"
    url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={CLIENT_KEY}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state=chefblick"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    return url


def exchange_code_for_token(code):
    """Tauscht Auth-Code gegen Access Token"""
    code_verifier = get_tt_setting('code_verifier', '')
    response = requests.post(
        'https://open.tiktokapis.com/v2/oauth/token/',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={
            'client_key': CLIENT_KEY,
            'client_secret': CLIENT_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': REDIRECT_URI,
            'code_verifier': code_verifier,
        }
    )
    data = response.json()
    if 'access_token' in data:
        set_tt_setting('access_token', data['access_token'])
        set_tt_setting('refresh_token', data.get('refresh_token', ''))
        set_tt_setting('open_id', data.get('open_id', ''))
        expires_at = (datetime.now() + timedelta(seconds=data.get('expires_in', 86400))).isoformat()
        set_tt_setting('token_expires', expires_at)
        return True, data
    return False, data


def refresh_access_token():
    """Erneuert den Access Token"""
    refresh_token = get_tt_setting('refresh_token')
    if not refresh_token:
        return False
    response = requests.post(
        'https://open.tiktokapis.com/v2/oauth/token/',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={
            'client_key': CLIENT_KEY,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
    )
    data = response.json()
    if 'access_token' in data:
        set_tt_setting('access_token', data['access_token'])
        set_tt_setting('refresh_token', data.get('refresh_token', ''))
        expires_at = (datetime.now() + timedelta(seconds=data.get('expires_in', 86400))).isoformat()
        set_tt_setting('token_expires', expires_at)
        return True
    return False


def get_valid_token():
    """Gibt gültigen Token zurück, erneuert wenn nötig"""
    token = get_tt_setting('access_token')
    if not token:
        return None
    expires = get_tt_setting('token_expires')
    if expires:
        try:
            if datetime.fromisoformat(expires) < datetime.now() + timedelta(hours=1):
                refresh_access_token()
                token = get_tt_setting('access_token')
        except:
            pass
    return token


def get_tiktok_queue():
    """Gibt Videos im tiktok_pool zurück"""
    if not os.path.exists(TIKTOK_POOL):
        return []
    allowed = {'.mp4', '.mov', '.avi'}
    files = []
    for f in sorted(os.listdir(TIKTOK_POOL)):
        if os.path.isfile(os.path.join(TIKTOK_POOL, f)):
            if os.path.splitext(f)[1].lower() in allowed:
                files.append(f)
    return files


def generate_tiktok_caption(filename):
    """GPT-4o generiert TikTok Caption"""
    from instagram_engine import OPENAI_API_KEY
    thema = os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' ')
    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': 'gpt-4o',
                'messages': [
                    {'role': 'system', 'content': '''Du bist Copywriter fuer ChefBlick (www.chefblick.de), eine Webdesign- und Software-Agentur aus Bayern.
ChefBlick macht: Webseiten, Dashboards, Backend-Loesungen, CRM, Ticket-Systeme.
Schreibe TikTok-Captions: kurz, direkt, mit Emojis, auf Deutsch.
Call-to-Action: "UPGRADE" in DM schicken fuer kostenloses Erstgespraech.
Hashtags am Ende: #ChefBlick #Webdesign #Bayern #Business'''},
                    {'role': 'user', 'content': f'TikTok Caption fuer: {thema}. Max 150 Woerter.'}
                ]
            },
            timeout=30
        )
        text = response.json()['choices'][0]['message']['content']
        return f"{text}\n\n(Hinweis: Dieser Text wurde mit KI-Unterstützung erstellt.)"
    except:
        return f"Dein Business verdient eine bessere Online-Praesenz! 🚀 ChefBlick baut Webseiten & Dashboards die wirklich funktionieren. Schick 'UPGRADE' per DM! #ChefBlick #Webdesign #Bayern\n\n(Hinweis: Dieser Text wurde mit KI-Unterstützung erstellt.)"


def upload_video_to_tiktok(filepath, caption):
    """Laedt Video direkt zu TikTok hoch"""
    token = get_valid_token()
    if not token:
        raise Exception("Kein TikTok Access Token - bitte neu autorisieren")

    file_size = os.path.getsize(filepath)

    # Schritt 1: Upload initialisieren
    init_response = requests.post(
        'https://open.tiktokapis.com/v2/post/publish/video/init/',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        },
        json={
            'post_info': {
                'title': caption[:150],
                'privacy_level': 'PUBLIC_TO_EVERYONE',
                'disable_duet': False,
                'disable_comment': False,
                'disable_stitch': False,
            },
            'source_info': {
                'source': 'FILE_UPLOAD',
                'video_size': file_size,
                'chunk_size': file_size,
                'total_chunk_count': 1
            }
        }
    ).json()

    if 'data' not in init_response:
        raise Exception(f"Init Fehler: {init_response}")

    publish_id = init_response['data']['publish_id']
    upload_url = init_response['data']['upload_url']

    # Schritt 2: Video hochladen
    with open(filepath, 'rb') as f:
        video_data = f.read()

    upload_response = requests.put(
        upload_url,
        headers={
            'Content-Type': 'video/mp4',
            'Content-Range': f'bytes 0-{file_size-1}/{file_size}',
            'Content-Length': str(file_size)
        },
        data=video_data,
        timeout=120
    )

    if upload_response.status_code not in [200, 201, 206]:
        raise Exception(f"Upload Fehler: {upload_response.status_code}")

    # Schritt 3: Status prüfen
    time.sleep(5)
    for _ in range(10):
        status_response = requests.post(
            'https://open.tiktokapis.com/v2/post/publish/status/fetch/',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'publish_id': publish_id}
        ).json()

        status = status_response.get('data', {}).get('status', '')
        if status == 'PUBLISH_COMPLETE':
            return publish_id
        elif status in ['FAILED', 'ERROR']:
            raise Exception(f"Publish fehlgeschlagen: {status_response}")
        time.sleep(3)

    return publish_id


def process_next_tiktok():
    """Verarbeitet das naechste Video in der Queue"""
    init_tiktok_db()
    queue = get_tiktok_queue()
    if not queue:
        return {'success': False, 'error': 'Keine Videos in der Warteschlange'}

    filename = queue[0]
    filepath = os.path.join(TIKTOK_POOL, filename)

    # Max 3 Versuche
    conn = sqlite3.connect(TIKTOK_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tiktok_posts WHERE filename=? AND status='error'", (filename,))
    error_count = c.fetchone()[0]
    if error_count >= 3:
        import shutil
        posted_dir = os.path.join(TIKTOK_POOL, 'gepostet')
        os.makedirs(posted_dir, exist_ok=True)
        try:
            shutil.move(filepath, os.path.join(posted_dir, 'FEHLER_' + filename))
        except:
            pass
        conn.close()
        return {'success': False, 'error': f'Nach 3 Fehlversuchen uebersprungen: {filename}'}

    c.execute('INSERT INTO tiktok_posts (filename, status) VALUES (?,?)', (filename, 'processing'))
    post_id = c.lastrowid
    conn.commit()

    try:
        print(f"TikTok: Verarbeite {filename}")
        caption = generate_tiktok_caption(filename)
        publish_id = upload_video_to_tiktok(filepath, caption)

        import shutil
        posted_dir = os.path.join(TIKTOK_POOL, 'gepostet')
        os.makedirs(posted_dir, exist_ok=True)
        shutil.move(filepath, os.path.join(posted_dir, filename))

        c.execute("UPDATE tiktok_posts SET caption=?, post_id=?, status='posted', posted_at=? WHERE id=?",
                  (caption, publish_id, datetime.now().isoformat(), post_id))
        conn.commit()
        conn.close()
        set_tt_setting('last_posted', datetime.now().isoformat())
        print(f"TikTok: Erfolgreich gepostet {filename}")
        return {'success': True, 'filename': filename, 'publish_id': publish_id}

    except Exception as e:
        c.execute("UPDATE tiktok_posts SET status='error', error=? WHERE id=?", (str(e), post_id))
        conn.commit()
        conn.close()
        print(f"TikTok Fehler: {e}")
        return {'success': False, 'error': str(e)}


def should_tiktok_post():
    """Prueft ob automatisch gepostet werden soll"""
    if get_tt_setting('auto_enabled', '0') != '1':
        return False
    if not get_valid_token():
        return False

    post_days = get_tt_setting('post_days', 'mon,tue,wed,thu,fri,sat,sun').split(',')
    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    now = datetime.now()
    if day_map[now.weekday()] not in post_days:
        return False

    day_key = f'post_times_{today}'
    day_specific = get_tt_setting(day_key, '').strip()
    if day_specific:
        post_times_raw = day_specific.split(',')
    else:
        post_times_raw = get_tt_setting('post_times', '10:00').split(',')

    post_times = []
    for pt in post_times_raw:
        try:
            h, m = map(int, pt.strip().split(':'))
            post_times.append((h, m))
        except:
            pass

    if not post_times:
        return False

    conn = sqlite3.connect(TIKTOK_DB_PATH)
    c = conn.cursor()
    today_str = now.strftime('%Y-%m-%d')
    c.execute("SELECT posted_at FROM tiktok_posts WHERE status='posted' AND posted_at LIKE ?", (f'{today_str}%',))
    posts_today = []
    for row in c.fetchall():
        try:
            posts_today.append(datetime.fromisoformat(row[0]))
        except:
            pass
    conn.close()

    for (h, m) in sorted(post_times):
        slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < slot_time:
            continue
        if not any(p >= slot_time for p in posts_today):
            return True

    return False


def get_tiktok_history():
    conn = sqlite3.connect(TIKTOK_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM tiktok_posts ORDER BY id DESC LIMIT 20')
    result = [dict(row) for row in c.fetchall()]
    conn.close()
    return result
