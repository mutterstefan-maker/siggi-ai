# -*- coding: utf-8 -*-
"""
Reels Engine für SIGGI
Erstellt automatisch stille Slideshow-Videos aus bereits geposteten Flyer-Bildern
(instagram_engine.posted_dir()) und postet sie über die Meta Graph API als Instagram-Story
(24h, kein dauerhafter Feed-Eintrag).

Ablauf:
  1. generate_reel_from_images(): baut per ffmpeg ein 9:16-Slideshow-Video (Crossfades,
     kein Ton) aus den zuletzt geposteten Flyer-Bildern und legt es im Reels-Pool ab.
  2. post_next_reel_in_queue(): lädt das erste Video aus dem Pool, stellt es unter einer
     öffentlichen URL bereit, erstellt einen Story-Media-Container (media_type=STORIES)
     und veröffentlicht ihn, sobald die Verarbeitung abgeschlossen ist.
  3. Datei wird nach erfolgreichem Post ins Archiv verschoben, Ergebnis wird protokolliert.
"""
import glob
import os
import random
import sqlite3
import subprocess
import time
from datetime import datetime
from urllib.parse import quote

import requests as req
from PIL import Image, ImageDraw, ImageFont

import instagram_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REELS_DB_PATH = os.path.join(BASE_DIR, 'reels.db')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
GRAPH_BASE = instagram_engine.GRAPH_BASE

VIDEO_EXTENSIONS = {'.mp4', '.mov'}
IMAGES_PER_REEL = 5
SECONDS_PER_IMAGE = 3
CROSSFADE_SECONDS = 0.6
MIN_SOURCE_IMAGES = 3
FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
KI_LABEL_TEXT = 'KI-GENERIERT'
KI_LABEL_PNG_PATH = os.path.join(BASE_DIR, '_reels_ki_label.png')


def init_reels_db():
    conn = sqlite3.connect(REELS_DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS reels_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        caption TEXT,
        status TEXT,
        error TEXT,
        posted_at TEXT
    )''')
    conn.commit()
    conn.close()


init_reels_db()


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


def _reels_settings():
    return _load_settings().get('reels_settings', {})


def _resolve_dir(rel_path, create=True):
    path = rel_path if os.path.isabs(rel_path) else os.path.join(BASE_DIR, rel_path)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def pool_dir():
    s = _reels_settings()
    return _resolve_dir(s.get('pool_path', 'reels_pool'))


def posted_dir():
    s = _reels_settings()
    return _resolve_dir(s.get('posted_path', 'reels_pool/gepostet'))


def _safe_filename(filename):
    return os.path.basename(filename)


# ─── Queue-Verwaltung ───────────────────────────────────────────────

def get_reels_queue():
    d = pool_dir()
    try:
        return [
            f for f in sorted(os.listdir(d))
            if os.path.isfile(os.path.join(d, f)) and os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
        ]
    except Exception as e:
        print(f'[Reels] Queue-Fehler: {e}')
        return []


def save_uploaded_reel(filename, raw_bytes):
    filename = _safe_filename(filename)
    if os.path.splitext(filename)[1].lower() not in VIDEO_EXTENSIONS:
        return {'success': False, 'error': 'Nur Videodateien erlaubt (mp4, mov).'}
    path = os.path.join(pool_dir(), filename)
    with open(path, 'wb') as f:
        f.write(raw_bytes)
    return {'success': True, 'filename': filename}


def delete_reel(filename):
    filename = _safe_filename(filename)
    path = os.path.join(pool_dir(), filename)
    if os.path.exists(path):
        os.remove(path)
        return {'success': True}
    return {'success': False, 'error': 'Datei nicht gefunden.'}


def media_file_path(filename):
    filename = _safe_filename(filename)
    path = os.path.join(pool_dir(), filename)
    return path if os.path.exists(path) else None


# ─── History ─────────────────────────────────────────────────────────

def _log_post(filename, caption, status, error=None):
    conn = sqlite3.connect(REELS_DB_PATH)
    conn.execute(
        'INSERT INTO reels_posts (filename, caption, status, error, posted_at) VALUES (?, ?, ?, ?, ?)',
        (filename, caption, status, error, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_reels_history(limit=30):
    conn = sqlite3.connect(REELS_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM reels_posts ORDER BY id DESC LIMIT ?', (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Video-Erstellung (ffmpeg, stille Slideshow) ─────────────────────

def _ffmpeg_binary():
    """Nutzt ein von imageio-ffmpeg mitgeliefertes ffmpeg-Binary, falls verfügbar
    (kein System-Setup auf dem Server nötig), sonst das ffmpeg aus dem PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


def _get_ki_label_png():
    """Rendert die 'KI-GENERIERT'-Kennzeichnung einmalig als PNG mit halbtransparentem
    Balken (per PIL statt ffmpeg drawtext, da der von imageio-ffmpeg mitgelieferte
    ffmpeg-Build ohne Freetype/drawtext-Unterstützung gebaut ist)."""
    if os.path.exists(KI_LABEL_PNG_PATH):
        return KI_LABEL_PNG_PATH
    img = Image.new('RGBA', (1080, 64), (0, 0, 0, 140))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 30) if os.path.exists(FONT_PATH) else ImageFont.load_default()
    bbox = draw.textbbox((0, 0), KI_LABEL_TEXT, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((1080 - text_w) / 2 - bbox[0], (64 - text_h) / 2 - bbox[1]), KI_LABEL_TEXT, font=font, fill=(255, 255, 255, 255))
    img.save(KI_LABEL_PNG_PATH)
    return KI_LABEL_PNG_PATH


def _pick_themed_group(files, count):
    """Lässt Claude (Haiku, wenige Tokens) aus den Dateinamen eine inhaltlich
    zusammenpassende Gruppe auswählen, statt rein zufällig zu mischen. Gibt None
    zurück, wenn kein API-Key vorhanden ist oder die Auswahl fehlschlägt."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key or len(files) <= count:
        return None

    names = [os.path.basename(f) for f in files]
    prompt = (
        "Hier sind Dateinamen von Marketing-Flyer-Bildern (der Dateiname deutet meist "
        "auf Thema/Headline hin):\n" + "\n".join(names) +
        f"\n\nWähle genau {count} davon aus, die inhaltlich am besten zueinander passen "
        "(gleiches Thema, gleiche Zielgruppe oder ähnliche Botschaft), damit sie zusammen "
        "ein stimmiges Instagram-Reel ergeben. Antworte NUR mit den exakten Dateinamen, "
        "durch Kommas getrennt, keine Erklärung."
    )
    try:
        resp = req.post(
            instagram_engine.ANTHROPIC_API_URL,
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': instagram_engine.ANTHROPIC_MODEL,
                'max_tokens': 200,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=20
        )
        text = resp.json()['content'][0]['text']
        picked_names = {p.strip() for p in text.split(',') if p.strip()}
        picked_paths = [f for f in files if os.path.basename(f) in picked_names]
        if len(picked_paths) >= 2:
            return picked_paths[:count]
    except Exception as e:
        print(f'[Reels] Themen-Auswahl fehlgeschlagen, nutze Zufallsauswahl: {e}')
    return None


def _source_images_for_reel(count):
    """Nimmt die zuletzt geposteten Flyer-Bilder als Quelle - die wurden bereits als
    Bild-Post verwendet, stehen also nicht mehr in Konkurrenz zur Bild-Warteschlange.
    Bevorzugt eine thematisch passende Gruppe (per KI), fällt sonst auf eine
    Zufallsauswahl aus den zuletzt geposteten Bildern zurück."""
    d = instagram_engine.posted_dir()
    files = [
        os.path.join(d, f) for f in os.listdir(d)
        if os.path.splitext(f)[1].lower() in instagram_engine.IMAGE_EXTENSIONS
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    if len(files) < MIN_SOURCE_IMAGES:
        return []

    candidate_pool = files[:max(count, MIN_SOURCE_IMAGES) * 4]
    themed = _pick_themed_group(candidate_pool, count)
    if themed:
        return themed

    pick = candidate_pool[:max(count, MIN_SOURCE_IMAGES) * 2]
    random.shuffle(pick)
    return pick[:count]


def generate_reel_from_images(image_paths=None):
    """Baut aus den übergebenen (oder automatisch gewählten) Bildern ein stilles
    9:16-Slideshow-Video mit Crossfade-Übergängen und legt es im Reels-Pool ab."""
    images = image_paths or _source_images_for_reel(IMAGES_PER_REEL)
    if len(images) < 2:
        return {'success': False, 'error': 'Nicht genug bereits gepostete Bilder für ein Reel vorhanden.'}

    filters = []
    inputs = []
    for img in images:
        inputs += ['-loop', '1', '-t', str(SECONDS_PER_IMAGE + CROSSFADE_SECONDS), '-i', img]
    label_png = _get_ki_label_png()
    inputs += ['-loop', '1', '-i', label_png]

    # Flyer-Bilder sind 4:5, Reels brauchen 9:16 - ein reiner Crop auf 9:16 würde
    # Text am linken/rechten Rand abschneiden. Stattdessen: unscharfer, abgedunkelter
    # Hintergrund (bildfüllend zugeschnitten) plus das komplette Originalbild zentriert
    # darüber (bildschirmfüllend eingepasst, nichts abgeschnitten).
    n = len(images)
    labels = []
    for i in range(n):
        filters.append(
            f'[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,'
            f'crop=1080:1920,gblur=sigma=25,eq=brightness=-0.15[bg{i}]'
        )
        filters.append(
            f'[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg{i}]'
        )
        filters.append(
            f'[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,fps=30[v{i}]'
        )
        labels.append(f'v{i}')

    cur = labels[0]
    offset = SECONDS_PER_IMAGE
    for i in range(1, n):
        nxt = f'x{i}'
        filters.append(
            f'[{cur}][{labels[i]}]xfade=transition=fade:duration={CROSSFADE_SECONDS}:offset={offset}[{nxt}]'
        )
        cur = nxt
        offset += SECONDS_PER_IMAGE

    # KI-Kennzeichnung fest über die gesamte Videolaufzeit einblenden (dieselbe
    # Pflichtangabe wie bei den Bild-Posts, die Quellbilder hier tragen sie noch nicht).
    filters.append(f'[{cur}][{n}:v]overlay=0:H-h:shortest=1[outv]')

    filter_complex = ';'.join(filters)
    filename = f"reel_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    out_path = os.path.join(pool_dir(), filename)

    # Instagram lehnt REELS-Videos ohne Audiospur beim Verarbeiten ab (status_code
    # ERROR, Fehlercode 2207076) - eine stille Tonspur mit "normaler" Bitrate/Samplerate
    # mitgeben (eine quasi-leere ~2kbit/s-Spur wurde ebenfalls als ungültig abgelehnt).
    silent_audio_idx = n + 1
    inputs += ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000']

    cmd = [_ffmpeg_binary(), '-y', *inputs,
           '-filter_complex', filter_complex,
           '-map', '[outv]', '-map', f'{silent_audio_idx}:a',
           '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-shortest',
           '-movflags', '+faststart',
           out_path]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not os.path.exists(out_path):
            return {'success': False, 'error': f'ffmpeg-Fehler: {result.stderr[-800:]}'}
        return {'success': True, 'filename': filename, 'path': out_path}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def maybe_auto_refill_pool(minimum=1):
    """Legt automatisch ein neues Reel an, wenn der Pool leer/knapp ist und genug
    Quellbilder vorhanden sind."""
    if len(get_reels_queue()) >= minimum:
        return
    result = generate_reel_from_images()
    if result['success']:
        print(f"[Reels] Neues Reel automatisch erstellt: {result['filename']}")
    elif 'Nicht genug' not in result.get('error', ''):
        print(f"[Reels] Auto-Generierung fehlgeschlagen: {result.get('error')}")


# ─── Graph API ──────────────────────────────────────────────────────

def is_configured():
    return instagram_engine.is_configured()


def _publish_reel(video_url, caption):
    """Postet als Instagram-Story (24h, kein Feed-Eintrag) statt als dauerhaftes Feed-Reel -
    Stories zeigen keine Caption an, daher wird sie nur für die Historie generiert/geloggt."""
    cfg = instagram_engine._graph_config()
    if not is_configured():
        return {'success': False, 'error': 'Instagram Access-Token oder Business-Account-ID fehlt in den Einstellungen.'}

    try:
        create_resp = req.post(
            f"{GRAPH_BASE}/{cfg['ig_user_id']}/media",
            data={
                'media_type': 'STORIES',
                'video_url': video_url,
                'access_token': cfg['access_token'],
            },
            timeout=30
        )
        create_data = create_resp.json()
        if not create_resp.ok or 'id' not in create_data:
            return {'success': False, 'error': f'Media-Erstellung fehlgeschlagen: {create_data}'}
        creation_id = create_data['id']

        # Video-Verarbeitung dauert länger als bei Bildern - mit Pausen auf FINISHED warten.
        for attempt in range(30):
            status_resp = req.get(
                f"{GRAPH_BASE}/{creation_id}",
                params={'fields': 'status_code,status', 'access_token': cfg['access_token']},
                timeout=30
            )
            status_code = status_resp.json().get('status_code')
            if status_code == 'FINISHED':
                break
            if status_code == 'ERROR':
                return {'success': False, 'error': f'Video-Verarbeitung fehlgeschlagen: {status_resp.json()}'}
            time.sleep(5)
        else:
            return {'success': False, 'error': 'Zeitüberschreitung bei der Video-Verarbeitung.'}

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


def post_next_reel_in_queue(public_base_url):
    maybe_auto_refill_pool()
    queue = get_reels_queue()
    if not queue:
        return {'success': False, 'error': 'Keine Videos in der Reels-Warteschlange.'}

    filename = queue[0]
    s = _reels_settings()
    caption = instagram_engine._generate_caption_from_filename(filename, s.get('default_caption', ''))
    video_url = f"{public_base_url.rstrip('/')}/api/instagram/reels/media/{quote(filename)}"

    result = _publish_reel(video_url, caption)

    if result['success']:
        _log_post(filename, caption, 'posted')
        src = os.path.join(pool_dir(), filename)
        dst = os.path.join(posted_dir(), filename)
        try:
            os.replace(src, dst)
        except Exception as e:
            print(f'[Reels] Konnte Datei nicht archivieren: {e}')

        settings = _load_settings()
        rs = settings.setdefault('reels_settings', {})
        rs['last_posted'] = datetime.now().isoformat()
        _save_settings(settings)
        return {'success': True, 'filename': filename}
    else:
        _log_post(filename, caption, 'error', result.get('error'))
        return {'success': False, 'error': result.get('error')}


# ─── Auto-Scheduler ─────────────────────────────────────────────────

def maybe_auto_post(public_base_url):
    s = _reels_settings()
    if s.get('auto_enabled') != '1':
        return

    now = datetime.now()
    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    today = day_map[now.weekday()]
    active_days = (s.get('post_days') or 'mon,tue,wed,thu,fri,sat,sun').split(',')
    if today not in active_days:
        return

    times = (s.get('post_times') or '11:00').split(',')
    current_hm = now.strftime('%H:%M')
    if current_hm not in [t.strip() for t in times]:
        return

    slot_key = f'{now.strftime("%Y-%m-%d")}_{current_hm}'
    if s.get('auto_post_last_slot') == slot_key:
        return

    settings = _load_settings()
    settings.setdefault('reels_settings', {})['auto_post_last_slot'] = slot_key
    _save_settings(settings)

    maybe_auto_refill_pool()
    if get_reels_queue():
        post_next_reel_in_queue(public_base_url)
