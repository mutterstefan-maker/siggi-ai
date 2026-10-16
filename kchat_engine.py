"""kChat-Agent: Ueberblick ueber den Firmen-Chat der Steler GmbH (Infomaniak kChat) - strikt getrennt.

Trennung (Stefans Vorgabe: Steler, Chefblick und Privat nie vermischen):
- Eigener Bereich: eigene Datenbank (kchat.db), eigene Token-Datei, eigener Einstellungs-Schluessel.
  Beruehrt NIE mails.db, mail_engine, Kontakte, Nachfass-Agent, Outlook-Agent, Gedaechtnis oder Autopilot.
- Siggi LIEST nur. Im Code gibt es keinen einzigen Schreib-Aufruf (keine Nachricht, keine Reaktion,
  kein "als gelesen markieren") - nur GET und die zwei lesenden POST-Abfragen in _READ_POSTS.
  Auch Stefans Ungelesen-Zaehler in kChat bleibt unveraendert.
- Hinweise und Lageberichte gehen NUR per Telegram an Stefan, nie per Mail (der Mail-Fallback
  wuerde Steler-Interna ins Chefblick-Postfach legen, das der Mail-Agent abruft).

kChat ist ein Mattermost: REST-API unter https://<team>.kchat.infomaniak.com/api/v4, Anmeldung mit
einem Infomaniak-API-Token (Bereich "kChat", manager.infomaniak.com → API-Token). Ein Token, das ein
Jahr nicht benutzt wird, schaltet Infomaniak ab - bei uns laeuft es staendig, das passiert also nicht.
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import requests

import agents_engine
import settings_store
import siggi_time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'kchat.db')
TOKEN_PATH = os.path.join(BASE_DIR, 'kchat_token.json')  # gitignored, chmod 600
AGENT_ID = 'kchat'
LABEL = '🏢 Steler-Chat'

DEFAULTS = {
    'team': 'steler-gmbh',
    'check_minutes': 15,
    # Sofort-Hinweis per Telegram bei Direktnachricht oder @-Erwaehnung von Stefan
    'notify_mentions': True,
    # zusaetzliche Stichwoerter, die einen Sofort-Hinweis ausloesen (z.B. "dringend")
    'keywords': ['dringend', 'stefan'],
    # Lagebericht "was ist gerade aktuell" per Telegram
    'summary_times': ['08:00', '16:00'],
    'summary_weekdays_only': True,
    'summary_ai': True,  # Haiku fasst zusammen (Bruchteil eines Cents); aus = nur Liste
    'muted_channels': [],  # Kanalnamen, die Siggi ignoriert
}

_lock = threading.Lock()
_cache = {}  # team_id, me, user-Namen - nur im Speicher


# ─── Einstellungen & Token ─────────────────────────────────────────────

def config():
    cfg = dict(DEFAULTS)
    cfg.update(settings_store.load().get('kchat') or {})
    return cfg


def save_config(data):
    def clean_list(v, lower=True):
        if isinstance(v, str):
            v = re.split(r'[\n,]', v)
        out = [str(s).strip()[:80] for s in (v or []) if str(s).strip()]
        return [s.lower() for s in out][:50] if lower else out[:50]

    def upd(s):
        cfg = dict(DEFAULTS)
        cfg.update(s.get('kchat') or {})
        if 'team' in data:
            team = str(data['team']).strip().lower()
            m = re.search(r'([a-z0-9-]+)\.kchat\.infomaniak\.com', team)
            team = m.group(1) if m else team
            if team and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', team):
                raise ValueError('Teamname so wie in der kChat-Adresse, z.B. steler-gmbh')
            cfg['team'] = team
        if 'check_minutes' in data:
            cfg['check_minutes'] = max(1, min(240, int(data['check_minutes'])))
        if 'notify_mentions' in data:
            cfg['notify_mentions'] = bool(data['notify_mentions'])
        if 'keywords' in data:
            cfg['keywords'] = clean_list(data['keywords'])
        if 'summary_times' in data:
            times = []
            for t in clean_list(data['summary_times']):
                m = re.fullmatch(r'(\d{1,2})[:.](\d{2})', t)
                if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                    raise ValueError(f'Uhrzeit „{t}“ bitte als HH:MM, z.B. 08:00')
                times.append(f'{int(m.group(1)):02d}:{m.group(2)}')
            cfg['summary_times'] = sorted(set(times))[:6]
        if 'summary_weekdays_only' in data:
            cfg['summary_weekdays_only'] = bool(data['summary_weekdays_only'])
        if 'summary_ai' in data:
            cfg['summary_ai'] = bool(data['summary_ai'])
        if 'muted_channels' in data:
            cfg['muted_channels'] = clean_list(data['muted_channels'])
        s['kchat'] = cfg
    settings_store.update(upd)
    _cache.clear()


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


class NotConnected(Exception):
    pass


def _base():
    return f"https://{config()['team']}.kchat.infomaniak.com/api/v4"


def _token():
    tok = _load_token()
    if not tok or not tok.get('token'):
        raise NotConnected('kChat ist nicht verbunden')
    return tok['token']


# Die einzigen POST-Aufrufe - beide nur lesend (Suche, Namen zu Benutzer-IDs)
_READ_POSTS = ('/posts/search', '/users/ids')


def _api(path, params=None, json_body=None, token=None):
    headers = {'Authorization': 'Bearer ' + (token or _token()), 'User-Agent': 'Siggi'}
    if json_body is None:
        r = requests.get(_base() + path, params=params, headers=headers, timeout=30)
    else:
        if not path.endswith(_READ_POSTS):
            raise RuntimeError('Siggi liest kChat nur - Schreiben ist hier gesperrt')
        r = requests.post(_base() + path, json=json_body, headers=headers, timeout=30)
    if r.status_code == 401 and token is None:
        _mark_expired()
        raise NotConnected('kChat-Token ungültig - bitte neu verbinden')
    if r.status_code >= 400:
        try:
            msg = r.json().get('message') or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f'kChat antwortet mit {r.status_code}: {str(msg)[:200]}')
    return r.json()


def connect(token):
    """Token pruefen (wer bin ich, gibt es das Team) und speichern."""
    token = str(token or '').strip()
    if len(token) < 20 or not re.fullmatch(r'[A-Za-z0-9_\-.]+', token):
        raise ValueError('Das sieht nicht wie ein Infomaniak-API-Token aus')
    team = config()['team']
    if not team:
        raise ValueError('Zuerst den Teamnamen eintragen (aus der kChat-Adresse, z.B. steler-gmbh)')
    try:
        me = _api('/users/me', token=token)
        t = _api(f'/teams/name/{team}', token=token)
    except requests.RequestException as e:
        raise ValueError(f'kChat nicht erreichbar: {e}')
    except RuntimeError as e:
        if ' 401' in str(e) or ' 403' in str(e):
            raise ValueError('Token abgelehnt - beim Erstellen den Bereich „kChat“ auswählen')
        if ' 404' in str(e):
            raise ValueError(f'Team „{team}“ nicht gefunden - Name aus der kChat-Adresse prüfen')
        raise ValueError(str(e))
    account = (f"{me.get('first_name', '')} {me.get('last_name', '')}".strip() or me.get('username'))
    _save_token({'token': token, 'account': account, 'username': me.get('username'),
                 'team_name': t.get('display_name') or team})
    _cache.clear()
    agents_engine.event(AGENT_ID, f'Mit kChat verbunden ({account}, {t.get("display_name") or team}) - nur lesen', 'success')
    # Ab jetzt zaehlt Neues - bisherige Nachrichten nicht melden
    c = _db()
    try:
        _set_state(c, 'last_check_ms', str(int(time.time() * 1000)))
        _set_state(c, 'last_summary_ms', str(int(time.time() * 1000)))
        c.commit()
    finally:
        c.close()
    threading.Thread(target=run_as_agent, daemon=True).start()
    return {'account': account, 'team': t.get('display_name') or team}


def _mark_expired():
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass
    _cache.clear()
    agents_engine.event(AGENT_ID, 'kChat-Token abgelehnt - Verbindung entfernt', 'error')
    _telegram('⚠️ Siggi kommt nicht mehr in den Steler-kChat (Token ungültig oder gelöscht). '
              'Bitte in Siggi → Agenten → kChat-Agent ein neues Token eintragen.')


def disconnect():
    with _lock:
        try:
            os.remove(TOKEN_PATH)
        except OSError:
            pass
    _cache.clear()
    agents_engine.event(AGENT_ID, 'kChat-Verbindung getrennt (Token in Siggi gelöscht)', 'info')


def status():
    cfg = config()
    tok = _load_token() or {}
    try:
        unread = json.loads(_get_state('unread_json') or '[]')
    except ValueError:
        unread = []
    return {
        'connected': bool(tok.get('token')),
        'account': tok.get('account'), 'team_name': tok.get('team_name'),
        'last_check': _get_state('last_check'), 'last_summary': _get_state('last_summary'),
        'unread': unread,
        **{k: cfg[k] for k in DEFAULTS},
    }


# ─── kChat lesen ───────────────────────────────────────────────────────

def _me():
    if 'me' not in _cache:
        _cache['me'] = _api('/users/me')
    return _cache['me']


def _team_id():
    if 'team_id' not in _cache:
        _cache['team_id'] = _api(f"/teams/name/{config()['team']}")['id']
    return _cache['team_id']


def _user_names(ids):
    names = _cache.setdefault('users', {})
    missing = [i for i in set(ids) if i and i not in names]
    if missing:
        try:
            for u in _api('/users/ids', json_body=missing[:200]):
                names[u['id']] = (f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
                                  or u.get('nickname') or u.get('username') or '?')
        except Exception as e:
            print(f'[kChat] Namen nicht lesbar: {e}')
    return names


def _channels():
    """Alle Kanaele, in denen Stefan ist (offen, privat, Direkt- und Gruppennachrichten)."""
    chans = _api(f'/users/me/teams/{_team_id()}/channels')
    members = {m['channel_id']: m for m in _api(f'/users/me/teams/{_team_id()}/channels/members')}
    me = _me()['id']
    dm_partner = {}
    for ch in chans:
        if ch.get('type') == 'D':
            other = [p for p in (ch.get('name') or '').split('__') if p != me]
            dm_partner[ch['id']] = other[0] if other else me
    names = _user_names(dm_partner.values()) if dm_partner else {}
    muted = config()['muted_channels']
    out = []
    for ch in chans:
        if ch.get('delete_at'):
            continue
        if ch.get('type') == 'D':
            name = f"Direkt: {names.get(dm_partner.get(ch['id']), '?')}"
        else:
            name = ch.get('display_name') or ch.get('name') or '?'
        if name.lower() in muted or (ch.get('name') or '').lower() in muted:
            continue
        m = members.get(ch['id']) or {}
        total = ch.get('total_msg_count_root', ch.get('total_msg_count', 0)) or 0
        seen = m.get('msg_count_root', m.get('msg_count', 0)) or 0
        out.append({'id': ch['id'], 'name': name, 'typ': ch.get('type'),
                    'ungelesen': max(0, total - seen), 'erwaehnungen': m.get('mention_count', 0) or 0,
                    'letzte_ms': ch.get('last_post_at') or 0})
    return sorted(out, key=lambda c: -c['letzte_ms'])


def _link(post_id):
    team = config()['team']
    return f'https://{team}.kchat.infomaniak.com/{team}/pl/{post_id}'


def _posts(data, chan_names=None):
    posts = [p for p in (data.get('posts') or {}).values()
             if not (p.get('type') or '').startswith('system_') and not p.get('delete_at')]
    names = _user_names([p.get('user_id') for p in posts])
    out = []
    for p in sorted(posts, key=lambda p: p.get('create_at', 0)):
        text = (p.get('message') or '').strip()
        files = (p.get('metadata') or {}).get('files') or []
        if files:
            text += ' ' + ' '.join(f"[Datei: {f.get('name')}]" for f in files[:5])
        out.append({
            'id': p['id'], 'kanal_id': p.get('channel_id'),
            'kanal': (chan_names or {}).get(p.get('channel_id'), ''),
            'von': names.get(p.get('user_id'), '?'), 'user_id': p.get('user_id'),
            'zeit': datetime.fromtimestamp(p.get('create_at', 0) / 1000).strftime('%d.%m. %H:%M'),
            'ms': p.get('create_at', 0), 'antwort_auf': p.get('root_id') or None,
            'text': text[:2000], 'link': _link(p['id']),
            # kChat-Prioritaet beim Schreiben: 'urgent' (Dringend) / 'important' (Wichtig)
            'prioritaet': (((p.get('metadata') or {}).get('priority') or {}).get('priority') or '').lower(),
        })
    return out


def kanaele():
    return [{**{k: c[k] for k in ('name', 'ungelesen', 'erwaehnungen')},
             'zuletzt': datetime.fromtimestamp(c['letzte_ms'] / 1000).strftime('%d.%m. %H:%M') if c['letzte_ms'] else '-'}
            for c in _channels()]


def neu_seit(since_ms, max_kanaele=25, pro_kanal=60):
    """Alle Nachrichten seit since_ms aus allen (nicht stummen) Kanaelen, aelteste zuerst."""
    chans = [c for c in _channels() if c['letzte_ms'] > since_ms][:max_kanaele]
    names = {c['id']: c['name'] for c in chans}
    out = []
    for c in chans:
        posts = _posts(_api(f"/channels/{c['id']}/posts", params={'since': int(since_ms)}), names)
        out += [p for p in posts if p['ms'] > since_ms][-pro_kanal:]
    return sorted(out, key=lambda p: p['ms']), chans


def ueberblick(stunden=24):
    """Fuer den Chat: Ungelesenes je Kanal + was in den letzten Stunden geschrieben wurde."""
    stunden = max(1, min(int(stunden or 24), 24 * 7))
    posts, _ = neu_seit((time.time() - stunden * 3600) * 1000, pro_kanal=40)
    chans = [c for c in kanaele() if c['ungelesen'] or c['erwaehnungen']]
    return {'ungelesen_je_kanal': chans,
            'nachrichten': [{k: p[k] for k in ('kanal', 'von', 'zeit', 'text')} for p in posts[-150:]],
            'zeitraum': f'letzte {stunden} Stunden'}


def kanal(name, anzahl=30):
    q = str(name or '').strip().lower()
    chans = _channels()
    hit = next((c for c in chans if c['name'].lower() == q), None) or \
        next((c for c in chans if q and q in c['name'].lower()), None)
    if not hit:
        return {'fehler': f'Kein Kanal „{name}“. Vorhanden: ' + ', '.join(c['name'] for c in chans[:30])}
    data = _api(f"/channels/{hit['id']}/posts", params={'per_page': max(1, min(int(anzahl or 30), 100))})
    posts = _posts(data, {hit['id']: hit['name']})
    return {'kanal': hit['name'], 'nachrichten': [{k: p[k] for k in ('von', 'zeit', 'text', 'link')} for p in posts]}


def suchen(begriff, anzahl=20):
    begriff = str(begriff or '').strip()[:100]
    if not begriff:
        return []
    data = _api(f'/teams/{_team_id()}/posts/search', json_body={'terms': begriff, 'is_or_search': False})
    names = {c['id']: c['name'] for c in _channels()}
    posts = _posts(data, names)
    return [{k: p[k] for k in ('kanal', 'von', 'zeit', 'text', 'link')} for p in posts[-max(1, min(int(anzahl or 20), 50)):]]


# ─── Agent: Erwaehnungen sofort, Lagebericht zu festen Zeiten ─────────

def _db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS notified (post_id TEXT PRIMARY KEY, created_at TEXT)')
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


def ist_faellig():
    letzte = _get_state('last_check')
    if not letzte:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(letzte) >= timedelta(minutes=config()['check_minutes'])
    except ValueError:
        return True


def faelliger_bericht(now=None):
    """Uhrzeit-Slot fuer den Lagebericht, falls jetzt einer dran ist (bis 2 Std. danach nachholen)."""
    now = now or datetime.now()
    cfg = config()
    if cfg['summary_weekdays_only'] and now.weekday() >= 5:
        return None
    erledigt = _get_state('last_summary_slot') or ''
    for t in reversed(cfg['summary_times']):
        h, m = map(int, t.split(':'))
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = slot.strftime('%Y-%m-%d %H:%M')
        if slot <= now < slot + timedelta(hours=2):
            return None if key <= erledigt else key
    return None


def melde_grund(post, cfg, me):
    if post['user_id'] == me['id']:
        return None
    if post.get('prioritaet') == 'urgent':
        return '🔴 als DRINGEND markiert'
    if post.get('prioritaet') == 'important':
        return '🟠 als WICHTIG markiert'
    if cfg['notify_mentions']:
        if post['kanal'].startswith('Direkt:'):
            return 'Direktnachricht'
        if re.search(r'@' + re.escape(me.get('username') or '\0') + r'\b', post['text'], re.I):
            return 'du wurdest erwähnt'
        if re.search(r'@(channel|all|here)\b', post['text']):
            return 'an alle'
    for k in cfg['keywords']:
        if k and re.search(r'(?<!\w)' + re.escape(k), post['text'].lower()):
            return f'Stichwort „{k}“'
    return None


def run_as_agent(bericht_slot=None, bericht_jetzt=False):
    """Neue Nachrichten pruefen. Erwaehnungen/Direktnachrichten sofort per Telegram; zum Bericht-Slot
    (oder bericht_jetzt) zusaetzlich den Lagebericht schicken."""
    if not (_load_token() or {}).get('token'):
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        if not agents_engine.start(AGENT_ID, 'Schaue in den Steler-Chat …', quiet=True):
            return
        cfg = config()
        c = _db()
        try:
            now_ms = int(time.time() * 1000)
            since = int(_get_state('last_check_ms') or now_ms - 3600 * 1000)
            since = max(since, now_ms - 3 * 24 * 3600 * 1000)  # nach langer Pause nicht alles nachmelden
            posts, _ = neu_seit(since)
            me = _me()
            gemeldet = 0
            for p in posts:
                grund = melde_grund(p, cfg, me)
                if not grund or c.execute('SELECT 1 FROM notified WHERE post_id=?', (p['id'],)).fetchone():
                    continue
                if _melden(p, grund):
                    gemeldet += 1
                c.execute('INSERT OR IGNORE INTO notified (post_id, created_at) VALUES (?, ?)',
                          (p['id'], datetime.now().isoformat(timespec='seconds')))
            unread = [x for x in kanaele() if x['ungelesen'] or x['erwaehnungen']]
            _set_state(c, 'unread_json', json.dumps(unread[:30], ensure_ascii=False))
            _set_state(c, 'last_check_ms', str(max([now_ms] + [p['ms'] for p in posts])))
            _set_state(c, 'last_check', datetime.now().isoformat(timespec='seconds'))
            c.execute('DELETE FROM notified WHERE created_at < ?', ((datetime.now() - timedelta(days=30)).isoformat(),))
            c.commit()

            bericht = ''
            if bericht_slot or bericht_jetzt:
                b_since = int(_get_state('last_summary_ms') or now_ms - 24 * 3600 * 1000)
                b_since = max(b_since, now_ms - 3 * 24 * 3600 * 1000)
                b_posts, _ = neu_seit(b_since, pro_kanal=80)
                if b_posts or bericht_jetzt:
                    _telegram(_lagebericht(b_posts, unread, cfg))
                    bericht = f' · Lagebericht geschickt ({len(b_posts)} Nachrichten)'
                else:
                    bericht = ' · nichts Neues für den Lagebericht'
                _set_state(c, 'last_summary_ms', str(now_ms))
                _set_state(c, 'last_summary', datetime.now().isoformat(timespec='seconds'))
                if bericht_slot:
                    _set_state(c, 'last_summary_slot', bericht_slot)
                c.commit()
        finally:
            c.close()
        n_unread = sum(x['ungelesen'] for x in unread)
        summary = (f'{len(posts)} neue Nachricht(en)' + (f', {gemeldet} gemeldet' if gemeldet else '')
                   + f' · {n_unread} ungelesen' + bericht)
        agents_engine.done(AGENT_ID, summary, quiet=not (gemeldet or bericht), bubble=summary)
    except NotConnected as e:
        agents_engine.done(AGENT_ID, str(e), status='error', bubble=str(e))
    except Exception as e:
        agents_engine.fail(AGENT_ID, str(e)[:200])
    finally:
        _lock.release()


def _lagebericht(posts, unread, cfg):
    kopf = f"{LABEL} – Lagebericht {datetime.now().strftime('%H:%M')}"
    offen = ''
    if unread:
        offen = '\n\nUngelesen: ' + ', '.join(
            f"{u['name']} ({u['ungelesen']}{', @' + str(u['erwaehnungen']) if u['erwaehnungen'] else ''})"
            for u in unread[:10])
    if not posts:
        return f'{kopf}\n\nSeit dem letzten Bericht nichts Neues.{offen}'
    if cfg['summary_ai']:
        try:
            return f'{kopf}\n\n{_ki_zusammenfassung(posts)}{offen}'
        except Exception as e:
            print(f'[kChat] KI-Zusammenfassung fehlgeschlagen: {e}')
    # ohne KI: je Kanal Anzahl + letzte Nachricht
    je = {}
    for p in posts:
        je.setdefault(p['kanal'], []).append(p)
    zeilen = [f"• {k} ({len(v)}): {v[-1]['von']}: {v[-1]['text'][:140]}" for k, v in je.items()]
    return f'{kopf}\n\n' + '\n'.join(zeilen[:15]) + offen


def _ki_zusammenfassung(posts):
    settings = settings_store.load_or_empty()
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    if not api_key:
        raise RuntimeError('kein API-Key')
    verlauf, laenge = [], 0
    for p in reversed(posts):  # neueste zuerst, bis das Budget voll ist
        zeile = f"[{p['kanal']}] {p['zeit']} {p['von']}: {p['text'][:600]}"
        laenge += len(zeile)
        if laenge > 24000:
            break
        verlauf.append(zeile)
    prompt = (
        f"{siggi_time.now_line()}\n\n"
        f"Das sind die neuen Nachrichten aus dem internen Firmen-Chat der Steler GmbH (Stefan Mutter, "
        f"{_me().get('username')}, liest mit):\n\n" + '\n'.join(reversed(verlauf)) + "\n\n"
        "Schreib Stefan einen kurzen Lagebericht auf Deutsch (max. 12 Zeilen, Telegram, kein Markdown, "
        "Aufzaehlung mit •): 1) Was ist gerade aktuell / was ist passiert, je Thema eine Zeile mit Kanal. "
        "2) Was ist offen oder wartet auf Stefan (Fragen an ihn, Entscheidungen, Termine) - mit ➡️ markieren. "
        "Nichts erfinden, Smalltalk weglassen, Namen behalten."
    )
    r = requests.post('https://api.anthropic.com/v1/messages',
                      headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 700,
                            'messages': [{'role': 'user', 'content': prompt}]}, timeout=60).json()
    text = ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text').strip()
    if not text:
        raise RuntimeError(str(r)[:200])
    return text


def _telegram(text, buttons=None):
    """Nur Telegram - nie Mail (Steler bleibt Steler, Chefblick-Postfach bleibt unberuehrt)."""
    try:
        import telegram_engine  # spaet: telegram_engine importiert agents_engine
        if not telegram_engine.status().get('paired'):
            return False
        return bool(telegram_engine.send(text, buttons=buttons))
    except Exception as e:
        print(f'[kChat] Telegram fehlgeschlagen: {e}')
        return False


def _melden(post, grund):
    wo = post['von'] if post['kanal'].startswith('Direkt:') else f"{post['kanal']} · {post['von']}"
    text = f"{LABEL} ({grund})\n{wo} · {post['zeit']}\n\n{post['text'][:600]}"
    return _telegram(text, buttons=[[{'text': '💬 In kChat öffnen', 'url': post['link']}]])
