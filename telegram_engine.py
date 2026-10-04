"""Telegram-Anbindung: Stefan gibt Siggi per Text- oder Sprachnachricht Aufgaben.

- Eigener Bot (Token von @BotFather), eingetragen in der Agenten-Zentrale.
- Nur EIN Telegram-Chat darf Siggi steuern: gekoppelt per Einmal-Code (/start <code>),
  alle anderen Absender werden ignoriert.
- Abruf per Long-Polling (getUpdates) statt Webhook - kein oeffentlicher Endpunkt noetig.
- Sprachnachrichten werden lokal transkribiert (stt_transcribe.py, faster-whisper) und
  per Sprachnachricht in Siggis Stimme beantwortet (edge-tts, wie im Dashboard). /stimme
  schaltet Sprachantworten auch fuer Textnachrichten an/aus.
- Die eigentliche Antwort kommt aus Siggis normalem Chat (inkl. aller Werkzeuge) - app.py
  uebergibt dafuer chat_fn an poll_forever().
- Laeuft als Agent 'telegram' in der Agenten-Zentrale.
"""
import json
import os
import re
import secrets
import subprocess
import tempfile
import time

import requests

import agents_engine as agents
import settings_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, 'telegram_state.json')  # nur der Update-Offset
VENV_PY = os.path.join(BASE_DIR, 'venv', 'bin', 'python3')
API = 'https://api.telegram.org/bot{token}/{method}'
FILE_API = 'https://api.telegram.org/file/bot{token}/{path}'
IDLE_BUBBLE = 'Bereit – höre auf deine Nachrichten'
VOICE_MAX_CHARS = 700  # laengere Antworten: Anfang vorlesen, Rest steht im Text
HELP = ('Schreib oder sprich mir einfach, was ich tun soll – z. B. „Erinnere mich morgen um 9 an den Anruf bei '
        'Fischmann“, „Was steht heute im Kalender?“ oder „Schreib Kunde X, dass das Angebot kommt“.\n\n'
        'Alles, was ich im Dashboard-Chat kann, geht auch hier. Schick mir auch Fotos, PDFs oder Videos '
        '(„fass zusammen“, „ab in die Instagram-Warteschlange“) oder lass dir Dateien schicken '
        '(„schick mir das Audit von chefblick.de“). Auf Sprachnachrichten antworte ich mit '
        'Sprachnachricht – mit /stimme bekommst du auch auf Textnachrichten eine.')


def _cfg():
    return dict(settings_store.load_or_empty().get('telegram') or {})


def _save_cfg(cfg):
    settings_store.update(lambda s: s.__setitem__('telegram', cfg))


def _call(token, method, timeout=30, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=timeout)
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('description', f'Telegram-Fehler {r.status_code}'))
    return data['result']


# ─── Einrichtung (aus dem Dashboard) ─────────────────────────────────

def connect(token):
    """Token pruefen und speichern; liefert den Kopplungs-Code fuer /start."""
    token = (token or '').strip()
    me = _call(token, 'getMe', timeout=15)
    code = f'{secrets.randbelow(900000) + 100000}'
    _save_cfg({'bot_token': token, 'bot_username': me.get('username'), 'pairing_code': code, 'chat_id': None})
    agents.event('telegram', f"Bot @{me.get('username')} eingerichtet – warte auf deine erste Nachricht")
    return status()


def disconnect():
    settings_store.update(lambda s: s.pop('telegram', None))
    agents.event('telegram', 'Telegram-Verbindung getrennt')


def status():
    cfg = _cfg()
    return {
        'configured': bool(cfg.get('bot_token')),
        'bot_username': cfg.get('bot_username'),
        'paired': bool(cfg.get('chat_id')),
        'pairing_code': None if cfg.get('chat_id') else cfg.get('pairing_code'),
    }


# ─── Senden ──────────────────────────────────────────────────────────

def send(text, chat_id=None, buttons=None):
    """buttons: Inline-Tastatur (Liste von Zeilen) - haengt an der letzten Teilnachricht."""
    cfg = _cfg()
    chat_id = chat_id or cfg.get('chat_id')
    if not (cfg.get('bot_token') and chat_id):
        return False
    text = text or '…'
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]  # Telegram-Limit 4096 Zeichen
    for n, chunk in enumerate(chunks):
        params = {'chat_id': chat_id, 'text': chunk}
        if buttons and n == len(chunks) - 1:
            params['reply_markup'] = {'inline_keyboard': buttons}
        _call(cfg['bot_token'], 'sendMessage', **params)
    return True


# ─── Freigeben per Knopf ─────────────────────────────────────────────
# Telegram erlaubt nur 64 Byte Knopf-Daten - deshalb ein kurzes Kennzeichen, das auf die eigentliche
# Aufgabe zeigt (Art + Referenz, z.B. Mail-Entwurf 12 oder Reel-Dateiname).
ACTIONS_PATH = os.path.join(BASE_DIR, 'telegram_actions.json')


def _load_actions():
    try:
        with open(ACTIONS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def approval_buttons(kind, ref, labels=('✅ Freigeben', '❌ Ablehnen')):
    actions = _load_actions()
    token = secrets.token_hex(5)
    actions[token] = {'kind': kind, 'ref': ref, 'at': time.time()}
    if len(actions) > 300:  # alte Eintraege wegwerfen
        actions = dict(sorted(actions.items(), key=lambda kv: kv[1].get('at', 0))[-300:])
    with open(ACTIONS_PATH, 'w') as f:
        json.dump(actions, f)
    return [[{'text': labels[0], 'callback_data': f'a:{token}:y'},
             {'text': labels[1], 'callback_data': f'a:{token}:n'}]]


def send_video_for_approval(path, caption, kind, ref):
    """Video (z.B. neues Reel) zum Anschauen schicken, mit Freigabe-Knoepfen."""
    cfg = _cfg()
    if not (cfg.get('bot_token') and cfg.get('chat_id')) or os.path.getsize(path) > MAX_SEND:
        return False
    with open(path, 'rb') as f:
        r = requests.post(API.format(token=cfg['bot_token'], method='sendVideo'),
                          data={'chat_id': cfg['chat_id'], 'caption': caption[:1000],
                                'reply_markup': json.dumps({'inline_keyboard': approval_buttons(kind, ref)})},
                          files={'video': (os.path.basename(path), f)}, timeout=300)
    return bool(r.json().get('ok'))


def _handle_callback(cq, action_fn):
    cfg = _cfg()
    msg = cq.get('message') or {}
    chat_id = (msg.get('chat') or {}).get('id')
    token = cfg.get('bot_token')
    if not cfg.get('chat_id') or chat_id != cfg['chat_id'] or (cq.get('from') or {}).get('id') != cfg['chat_id']:
        _call(token, 'answerCallbackQuery', callback_query_id=cq['id'], text='Nicht erlaubt.')
        return
    parts = (cq.get('data') or '').split(':')
    actions = _load_actions()
    entry = actions.pop(parts[1], None) if len(parts) == 3 and parts[0] == 'a' else None
    if not entry:
        _call(token, 'answerCallbackQuery', callback_query_id=cq['id'], text='Schon erledigt oder abgelaufen.')
        _call(token, 'editMessageReplyMarkup', chat_id=chat_id, message_id=msg.get('message_id'), reply_markup={'inline_keyboard': []})
        return
    with open(ACTIONS_PATH, 'w') as f:  # Knopf wirkt nur einmal - auch beim Gegenstueck (Ablehnen/Freigeben)
        json.dump({k: v for k, v in actions.items() if not (v['kind'] == entry['kind'] and v['ref'] == entry['ref'])}, f)
    approve = parts[2] == 'y'
    _call(token, 'answerCallbackQuery', callback_query_id=cq['id'], text='Wird erledigt …')
    # Link-Knoepfe (z.B. "Inserat oeffnen") bleiben stehen, nur die Entscheidungs-Knoepfe verschwinden
    link_rows = [row for row in ((msg.get('reply_markup') or {}).get('inline_keyboard') or [])
                 if all('url' in b for b in row)]
    _call(token, 'editMessageReplyMarkup', chat_id=chat_id, message_id=msg.get('message_id'), reply_markup={'inline_keyboard': link_rows})
    try:
        result = action_fn(entry['kind'], entry['ref'], approve)
    except Exception as e:
        result = f'⚠️ Hat nicht geklappt: {e}'
    agents.event('telegram', f"Per Telegram {'freigegeben' if approve else 'abgelehnt'}: {entry['kind']} {entry['ref']} → {str(result)[:120]}",
                 'success' if not str(result).startswith('⚠️') else 'warn')
    send(result)


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


def send_voice(text, clean_fn=None, chat_id=None):
    # Antwort als Telegram-Sprachnachricht in Siggis Stimme (edge-tts -> OGG/Opus)
    cfg = _cfg()
    chat_id = chat_id or cfg.get('chat_id')
    settings = settings_store.load_or_empty()
    spoken = clean_fn(text) if clean_fn else text
    spoken = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]', '', spoken).strip()  # Emojis nicht vorlesen
    if len(spoken) > VOICE_MAX_CHARS:
        spoken = spoken[:VOICE_MAX_CHARS].rsplit(' ', 1)[0] + ' … den Rest habe ich dir aufgeschrieben.'
    if not spoken:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        mp3, ogg = os.path.join(tmp, 'a.mp3'), os.path.join(tmp, 'a.ogg')
        subprocess.run(['edge-tts', '--voice', settings.get('tts_voice', 'de-DE-ConradNeural'),
                        f"--pitch={settings.get('tts_pitch', '+0Hz')}", f"--rate={settings.get('tts_rate', '+0%')}",
                        '--text', spoken, '--write-media', mp3], check=True, capture_output=True, timeout=90)
        subprocess.run([_ffmpeg(), '-y', '-loglevel', 'error', '-i', mp3, '-c:a', 'libopus', '-b:a', '32k', '-ac', '1', ogg],
                       check=True, capture_output=True, timeout=60)
        with open(ogg, 'rb') as f:
            r = requests.post(API.format(token=cfg['bot_token'], method='sendVoice'),
                              data={'chat_id': chat_id}, files={'voice': ('siggi.ogg', f, 'audio/ogg')}, timeout=60)
        if not r.json().get('ok'):
            raise RuntimeError(r.json().get('description', 'sendVoice fehlgeschlagen'))
    return True


# ─── Empfangen ───────────────────────────────────────────────────────

def _load_offset():
    try:
        with open(STATE_PATH) as f:
            return json.load(f).get('offset', 0)
    except Exception:
        return 0


def _save_offset(offset):
    with open(STATE_PATH, 'w') as f:
        json.dump({'offset': offset}, f)


def _transcribe(token, file_id):
    info = _call(token, 'getFile', file_id=file_id)
    audio = requests.get(FILE_API.format(token=token, path=info['file_path']), timeout=60).content
    with tempfile.NamedTemporaryFile(suffix='.oga', delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        r = subprocess.run([VENV_PY, os.path.join(BASE_DIR, 'stt_transcribe.py'), path],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'Spracherkennung fehlgeschlagen')
        return r.stdout.strip()
    finally:
        os.remove(path)


# ─── Dateien ─────────────────────────────────────────────────────────

INBOX_DIR = os.path.join(BASE_DIR, 'telegram_inbox')  # was Stefan per Telegram schickt
MAX_DOWNLOAD = 20 * 1024 * 1024   # Bot-API-Grenze fuers Herunterladen
MAX_SEND = 50 * 1024 * 1024       # Bot-API-Grenze fuers Senden
PENDING_SECONDS = 30 * 60         # Datei ohne Text: die naechste Nachricht in dieser Zeit bezieht sich darauf


def _safe_name(name):
    name = os.path.basename(name or 'datei')
    name = re.sub(r'[^\w.\- ]+', '_', name).strip(' .') or 'datei'
    return name[:120]


def inbox_path(name):
    """Absoluter Pfad einer Datei in der Telegram-Ablage - None, wenn der Name aus der Ablage herausfuehrt."""
    full = os.path.realpath(os.path.join(INBOX_DIR, os.path.basename(name or '')))
    if not full.startswith(os.path.realpath(INBOX_DIR) + os.sep) or not os.path.isfile(full):
        return None
    return full


def _incoming_file(msg):
    """Datei-Anhang einer Nachricht (Foto, Dokument, Video) - Sprachnachrichten laufen separat."""
    if msg.get('photo'):
        p = msg['photo'][-1]  # groesste Aufloesung
        return {'file_id': p['file_id'], 'name': f"foto_{time.strftime('%Y%m%d_%H%M%S')}.jpg", 'size': p.get('file_size', 0)}
    for key in ('document', 'video', 'animation'):
        d = msg.get(key)
        if d:
            ext = {'video': '.mp4', 'animation': '.mp4'}.get(key, '')
            return {'file_id': d['file_id'], 'name': d.get('file_name') or f'{key}_{time.strftime("%Y%m%d_%H%M%S")}{ext}',
                    'size': d.get('file_size', 0)}
    return None


def _download_to_inbox(token, file_id, name):
    info = _call(token, 'getFile', file_id=file_id)
    data = requests.get(FILE_API.format(token=token, path=info['file_path']), timeout=120).content
    os.makedirs(INBOX_DIR, exist_ok=True)
    stem, ext = os.path.splitext(_safe_name(name))
    path = os.path.join(INBOX_DIR, f'{stem}{ext}')
    n = 2
    while os.path.exists(path):  # nichts ueberschreiben
        path = os.path.join(INBOX_DIR, f'{stem}_{n}{ext}')
        n += 1
    with open(path, 'wb') as f:
        f.write(data)
    return path


def send_document(path, caption=None, chat_id=None):
    """Schickt Stefan eine Datei (bis 50 MB) per Telegram."""
    cfg = _cfg()
    chat_id = chat_id or cfg.get('chat_id')
    if not (cfg.get('bot_token') and chat_id):
        raise RuntimeError('Telegram ist nicht verbunden.')
    size = os.path.getsize(path)
    if size > MAX_SEND:
        raise RuntimeError(f'Datei ist {size // 1024 // 1024} MB groß – Telegram-Bots dürfen höchstens 50 MB senden.')
    with open(path, 'rb') as f:
        r = requests.post(API.format(token=cfg['bot_token'], method='sendDocument'),
                          data={'chat_id': chat_id, 'caption': (caption or '')[:1000]},
                          files={'document': (os.path.basename(path), f)}, timeout=300)
    if not r.json().get('ok'):
        raise RuntimeError(r.json().get('description', 'sendDocument fehlgeschlagen'))
    agents.event('telegram', f'Datei an Stefan geschickt: {os.path.basename(path)}', 'success')
    return True


def _handle(update, chat_fn, clean_fn=None):
    msg = update.get('message') or {}
    chat_id = (msg.get('chat') or {}).get('id')
    if not chat_id:
        return
    cfg = _cfg()
    token = cfg['bot_token']
    text = (msg.get('text') or '').strip()

    # Noch nicht gekoppelt: nur der richtige Einmal-Code bindet diesen Chat an Siggi
    if not cfg.get('chat_id'):
        if text == f"/start {cfg.get('pairing_code')}":
            cfg['chat_id'] = chat_id
            cfg['pairing_code'] = None
            _save_cfg(cfg)
            send('✅ Verbunden! Ab jetzt hört Siggi hier auf dich.\n\n' + HELP, chat_id)
            agents.done('telegram', 'Mit deinem Telegram verbunden', bubble=IDLE_BUBBLE, status='ready')
        else:
            _call(token, 'sendMessage', chat_id=chat_id, text='Dieser Bot ist privat.')
        return
    if chat_id != cfg['chat_id']:
        return  # fremde Absender still ignorieren

    if text in ('/hilfe', '/help') or text.startswith('/start'):  # /start <code> nach der Kopplung = nur Hilfe
        send(HELP)
        return
    if text == '/stimme':
        cfg['voice_always'] = not cfg.get('voice_always')
        _save_cfg(cfg)
        send('🔊 Ab jetzt antworte ich dir immer auch per Sprachnachricht.' if cfg['voice_always']
             else '🔇 Sprachantworten nur noch, wenn du mir eine Sprachnachricht schickst.')
        return

    heard = ''
    attachment = None
    incoming = _incoming_file(msg)
    if incoming:
        agents.start('telegram', f"Lade deine Datei herunter: {incoming['name']} …", text=f"Datei erhalten: {incoming['name']}")
        if incoming['size'] and incoming['size'] > MAX_DOWNLOAD:
            agents.done('telegram', 'Datei zu groß für Telegram-Bots', bubble=IDLE_BUBBLE, status='ready')
            send(f"📎 {incoming['name']} ist {incoming['size'] // 1024 // 1024} MB groß – Telegram lässt Bots nur Dateien "
                 f"bis 20 MB herunterladen. Lad sie bitte im Dashboard hoch.")
            return
        try:
            path = _download_to_inbox(token, incoming['file_id'], incoming['name'])
        except Exception as e:
            agents.fail('telegram', f'Datei konnte nicht geladen werden: {e}')
            send('⚠️ Die Datei konnte ich leider nicht herunterladen. Versuch es bitte nochmal.')
            return
        attachment = {'path': path, 'name': os.path.basename(path)}
        text = (msg.get('caption') or '').strip()
        if not text:
            # Ohne Begleittext: merken und nachfragen - die naechste Nachricht bezieht sich darauf
            cfg['pending_file'] = {'path': path, 'name': attachment['name'], 'at': time.time()}
            _save_cfg(cfg)
            agents.done('telegram', f"Datei abgelegt: {attachment['name']}", bubble=IDLE_BUBBLE, status='ready')
            send(f"📎 Hab ich: {attachment['name']} – liegt in meiner Ablage.\n\nWas soll ich damit machen? Zum Beispiel "
                 f"zusammenfassen, in die Instagram-Warteschlange legen oder ein Reel daraus machen.")
            return
        cfg.pop('pending_file', None)
        _save_cfg(cfg)

    voice = None if attachment else (msg.get('voice') or msg.get('audio'))
    if attachment:
        agents.step('telegram', f"Schaue mir {attachment['name']} an …")
    elif voice:
        agents.start('telegram', 'Höre deine Sprachnachricht ab …', text='Sprachnachricht erhalten')
        _call(token, 'sendChatAction', chat_id=chat_id, action='typing')
        try:
            text = _transcribe(token, voice['file_id'])
        except Exception as e:
            agents.fail('telegram', f'Sprachnachricht nicht verstanden: {e}')
            send('🙉 Die Sprachnachricht konnte ich leider nicht verstehen. Versuch es nochmal oder schreib mir.')
            return
        if not text:
            agents.done('telegram', 'Sprachnachricht war leer', bubble=IDLE_BUBBLE, status='ready')
            send('🙉 Ich habe nichts verstanden – war die Nachricht vielleicht stumm?')
            return
        heard = f'🎙️ „{text}“\n\n'
    elif text:
        agents.start('telegram', 'Lese deine Nachricht …', text='Nachricht erhalten')
    else:
        send('Das kann ich noch nicht verarbeiten – schick mir Text, Sprache, Fotos, Videos oder Dateien.')
        return

    # Datei kam vorhin ohne Text? Dann bezieht sich diese Nachricht darauf (einmalig, max. 30 Min.)
    pending = cfg.get('pending_file')
    if not attachment and pending and time.time() - pending.get('at', 0) < PENDING_SECONDS and os.path.isfile(pending['path']):
        attachment = {'path': pending['path'], 'name': pending['name']}
    if pending:
        cfg.pop('pending_file', None)
        _save_cfg(cfg)

    agents.event('telegram', f'Aufgabe von Stefan: {text[:200]}' + (f" (Datei: {attachment['name']})" if attachment else ''))
    agents.step('telegram', f'Kümmere mich darum: „{text[:70]}“ …')
    _call(token, 'sendChatAction', chat_id=chat_id, action='typing')
    try:
        reply, actions = chat_fn(text, attachment)
    except Exception as e:
        agents.fail('telegram', f'Siggi konnte nicht antworten: {e}')
        send(heard + '⚠️ Da ist gerade etwas schiefgegangen – versuch es bitte gleich nochmal.')
        return
    if actions:
        reply += '\n\n👉 Das muss ich noch bestätigt bekommen – bitte im Dashboard freigeben.'
    if voice or cfg.get('voice_always'):
        agents.step('telegram', 'Spreche dir die Antwort ein …')
        _call(token, 'sendChatAction', chat_id=chat_id, action='record_voice')
        try:
            send_voice(reply, clean_fn)
        except Exception as e:
            print(f'[Telegram] Sprachantwort fehlgeschlagen: {e}')
            agents.event('telegram', f'Sprachantwort fehlgeschlagen, nur Text gesendet: {e}', 'warn')
    send(heard + reply)
    agents.done('telegram', f'Erledigt: {text[:80]}', bubble=IDLE_BUBBLE, status='ready')


def poll_forever(chat_fn, clean_fn=None, action_fn=None):
    """Endlosschleife (Thread in app.py). Ohne Token wartet sie einfach."""
    offset = _load_offset()
    while True:
        cfg = _cfg()
        token = cfg.get('bot_token')
        if not token or agents.is_paused('telegram'):
            time.sleep(15)
            continue
        try:
            # Long-Polling: Telegram haelt die Anfrage bis zu 50 s offen, bis eine Nachricht kommt
            r = requests.post(API.format(token=token, method='getUpdates'),
                              json={'offset': offset, 'timeout': 50, 'allowed_updates': ['message', 'callback_query']},
                              timeout=60)
            updates = r.json().get('result', [])
        except Exception as e:
            print(f'[Telegram] Abruf-Fehler: {e}')
            time.sleep(10)
            continue
        for upd in updates:
            offset = upd['update_id'] + 1
            _save_offset(offset)  # vor der Verarbeitung: eine kaputte Nachricht blockiert nicht ewig
            try:
                if 'callback_query' in upd:
                    if action_fn:
                        _handle_callback(upd['callback_query'], action_fn)
                    continue
                _handle(upd, chat_fn, clean_fn)
            except Exception as e:
                print(f'[Telegram] Verarbeitungs-Fehler: {e}')
                agents.fail('telegram', e)
