"""Outlook-Agent: Stefans PRIVATES Outlook.com/Hotmail-Postfach lesen - strikt getrennt von Chefblick.

Trennung zu Chefblick (Stefans Vorgabe - der Mail-Agent antwortet dort automatisch):
- Eigene Datenbank (outlook.db), eigene Token-Datei. Beruehrt NIE mails.db, mail_engine, Kontakte,
  Nachfass-Agent, Auto-Antworten oder den Autopilot.
- Nur das Microsoft-Recht "Mail.Read": Senden, Loeschen, Verschieben oder als-gelesen-markieren ist
  mit diesem Token technisch unmoeglich - auch bei einem Fehler in Siggi.
- Hinweise auf wichtige Mails gehen NUR per Telegram an Stefan, nie per Mail (der Mail-Fallback
  wuerde private Inhalte ins Chefblick-Postfach legen, das der Mail-Agent abruft).

Anmeldung per Microsoft "Geraete-Code" (Device Code Flow): Stefan oeffnet microsoft.com/link, gibt
einen Code ein und klickt "Zulassen" - einmalig. Danach erneuert Siggi den Zugang selbst; er laeuft
nur ab, wenn er 90 Tage gar nicht benutzt wird.
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

import agents_engine
import settings_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'outlook.db')
TOKEN_PATH = os.path.join(BASE_DIR, 'outlook_token.json')  # gitignored, chmod 600

AUTHORITY = 'https://login.microsoftonline.com/consumers/oauth2/v2.0'  # nur private Microsoft-Konten
GRAPH = 'https://graph.microsoft.com/v1.0'
SCOPES = 'offline_access Mail.Read User.Read'
AGENT_ID = 'outlook'

DEFAULTS = {
    'client_id': '',
    'check_minutes': 10,
    # Wichtig = Telegram-Hinweis. Alles andere liest Siggi nur auf Nachfrage im Chat.
    'senders': ['kleinanzeigen.de', 'immobilienscout24.de', 'immowelt.de', 'boardinghaus-zolling.de'],
    # bewusst kein "zimmer" - meldete sonst auch "Zimmerpflanzen"-Werbung
    'keywords': ['wohnung', 'besichtigung', 'mietvertrag', 'pension', 'vermiet', 'kaution',
                 'einzug', 'zusage', 'absage'],
    'notify_all': False,
}

_lock = threading.Lock()
_pending = {}  # laufende Anmeldung: user_code, verification_uri, expires_at, error


# ─── Einstellungen & Token ─────────────────────────────────────────────

def config():
    cfg = dict(DEFAULTS)
    cfg.update(settings_store.load().get('outlook') or {})
    return cfg


def save_config(data):
    def clean_list(v):
        if isinstance(v, str):
            v = v.split('\n')
        return [s.strip().lower()[:100] for s in (v or []) if str(s).strip()][:50]

    def upd(s):
        cfg = dict(DEFAULTS)
        cfg.update(s.get('outlook') or {})
        if 'client_id' in data:
            cid = str(data['client_id']).strip()
            if cid and not re.fullmatch(r'[0-9a-fA-F-]{36}', cid):
                raise ValueError('Die Anwendungs-ID sieht so aus: 1a2b3c4d-....-.... (36 Zeichen)')
            cfg['client_id'] = cid
        if 'check_minutes' in data:
            cfg['check_minutes'] = max(5, min(240, int(data['check_minutes'])))
        if 'senders' in data:
            cfg['senders'] = clean_list(data['senders'])
        if 'keywords' in data:
            cfg['keywords'] = clean_list(data['keywords'])
        if 'notify_all' in data:
            cfg['notify_all'] = bool(data['notify_all'])
        s['outlook'] = cfg
    settings_store.update(upd)


def _load_token():
    try:
        with open(TOKEN_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_token(tok):
    tmp = TOKEN_PATH + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(tok, f)
    os.replace(tmp, TOKEN_PATH)


def _store_token_response(data, account=None):
    old = _load_token() or {}
    _save_token({
        'access_token': data['access_token'],
        # Microsoft liefert bei jeder Erneuerung einen neuen Refresh-Token (rollierend)
        'refresh_token': data.get('refresh_token') or old.get('refresh_token'),
        'expires_at': time.time() + int(data.get('expires_in', 3600)) - 120,
        'account': account or old.get('account'),
    })


class NotConnected(Exception):
    pass


def _access_token():
    with _lock:
        tok = _load_token()
        if not tok or not tok.get('refresh_token'):
            raise NotConnected('Outlook ist nicht verbunden')
        if tok.get('expires_at', 0) > time.time():
            return tok['access_token']
        r = requests.post(AUTHORITY + '/token', data={
            'client_id': config()['client_id'], 'grant_type': 'refresh_token',
            'refresh_token': tok['refresh_token'], 'scope': SCOPES,
        }, timeout=20)
        data = r.json()
        if r.status_code != 200:
            if data.get('error') in ('invalid_grant', 'interaction_required', 'invalid_client', 'unauthorized_client'):
                _mark_expired(data.get('error_description', data.get('error', '')))
                raise NotConnected('Outlook-Zugang abgelaufen - bitte neu verbinden')
            raise RuntimeError(f"Microsoft-Anmeldung fehlgeschlagen: {data.get('error_description', r.text)[:200]}")
        _store_token_response(data)
        return data['access_token']


def _mark_expired(reason):
    """Zugang ungueltig (z.B. 90 Tage ungenutzt oder Zugriff in Microsoft widerrufen)."""
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass
    agents_engine.event(AGENT_ID, f'Outlook-Zugang abgelaufen: {reason[:150]}', 'error')
    # Nur Telegram, keine Mail (siehe Modulkommentar)
    agents_engine.notify_stefan('⚠️ Der Zugang zu deinem privaten Outlook ist abgelaufen. '
                                'Bitte in Siggi → Agenten → Outlook-Agent neu verbinden.')


def status():
    cfg = config()
    tok = _load_token()
    pend = dict(_pending) if _pending and _pending.get('expires_at', 0) > time.time() else None
    if _pending.get('error') and not pend:
        pend = {'error': _pending['error']}
    return {
        'configured': bool(cfg['client_id']),
        'connected': bool(tok and tok.get('refresh_token')),
        'account': (tok or {}).get('account'),
        'pending': pend,
        'last_check': _get_state('last_check'),
        'check_minutes': cfg['check_minutes'],
        'senders': cfg['senders'], 'keywords': cfg['keywords'], 'notify_all': cfg['notify_all'],
        'client_id': cfg['client_id'],
    }


# ─── Anmeldung per Geraete-Code ────────────────────────────────────────

def start_connect():
    cid = config()['client_id']
    if not cid:
        raise ValueError('Zuerst die Anwendungs-ID aus dem Microsoft-Portal eintragen')
    r = requests.post(AUTHORITY + '/devicecode', data={'client_id': cid, 'scope': SCOPES}, timeout=20)
    data = r.json()
    if r.status_code != 200:
        raise ValueError(_explain_error(data))
    _pending.clear()
    _pending.update({
        'user_code': data['user_code'], 'verification_uri': data.get('verification_uri', 'https://microsoft.com/link'),
        'expires_at': time.time() + int(data.get('expires_in', 900)),
    })
    threading.Thread(target=_poll_device_code, args=(cid, data['device_code'], int(data.get('interval', 5)),
                                                      _pending['expires_at']), daemon=True).start()
    return {'user_code': data['user_code'], 'verification_uri': _pending['verification_uri']}


def _explain_error(data):
    desc = data.get('error_description', '') or data.get('error', '')
    if 'AADSTS7000218' in desc or 'client_assertion' in desc:
        return 'Im Microsoft-Portal unter "Authentifizierung" die Option "Öffentliche Clientflows zulassen" auf JA stellen.'
    if 'AADSTS700016' in desc or 'not found in the directory' in desc:
        return 'Anwendungs-ID nicht gefunden - bitte genau aus dem Microsoft-Portal kopieren.'
    if 'AADSTS50059' in desc or 'AADSTS9002331' in desc or 'personal' in desc.lower():
        return ('Die App erlaubt keine privaten Microsoft-Konten. Im Portal unter "Authentifizierung" → '
                '"Unterstützte Kontotypen" persönliche Microsoft-Konten erlauben.')
    return f'Microsoft meldet: {desc[:250]}'


def _poll_device_code(cid, device_code, interval, expires_at):
    while time.time() < expires_at:
        time.sleep(interval)
        try:
            r = requests.post(AUTHORITY + '/token', data={
                'client_id': cid, 'device_code': device_code,
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            }, timeout=20)
            data = r.json()
        except Exception:
            continue
        err = data.get('error')
        if err == 'authorization_pending':
            continue
        if err == 'slow_down':
            interval += 5
            continue
        if err:
            _pending.clear()
            _pending['error'] = ('Anmeldung abgelehnt' if err == 'authorization_declined'
                                 else 'Code abgelaufen - bitte neu starten' if err == 'expired_token'
                                 else _explain_error(data))
            return
        _store_token_response(data)
        _pending.clear()
        try:
            me = _graph('/me', params={'$select': 'mail,userPrincipalName,displayName'})
            account = me.get('mail') or me.get('userPrincipalName') or me.get('displayName')
            tok = _load_token()
            tok['account'] = account
            _save_token(tok)
        except Exception as e:
            account = None
            print(f'[Outlook] Kontoname nicht lesbar: {e}')
        agents_engine.event(AGENT_ID, f'Mit Outlook verbunden ({account or "Konto"}) - nur Lesezugriff', 'success')
        # Bestehende Mails als "schon gesehen" merken, damit nicht der ganze Posteingang gemeldet wird
        threading.Thread(target=run_as_agent, kwargs={'baseline': True}, daemon=True).start()
        return
    if _pending.get('expires_at') == expires_at:
        _pending.clear()
        _pending['error'] = 'Code abgelaufen - bitte neu starten'


def disconnect():
    with _lock:
        try:
            os.remove(TOKEN_PATH)
        except OSError:
            pass
    _pending.clear()
    agents_engine.event(AGENT_ID, 'Outlook-Verbindung getrennt (in Siggi gelöscht)', 'info')


# ─── Microsoft Graph (nur lesend) ──────────────────────────────────────

def _graph(path, params=None, text_body=False):
    headers = {'Authorization': 'Bearer ' + _access_token()}
    if text_body:
        headers['Prefer'] = 'outlook.body-content-type="text"'
    r = requests.get(GRAPH + path, params=params, headers=headers, timeout=30)
    if r.status_code == 401:
        raise NotConnected('Outlook-Zugang ungültig - bitte neu verbinden')
    if r.status_code >= 400:
        raise RuntimeError(f'Outlook antwortet mit {r.status_code}: {r.text[:200]}')
    return r.json()


_SELECT = 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments,webLink'


def _kurz(m):
    sender = (m.get('from') or {}).get('emailAddress') or {}
    try:
        empfangen = datetime.fromisoformat(m['receivedDateTime'].replace('Z', '+00:00')).astimezone().strftime('%d.%m.%Y %H:%M')
    except (KeyError, ValueError):
        empfangen = m.get('receivedDateTime')
    return {
        'id': m['id'], 'von': f"{sender.get('name', '')} <{sender.get('address', '')}>".strip(),
        'betreff': m.get('subject') or '(kein Betreff)', 'empfangen': empfangen,
        'vorschau': (m.get('bodyPreview') or '')[:300], 'ungelesen': not m.get('isRead', True),
        'anhang': bool(m.get('hasAttachments')), 'link': m.get('webLink'),
    }


def neueste(anzahl=10, nur_ungelesen=False, ordner='inbox'):
    params = {'$top': max(1, min(int(anzahl or 10), 30)), '$select': _SELECT, '$orderby': 'receivedDateTime desc'}
    if nur_ungelesen:
        params['$filter'] = 'isRead eq false'
    ordner = ordner if ordner in ('inbox', 'sentitems', 'junkemail') else 'inbox'
    return [_kurz(m) for m in _graph(f'/me/mailFolders/{ordner}/messages', params).get('value', [])]


def suchen(begriff, anzahl=10):
    # Graph-$search: Absender, Betreff und Text; Sortierung ist bei $search nicht erlaubt
    begriff = str(begriff or '').replace('"', ' ').strip()[:100]
    if not begriff:
        return []
    params = {'$search': f'"{begriff}"', '$top': max(1, min(int(anzahl or 10), 25)), '$select': _SELECT}
    treffer = [_kurz(m) for m in _graph('/me/messages', params).get('value', [])]
    return sorted(treffer, key=lambda t: datetime.strptime(t['empfangen'], '%d.%m.%Y %H:%M')
                  if re.match(r'\d\d\.\d\d\.\d{4}', str(t['empfangen'])) else datetime.min, reverse=True)


def lesen(message_id, max_zeichen=6000):
    m = _graph(f'/me/messages/{message_id}',
               params={'$select': _SELECT + ',body,toRecipients'}, text_body=True)
    info = _kurz(m)
    text = unescape((m.get('body') or {}).get('content') or '')
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    info['text'] = text[:max_zeichen] + ('\n[... gekürzt]' if len(text) > max_zeichen else '')
    info['an'] = ', '.join(((r.get('emailAddress') or {}).get('address') or '') for r in m.get('toRecipients') or [])
    info.pop('vorschau', None)
    return info


# ─── Agent: neue Mails pruefen, Wichtiges per Telegram melden ─────────

def _db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, received_at TEXT, wichtig INTEGER, created_at TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
    return c


def _get_state(key):
    try:
        c = _db()
        row = c.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        c.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _set_state(c, key, value):
    c.execute('INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, value))


def wichtig_grund(mail, cfg):
    """Regeln statt KI (kostet nichts): Absender oder Stichwort aus den Einstellungen."""
    if cfg.get('notify_all'):
        return 'alle Mails melden'
    von = mail['von'].lower()
    for s in cfg['senders']:
        if s and s in von:
            return f'Absender „{s}“'
    blob = f"{mail['betreff']} {mail['vorschau']}".lower()
    for k in cfg['keywords']:
        if k and re.search(r'(?<!\w)' + re.escape(k), blob):
            return f'Stichwort „{k}“'
    return None


def ist_faellig():
    letzte = _get_state('last_check')
    if not letzte:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(letzte) >= timedelta(minutes=config()['check_minutes'])
    except ValueError:
        return True


def run_as_agent(baseline=False):
    """Prueft den Posteingang auf neue Mails. baseline=True (direkt nach dem Verbinden): alles als
    gesehen merken, nichts melden."""
    tok = _load_token()
    if not tok or not tok.get('refresh_token'):
        return
    if not agents_engine.start(AGENT_ID, 'Schaue in dein privates Postfach …', quiet=True):
        return
    cfg = config()
    c = _db()
    try:
        _set_state(c, 'last_check', datetime.now().isoformat(timespec='seconds'))
        c.commit()
        mails = neueste(25)
        bekannt = {r[0] for r in c.execute(
            f"SELECT id FROM seen WHERE id IN ({','.join('?' * len(mails))})", [m['id'] for m in mails])} if mails else set()
        neu = [m for m in mails if m['id'] not in bekannt]
        gemeldet = 0
        for m in reversed(neu):  # aelteste zuerst melden
            grund = None if baseline else wichtig_grund(m, cfg)
            if grund and _melden(m, grund):
                gemeldet += 1
            c.execute('INSERT OR IGNORE INTO seen (id, received_at, wichtig, created_at) VALUES (?, ?, ?, ?)',
                      (m['id'], m['empfangen'], 1 if grund else 0, datetime.now().isoformat(timespec='seconds')))
        # Merkliste klein halten
        c.execute("DELETE FROM seen WHERE created_at < ?", ((datetime.now() - timedelta(days=60)).isoformat(),))
        c.commit()
        ungelesen = sum(1 for m in mails if m['ungelesen'])
        if baseline:
            summary = f'Verbunden – {len(mails)} vorhandene Mails gemerkt, ab jetzt melde ich nur Neues'
        elif gemeldet:
            summary = f'{gemeldet} wichtige neue Mail(s) per Telegram gemeldet'
        else:
            summary = f'{len(neu)} neue Mail(s), nichts Wichtiges · {ungelesen} ungelesen'
        agents_engine.done(AGENT_ID, summary, quiet=not (gemeldet or baseline), bubble=summary)
        if gemeldet:
            agents_engine.event(AGENT_ID, summary, 'success')
    except NotConnected as e:
        agents_engine.done(AGENT_ID, str(e), status='error', bubble=str(e))
    except Exception as e:
        agents_engine.fail(AGENT_ID, str(e)[:200])
    finally:
        c.close()


def _melden(mail, grund):
    """Nur Telegram - nie Mail (privat bleibt privat, Chefblick-Postfach bleibt unberuehrt)."""
    text = (f"📬 Private Mail ({grund})\n"
            f"Von: {mail['von']}\nBetreff: {mail['betreff']}\n\n{mail['vorschau'][:400]}")
    try:
        import telegram_engine  # spaet: telegram_engine importiert agents_engine
        if not telegram_engine.status().get('paired'):
            return False
        buttons = [[{'text': '📖 In Outlook öffnen', 'url': mail['link']}]] if str(mail.get('link') or '').startswith('https://') else None
        return bool(telegram_engine.send(text, buttons=buttons))
    except Exception as e:
        print(f'[Outlook] Telegram-Hinweis fehlgeschlagen: {e}')
        return False
