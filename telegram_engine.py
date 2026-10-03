"""Telegram-Anbindung: Stefan gibt Siggi per Text- oder Sprachnachricht Aufgaben.

- Eigener Bot (Token von @BotFather), eingetragen in der Agenten-Zentrale.
- Nur EIN Telegram-Chat darf Siggi steuern: gekoppelt per Einmal-Code (/start <code>),
  alle anderen Absender werden ignoriert.
- Abruf per Long-Polling (getUpdates) statt Webhook - kein oeffentlicher Endpunkt noetig.
- Sprachnachrichten werden lokal transkribiert (stt_transcribe.py, faster-whisper).
- Die eigentliche Antwort kommt aus Siggis normalem Chat (inkl. aller Werkzeuge) - app.py
  uebergibt dafuer chat_fn an poll_forever().
- Laeuft als Agent 'telegram' in der Agenten-Zentrale.
"""
import json
import os
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
HELP = ('Schreib oder sprich mir einfach, was ich tun soll – z. B. „Erinnere mich morgen um 9 an den Anruf bei '
        'Fischmann“, „Was steht heute im Kalender?“ oder „Schreib Kunde X, dass das Angebot kommt“.\n\n'
        'Alles, was ich im Dashboard-Chat kann, geht auch hier.')


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

def send(text, chat_id=None):
    cfg = _cfg()
    chat_id = chat_id or cfg.get('chat_id')
    if not (cfg.get('bot_token') and chat_id):
        return False
    text = text or '…'
    for i in range(0, len(text), 4000):  # Telegram-Limit 4096 Zeichen
        _call(cfg['bot_token'], 'sendMessage', chat_id=chat_id, text=text[i:i + 4000])
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


def _handle(update, chat_fn):
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

    if text in ('/start', '/hilfe', '/help'):
        send(HELP)
        return

    heard = ''
    voice = msg.get('voice') or msg.get('audio')
    if voice:
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
        send('Ich verstehe bisher Text- und Sprachnachrichten.')
        return

    agents.event('telegram', f'Aufgabe von Stefan: {text[:200]}')
    agents.step('telegram', f'Kümmere mich darum: „{text[:70]}“ …')
    _call(token, 'sendChatAction', chat_id=chat_id, action='typing')
    try:
        reply, actions = chat_fn(text)
    except Exception as e:
        agents.fail('telegram', f'Siggi konnte nicht antworten: {e}')
        send(heard + '⚠️ Da ist gerade etwas schiefgegangen – versuch es bitte gleich nochmal.')
        return
    if actions:
        reply += '\n\n👉 Das muss ich noch bestätigt bekommen – bitte im Dashboard freigeben.'
    send(heard + reply)
    agents.done('telegram', f'Erledigt: {text[:80]}', bubble=IDLE_BUBBLE, status='ready')


def poll_forever(chat_fn):
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
                              json={'offset': offset, 'timeout': 50, 'allowed_updates': ['message']}, timeout=60)
            updates = r.json().get('result', [])
        except Exception as e:
            print(f'[Telegram] Abruf-Fehler: {e}')
            time.sleep(10)
            continue
        for upd in updates:
            offset = upd['update_id'] + 1
            _save_offset(offset)  # vor der Verarbeitung: eine kaputte Nachricht blockiert nicht ewig
            try:
                _handle(upd, chat_fn)
            except Exception as e:
                print(f'[Telegram] Verarbeitungs-Fehler: {e}')
                agents.fail('telegram', e)
