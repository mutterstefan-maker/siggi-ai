"""Agenten-Zentrale: Siggi-Seite der n8n-Agenten.

Die Agenten selbst laufen als Workflows in n8n (Docker, n8n.stean.info). Sie melden
jeden Schritt per POST /api/agents/hook/<agent_id> an Siggi (Bearer-Token), Siggi
speichert den Zustand hier und zeigt ihn live in der Agenten-Zentrale an.

Hook-Nachrichten (JSON-Feld "type"):
  start    -> Lauf beginnt. Antwort {"run": bool, "config": {...}} - bei pausiertem
              Agenten bricht der Workflow damit selbst ab.
  status   -> {"status", "bubble", "progress", "plan", "next_run"} (alles optional)
  event    -> {"level": info|success|warn|error, "text"}
  finish   -> {"status", "bubble", "summary", "problems": [...], "notices": [...], "next_run"}
              - Probleme gehen zusaetzlich per Mail an Stefan (gedrosselt), "notices"
              (z.B. Entwarnung "wieder erreichbar") sofort und ungedrosselt.
  approval -> {"title", "body", "payload"} legt eine Freigabe an (wartet auf Stefan).
  metrics  -> liefert Server-Kennzahlen dieses Hosts (fuer den Server-Waechter, der
              im Container selbst nur seine eigene Sandbox sieht).
  report_data -> Kennzahlen der letzten 7 Tage fuer den Wochenbericht.
  send_mail -> {"subject", "body"} Mail an Stefan (Empfaenger fest - nie an Dritte).
  site_check -> {"url"} prueft eine Website (Status, Ladezeit, Tage bis SSL-Ablauf) -
              das SSL-Ablaufdatum kann n8n selbst nicht auslesen.
  "quiet": true bei start/finish schreibt Routine-Laeufe nicht in den Verlauf (Server-
  Waechter alle 10 Min.); Problem-Mails gehen pro Agent hoechstens alle 6 h raus.
"""
import hashlib
import json
import os
import secrets
import shutil
import smtplib
import socket
import ssl
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from email.message import EmailMessage

import requests

import settings_store
import usage_tracker  # zaehlt ab Import jeden Claude-Aufruf dieses Prozesses mit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'agents.db')
N8N_INTERNAL = os.environ.get('N8N_INTERNAL_URL', 'http://127.0.0.1:5678')
STALE_SECONDS = 15 * 60  # "arbeitet" ohne Lebenszeichen so lange -> haengt
MAIL_REPEAT_HOURS = 6     # gleiche Problem-Mail pro Agent hoechstens so oft

STATUSES = {'working', 'planning', 'waiting', 'ready', 'sleeping', 'error'}

# Bekannte Agenten in Anzeige-Reihenfolge. kind 'internal' = laeuft direkt in Siggi und meldet
# sich ueber start()/step()/done() unten; sonst ein n8n-Workflow mit Start-Webhook 'trigger'.
# 'view' = Dashboard-Ansicht, in der Freigaben dieses Agenten liegen.
AGENTS = {
    'mail': {
        'kind': 'internal', 'name': 'Mail-Agent', 'icon': 'mail', 'view': 'mail_drafts', 'home': 'inbox', 'runnable': True,
        'role': 'Ruft Mails ab, sortiert sie und beantwortet sie',
        'default_config': {}, 'next_run': 'alle 21 Minuten',
    },
    'instagram': {
        'kind': 'internal', 'name': 'Instagram-Agent', 'icon': 'camera', 'view': 'instagram', 'runnable': False,
        'role': 'Postet Bilder aus der Warteschlange zu deinen Zeiten (auch auf Facebook)',
        'default_config': {}, 'next_run': '–',
    },
    'reels': {
        'kind': 'internal', 'name': 'Reels-Agent', 'icon': 'film', 'view': 'instagram', 'runnable': False,
        'role': 'Baut Reels, legt sie dir zur Freigabe vor, postet sie als Story',
        'default_config': {}, 'next_run': '–',
    },
    'bild': {
        'kind': 'internal', 'name': 'Bild-Agent', 'icon': 'image', 'view': 'flyer_pipeline', 'runnable': True,
        'role': 'Entwirft jeden Morgen ein neues Bild für Instagram',
        'default_config': {}, 'next_run': 'täglich 09:00',
    },
    'linkedin': {
        'kind': 'internal', 'name': 'LinkedIn-Agent', 'icon': 'briefcase', 'view': 'linkedin_pipeline', 'runnable': True,
        'role': 'Schreibt jeden Morgen einen Entwurf und postet freigegebene Beiträge nach Zeitplan',
        'default_config': {}, 'next_run': 'täglich 08:00',
    },
    'comments': {
        'kind': 'internal', 'name': 'Kommentar-Agent', 'icon': 'chat', 'view': None, 'runnable': False,
        'role': 'Liest neue Instagram-Kommentare und schlägt Antworten vor',
        'default_config': {}, 'next_run': 'alle 5 Minuten',
    },
    'telegram': {
        'kind': 'internal', 'name': 'Telegram-Siggi', 'icon': 'send', 'view': None, 'runnable': False,
        'role': 'Nimmt deine Aufgaben per Telegram an – als Text oder Sprachnachricht',
        'default_config': {}, 'next_run': 'sofort bei jeder Nachricht',
    },
    'improve': {
        'kind': 'internal', 'name': 'Selbstverbesserung', 'icon': 'bulb', 'view': 'improvements', 'runnable': True,
        'role': 'Sucht Wissenslücken und Fehler in Siggi und schlägt Verbesserungen vor',
        'default_config': {}, 'next_run': 'täglich 05:30',
    },
    'health': {
        'kind': 'internal', 'name': 'Health-Check', 'icon': 'pulse', 'view': 'health_check', 'runnable': True,
        'role': 'Prüft Zugänge, Tokens, Mail-Konten und Dienste',
        'default_config': {}, 'next_run': 'alle 4 Stunden',
    },
    'watch': {
        'kind': 'n8n', 'view': None, 'runnable': True,
        'name': 'Website-Wächter',
        'role': 'Prüft alle 15 Min., ob deine Websites erreichbar sind – täglich auch SSL und Ladezeit',
        'icon': 'shield',
        'trigger': '/webhook/siggi-watch-run',
        'default_config': {'sites': ['https://chefblick.de', 'https://stean.info', 'https://www.fischmann-plattner.de']},
        'next_run': 'alle 15 Minuten',
    },
    'server': {
        'kind': 'n8n', 'view': None, 'runnable': True,
        'name': 'Server-Wächter',
        'role': 'Überwacht CPU, Arbeitsspeicher, Festplatte und Dienste',
        'icon': 'server',
        'trigger': '/webhook/siggi-server-run',
        'default_config': {'cpu_warn': 85, 'ram_warn': 90, 'disk_warn': 85},
        'next_run': 'alle 10 Minuten',
    },
    'report': {
        'kind': 'n8n', 'view': None, 'runnable': True,
        'name': 'Wochenbericht',
        'role': 'Fasst montags die Woche zusammen: Mails, Posts, Leads, Probleme',
        'icon': 'chart',
        'trigger': '/webhook/siggi-report-run',
        'default_config': {},
        'next_run': 'Mo 07:30 (wöchentlich)',
    },
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_table():
    c = _conn()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'sleeping',
            bubble TEXT DEFAULT '',
            progress REAL,
            plan TEXT DEFAULT '[]',
            next_run TEXT,
            paused INTEGER DEFAULT 0,
            config TEXT,
            last_summary TEXT,
            last_run_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            level TEXT,
            text TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            title TEXT,
            body TEXT,
            payload TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            decided_at TEXT
        );
    ''')
    cols = [r[1] for r in c.execute('PRAGMA table_info(agent_state)')]
    if 'gauges' not in cols:
        c.execute('ALTER TABLE agent_state ADD COLUMN gauges TEXT')
    for agent_id, meta in AGENTS.items():
        c.execute(
            'INSERT OR IGNORE INTO agent_state (agent_id, config, next_run, updated_at) VALUES (?, ?, ?, ?)',
            (agent_id, json.dumps(meta['default_config']), meta['next_run'], _now()))
    c.commit()
    c.close()


def _now():
    return datetime.now().isoformat(timespec='seconds')


# ─── Token (n8n -> Siggi) ────────────────────────────────────────────

def generate_token():
    """Neuer Zugangsschluessel fuer n8n; gespeichert wird nur der Hash."""
    token = secrets.token_urlsafe(32)
    settings_store.update(lambda s: s.__setitem__('agents_api_token_hash', hashlib.sha256(token.encode()).hexdigest()))
    return token


def verify_token(auth_header):
    if not auth_header or not auth_header.startswith('Bearer '):
        return False
    stored = settings_store.load_or_empty().get('agents_api_token_hash')
    if not stored:
        return False
    given = hashlib.sha256(auth_header[7:].strip().encode()).hexdigest()
    return secrets.compare_digest(given, stored)


# ─── Hook (von n8n) ──────────────────────────────────────────────────

def _log(c, agent_id, level, text):
    c.execute('INSERT INTO agent_events (agent_id, level, text, created_at) VALUES (?, ?, ?, ?)',
              (agent_id, level if level in ('info', 'success', 'warn', 'error') else 'info', str(text)[:500], _now()))
    # Verlauf begrenzen
    c.execute('DELETE FROM agent_events WHERE id NOT IN (SELECT id FROM agent_events ORDER BY id DESC LIMIT 2000)')


def handle_hook(agent_id, data):
    if agent_id not in AGENTS:
        return {'error': 'Unbekannter Agent'}, 404
    kind = data.get('type')
    c = _conn()
    try:
        row = c.execute('SELECT * FROM agent_state WHERE agent_id=?', (agent_id,)).fetchone()
        if kind == 'start':
            if row['paused']:
                return {'run': False, 'reason': 'pausiert'}, 200
            c.execute("UPDATE agent_state SET status='working', bubble=?, progress=0, last_run_at=?, updated_at=? WHERE agent_id=?",
                      (data.get('bubble') or 'Starte …', _now(), _now(), agent_id))
            if not data.get('quiet'):
                _log(c, agent_id, 'info', data.get('text') or 'Lauf gestartet')
            c.commit()
            return {'run': True, 'config': json.loads(row['config'] or '{}')}, 200

        if kind == 'status':
            fields, vals = [], []
            if data.get('status') in STATUSES:
                fields.append('status=?'); vals.append(data['status'])
            if 'bubble' in data:
                fields.append('bubble=?'); vals.append(str(data['bubble'])[:300])
            if 'progress' in data:
                p = data['progress']
                fields.append('progress=?'); vals.append(None if p is None else max(0.0, min(1.0, float(p))))
            if isinstance(data.get('plan'), list):
                fields.append('plan=?'); vals.append(json.dumps(_clean_plan(data['plan']), ensure_ascii=False))
            if isinstance(data.get('gauges'), list):
                fields.append('gauges=?'); vals.append(json.dumps(_clean_gauges(data['gauges']), ensure_ascii=False))
            if data.get('next_run'):
                fields.append('next_run=?'); vals.append(str(data['next_run'])[:80])
            fields.append('updated_at=?'); vals.append(_now())
            c.execute(f"UPDATE agent_state SET {', '.join(fields)} WHERE agent_id=?", (*vals, agent_id))
            c.commit()
            return {'ok': True}, 200

        if kind == 'event':
            _log(c, agent_id, data.get('level', 'info'), data.get('text', ''))
            c.commit()
            return {'ok': True}, 200

        if kind == 'finish':
            status = data.get('status') if data.get('status') in STATUSES else 'sleeping'
            problems = [str(p)[:300] for p in (data.get('problems') or [])][:50]
            summary = str(data.get('summary') or ('Fertig' if not problems else f'{len(problems)} Problem(e)'))[:300]
            c.execute("UPDATE agent_state SET status=?, bubble=?, progress=NULL, last_summary=?, updated_at=? WHERE agent_id=?",
                      (status, str(data.get('bubble') or summary)[:300], summary, _now(), agent_id))
            if isinstance(data.get('plan'), list):
                c.execute('UPDATE agent_state SET plan=? WHERE agent_id=?',
                          (json.dumps(_clean_plan(data['plan']), ensure_ascii=False), agent_id))
            if isinstance(data.get('gauges'), list):
                c.execute('UPDATE agent_state SET gauges=? WHERE agent_id=?',
                          (json.dumps(_clean_gauges(data['gauges']), ensure_ascii=False), agent_id))
            if data.get('next_run'):
                c.execute('UPDATE agent_state SET next_run=? WHERE agent_id=?', (str(data['next_run'])[:80], agent_id))
            notices = [str(n)[:300] for n in (data.get('notices') or [])][:20]
            for n in notices:
                _log(c, agent_id, 'success', n)
            had_problems = bool(row['last_summary']) and row['last_summary'].startswith('Problem')
            if problems or not data.get('quiet') or had_problems:
                for p in problems:
                    _log(c, agent_id, 'warn', p)
                _log(c, agent_id, 'warn' if problems else 'success', summary)
            if problems:
                c.execute('UPDATE agent_state SET last_summary=? WHERE agent_id=?', ('Problem: ' + summary, agent_id))
            c.commit()
            if problems and data.get('notify', True) and _mail_due(c, agent_id, problems):
                _send_mail(f"Siggi-Agent {AGENTS[agent_id]['name']}: {len(problems)} Problem(e)",
                           'Der Agent hat folgende Probleme gefunden:\n\n' + '\n'.join(f'- {p}' for p in problems)
                           + '\n\nDetails im Dashboard unter "Agenten".')
                _log(c, agent_id, 'info', 'Problem-Mail an Stefan gesendet')
                c.commit()
            if notices and data.get('notify', True):
                _send_mail(f"Siggi-Agent {AGENTS[agent_id]['name']}: Entwarnung",
                           '\n'.join(f'- {n}' for n in notices) + '\n\nDetails im Dashboard unter "Agenten".')
            return {'ok': True}, 200

        if kind == 'approval':
            c.execute('INSERT INTO agent_approvals (agent_id, title, body, payload, created_at) VALUES (?, ?, ?, ?, ?)',
                      (agent_id, str(data.get('title', ''))[:200], str(data.get('body', ''))[:5000],
                       json.dumps(data.get('payload') or {}, ensure_ascii=False), _now()))
            aid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("UPDATE agent_state SET status='waiting', updated_at=? WHERE agent_id=?", (_now(), agent_id))
            c.commit()
            return {'ok': True, 'approval_id': aid}, 200

        if kind == 'metrics':
            return {'metrics': host_metrics()}, 200

        if kind == 'site_check':
            return check_site(str(data.get('url', ''))), 200

        if kind == 'report_data':
            return {'stats': weekly_stats()}, 200

        if kind == 'send_mail':
            _send_mail(str(data.get('subject', 'Siggi'))[:200], str(data.get('body', ''))[:20000])
            _log(c, agent_id, 'success', f"Mail an Stefan: {str(data.get('subject', ''))[:120]}")
            c.commit()
            return {'ok': True}, 200

        return {'error': f'Unbekannter type: {kind}'}, 400
    finally:
        c.close()


def _mail_due(c, agent_id, problems):
    """Gleiche Probleme nicht bei jedem Lauf erneut mailen (Server-Waechter laeuft alle 10 Min.)."""
    key = 'mail:' + hashlib.sha1('|'.join(sorted(p.split(':')[0] for p in problems)).encode()).hexdigest()[:12]
    since = (datetime.now() - timedelta(hours=MAIL_REPEAT_HOURS)).isoformat(timespec='seconds')
    if c.execute("SELECT 1 FROM agent_events WHERE agent_id=? AND level='mailkey' AND text=? AND created_at>=?",
                 (agent_id, key, since)).fetchone():
        return False
    c.execute("INSERT INTO agent_events (agent_id, level, text, created_at) VALUES (?, 'mailkey', ?, ?)", (agent_id, key, _now()))
    c.commit()
    return True


def weekly_stats(days=7):
    """Zahlen fuer den Wochenbericht. Jede Zahl einzeln abgesichert - fehlt eine Tabelle,
    steht dort None statt dass der ganze Bericht scheitert."""
    # Nur das Datum vergleichen: die Tabellen speichern teils '2026-10-03T06:00', teils '2026-10-03 06:00'
    since = since_iso = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    def q(db, sql, args=()):
        try:
            conn = sqlite3.connect(f'file:{os.path.join(BASE_DIR, db)}?mode=ro', uri=True, timeout=10)
            try:
                return conn.execute(sql, args).fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return None

    stats = {
        'zeitraum_tage': days,
        'mails_eingang': q('mails.db', 'SELECT COUNT(*) FROM mails WHERE created_at >= ?', (since,)),
        'mails_auto_beantwortet': q('mails.db', 'SELECT COUNT(*) FROM mails WHERE created_at >= ? AND sent=1', (since,)),
        'mails_gesendet': q('mails.db', 'SELECT COUNT(*) FROM sent_mails WHERE sent_at >= ?', (since,)),
        'mails_spam': q('mails.db', 'SELECT COUNT(*) FROM mails WHERE created_at >= ? AND is_spam=1', (since,)),
        'mail_entwuerfe_offen': q('mails.db', "SELECT COUNT(*) FROM mail_drafts WHERE status='pending'"),
        'neue_kontakte': q('mails.db', 'SELECT COUNT(*) FROM contacts WHERE first_contact >= ?', (since,)),
        'audits': q('mails.db', 'SELECT COUNT(*) FROM audit_history WHERE created_at >= ?', (since,)),
        'instagram_posts': q('instagram.db', "SELECT COUNT(*) FROM ig_posts WHERE status='posted' AND posted_at >= ?", (since_iso,)),
        'instagram_fehler': q('instagram.db', "SELECT COUNT(*) FROM ig_posts WHERE status='error' AND posted_at >= ?", (since_iso,)),
        'stories_gepostet': q('reels.db', "SELECT COUNT(*) FROM reels_posts WHERE status='posted' AND posted_at >= ?", (since_iso,)),
        'linkedin_entwuerfe': q('mails.db', 'SELECT COUNT(*) FROM linkedin_drafts WHERE created_at >= ?', (since,)),
        'linkedin_gepostet': q('mails.db', "SELECT COUNT(*) FROM linkedin_drafts WHERE status IN ('approved_posted','auto_posted') AND COALESCE(posted_at, decided_at) >= ?", (since,)),
        'linkedin_warteschlange': q('mails.db', "SELECT COUNT(*) FROM linkedin_drafts WHERE status='approved'"),
        'linkedin_offen': q('mails.db', "SELECT COUNT(*) FROM linkedin_drafts WHERE status='pending'"),
        'bilder_erzeugt': q('mails.db', "SELECT COUNT(*) FROM flyer_history WHERE created_at >= ? AND status != 'failed'", (since,)),
        'bilder_fehlgeschlagen': q('mails.db', "SELECT COUNT(*) FROM flyer_history WHERE created_at >= ? AND status = 'failed'", (since,)),
    }
    try:
        c = _conn()
        stats['agenten_probleme'] = [dict(r) for r in c.execute(
            "SELECT agent_id, text, created_at FROM agent_events WHERE level IN ('warn','error') AND created_at >= ? ORDER BY id DESC LIMIT 15",
            (since_iso,))]
        for r in stats['agenten_probleme']:
            r['agent'] = AGENTS.get(r.pop('agent_id'), {}).get('name', '?')
        c.close()
    except Exception:
        stats['agenten_probleme'] = []
    try:
        stats['server'] = host_metrics()
    except Exception:
        stats['server'] = None
    return stats


def check_site(url):
    """Status, Ladezeit und Tage bis zum SSL-Ablauf einer Website."""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    result = {'url': url, 'ok': False, 'status': None, 'ms': None, 'ssl_days': None, 'error': None}
    try:
        t = time.time()
        r = requests.get(url, timeout=20, allow_redirects=True, headers={'User-Agent': 'Siggi-Website-Waechter/1.0'})
        result['ms'] = int((time.time() - t) * 1000)
        result['status'] = r.status_code
        result['ok'] = r.status_code < 400
        final = urlparse(r.url)
    except Exception as e:
        result['error'] = str(e)[:200]
        final = urlparse(url)
    if final.scheme == 'https' and final.hostname:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((final.hostname, final.port or 443), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=final.hostname) as tls:
                    not_after = tls.getpeercert()['notAfter']
            expires = datetime.strptime(not_after, '%b %d %H:%M:%S %Y %Z')
            result['ssl_days'] = (expires - datetime.utcnow()).days
        except Exception as e:
            result['ssl_error'] = str(e)[:200]
    return result


def _clean_gauges(gauges):
    """Messwerte fuer die Ampel-Anzeige: Wert und Warngrenze in Prozent."""
    out = []
    for g in gauges[:8]:
        if not isinstance(g, dict):
            continue
        try:
            value, warn = float(g.get('value', 0)), float(g.get('warn', 100))
        except (TypeError, ValueError):
            continue
        out.append({'label': str(g.get('label', ''))[:30], 'value': round(value, 1), 'warn': warn,
                    'detail': str(g.get('detail', ''))[:60]})
    return out


def _clean_plan(plan):
    out = []
    for p in plan[:20]:
        if isinstance(p, dict):
            state = p.get('state') if p.get('state') in ('done', 'active', 'wait', 'warn', 'open', 'error') else 'open'
            out.append({'label': str(p.get('label', ''))[:150], 'state': state, 'note': str(p.get('note', ''))[:60]})
    return out


# ─── Melde-API fuer Agenten, die direkt in Siggi laufen ──────────────
# Jeder Aufruf ist abgesichert: ein Fehler beim Melden darf nie die eigentliche
# Arbeit (Posten, Mailen ...) abbrechen.

def _emit(agent_id, data):
    try:
        body, _ = handle_hook(agent_id, data)
        return body
    except Exception as e:
        print(f'[Agenten] Meldung {agent_id}/{data.get("type")} fehlgeschlagen: {e}')
        return {}


def is_paused(agent_id):
    try:
        c = _conn()
        row = c.execute('SELECT paused FROM agent_state WHERE agent_id=?', (agent_id,)).fetchone()
        c.close()
        return bool(row and row['paused'])
    except Exception:
        return False


def start(agent_id, bubble, plan=None, quiet=False, text=None):
    """Lauf beginnt. False, wenn der Agent pausiert ist - dann nichts tun."""
    body = _emit(agent_id, {'type': 'start', 'bubble': bubble, 'quiet': quiet, 'text': text})
    if body.get('run') is False:
        return False
    if plan:
        step(agent_id, bubble, plan=plan)
    return True


def step(agent_id, bubble, plan=None, progress=None):
    data = {'type': 'status', 'status': 'working', 'bubble': bubble}
    if plan is not None:
        data['plan'] = plan
    if progress is not None:
        data['progress'] = progress
    _emit(agent_id, data)


def event(agent_id, text, level='info'):
    _emit(agent_id, {'type': 'event', 'level': level, 'text': text})


def done(agent_id, summary, problems=None, plan=None, quiet=False, notify=False, bubble=None, status='sleeping'):
    data = {'type': 'finish', 'status': status, 'summary': summary, 'problems': problems or [],
            'quiet': quiet, 'notify': notify, 'bubble': bubble or summary}
    if plan is not None:
        data['plan'] = plan
    _emit(agent_id, data)


def fail(agent_id, error, plan=None):
    """Lauf abgebrochen: Figur zeigt 'Problem', Fehler steht im Verlauf."""
    data = {'type': 'finish', 'status': 'error', 'summary': f'Fehler: {error}'[:300], 'problems': [],
            'notify': False, 'bubble': f'Fehler: {error}'[:300]}
    if plan is not None:
        data['plan'] = plan
    _emit(agent_id, data)
    event(agent_id, f'Fehler: {error}', 'error')


def request_approval(agent_id, title, body, payload):
    return _emit(agent_id, {'type': 'approval', 'title': title, 'body': body, 'payload': payload}).get('approval_id')


# Aktionen nach einer Freigabe in der Zentrale: agent_id -> Funktion(payload). app.py registriert sie.
APPROVAL_ACTIONS = {}


# ─── Server-Kennzahlen (fuer den Server-Waechter) ────────────────────

def _cpu_percent(interval=1.0):
    def snap():
        with open('/proc/stat') as f:
            vals = [int(v) for v in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)
    i1, t1 = snap()
    time.sleep(interval)
    i2, t2 = snap()
    return round(100.0 * (1 - (i2 - i1) / max(1, t2 - t1)), 1)


def host_metrics():
    mem = {}
    with open('/proc/meminfo') as f:
        for line in f:
            k, v = line.split(':', 1)
            mem[k] = int(v.split()[0]) * 1024
    disk = shutil.disk_usage('/')
    load1, load5, load15 = os.getloadavg()
    with open('/proc/uptime') as f:
        uptime = float(f.read().split()[0])
    services = {}
    for unit in ('stean.service', 'stean-mail-loop.service', 'nginx.service', 'docker.service'):
        try:
            r = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=5)
            services[unit] = r.stdout.strip()
        except Exception as e:
            services[unit] = f'unbekannt ({e})'
    total, avail = mem.get('MemTotal', 1), mem.get('MemAvailable', 0)
    return {
        'cpu_percent': _cpu_percent(),
        'cpu_count': os.cpu_count(),
        'load': [round(load1, 2), round(load5, 2), round(load15, 2)],
        'ram_percent': round(100.0 * (total - avail) / total, 1),
        'ram_total_gb': round(total / 1024 ** 3, 1),
        'ram_used_gb': round((total - avail) / 1024 ** 3, 1),
        'swap_used_gb': round((mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)) / 1024 ** 3, 2),
        'disk_percent': round(100.0 * disk.used / disk.total, 1),
        'disk_free_gb': round(disk.free / 1024 ** 3, 1),
        'disk_total_gb': round(disk.total / 1024 ** 3, 1),
        'uptime_days': round(uptime / 86400, 1),
        'services': services,
    }


# ─── Dashboard (Session-Login) ───────────────────────────────────────

def overview(extras=None):
    """extras: {agent_id: {'pending': n, 'next_run': str, 'idle': str}} - von app.py berechnet,
    weil die Zaehler in den jeweiligen Engines liegen."""
    extras = extras or {}
    c = _conn()
    try:
        agents = []
        for agent_id, meta in AGENTS.items():
            r = c.execute('SELECT * FROM agent_state WHERE agent_id=?', (agent_id,)).fetchone()
            status = 'paused' if r['paused'] else r['status']
            bubble = r['bubble']
            if status == 'working' and r['updated_at']:
                age = (datetime.now() - datetime.fromisoformat(r['updated_at'])).total_seconds()
                if age > STALE_SECONDS:
                    status, bubble = 'error', f'Keine Meldung seit {int(age // 60)} Minuten – hängt der Lauf?'
            pending = c.execute("SELECT COUNT(*) FROM agent_approvals WHERE agent_id=? AND status='pending'", (agent_id,)).fetchone()[0]
            ex = extras.get(agent_id) or {}
            pending += int(ex.get('pending') or 0)
            if pending and status in ('sleeping', 'ready'):
                status = 'waiting'
                bubble = f'{pending} warte{"t" if pending == 1 else "n"} auf deine Freigabe'
            elif ex.get('alert') and status in ('sleeping', 'ready'):
                bubble = ex['alert']  # z.B. LinkedIn-Warteschlange fast leer - wichtiger als die letzte Meldung
            elif status == 'sleeping' and ex.get('idle') and not bubble:
                bubble = ex['idle']
            agents.append({
                'id': agent_id, 'name': meta['name'], 'role': meta['role'], 'icon': meta['icon'],
                'status': status, 'bubble': bubble or '', 'progress': r['progress'],
                'plan': json.loads(r['plan'] or '[]'), 'next_run': ex.get('next_run') or r['next_run'] or meta['next_run'],
                'kind': meta.get('kind', 'n8n'), 'view': meta.get('view'), 'runnable': meta.get('runnable', True),
                # Klick auf die Karte: dorthin, wo die Aufgabe liegt (Freigaben, sonst der Arbeitsbereich)
                'target': meta.get('view') if pending else meta.get('home', meta.get('view')),
                'info': ex.get('info'),
                'gauges': json.loads(r['gauges'] or '[]'),
                'paused': bool(r['paused']), 'config': json.loads(r['config'] or '{}'),
                'last_summary': r['last_summary'], 'last_run_at': r['last_run_at'], 'updated_at': r['updated_at'],
                'pending_approvals': pending,
            })
        feed = [dict(e) for e in c.execute(
            "SELECT agent_id, level, text, created_at FROM agent_events WHERE level != 'mailkey' ORDER BY id DESC LIMIT 40")]
        for e in feed:
            e['agent_name'] = AGENTS.get(e['agent_id'], {}).get('name', e['agent_id'])
        approvals = [dict(a) for a in c.execute(
            "SELECT id, agent_id, title, body, created_at FROM agent_approvals WHERE status='pending' ORDER BY id")]
        try:
            costs = usage_tracker.summary()
            per_agent = {s['id']: s['amount'] for s in costs['by_source']}
            for a in agents:
                a['cost_month'] = per_agent.get(a['id'])
        except Exception as e:
            print(f'[Verbrauch] Auswertung fehlgeschlagen: {e}')
            costs = None
        return {'agents': agents, 'feed': feed, 'approvals': approvals, 'n8n_up': is_n8n_up(), 'costs': costs,
                'n8n_url': 'https://n8n.stean.info', 'server_time': _now()}
    finally:
        c.close()


def set_paused(agent_id, paused):
    if agent_id not in AGENTS:
        return False
    c = _conn()
    c.execute('UPDATE agent_state SET paused=?, updated_at=? WHERE agent_id=?', (1 if paused else 0, _now(), agent_id))
    _log(c, agent_id, 'info', 'Von Stefan pausiert' if paused else 'Von Stefan fortgesetzt')
    c.commit()
    c.close()
    return True


def set_config(agent_id, config):
    if agent_id not in AGENTS or not isinstance(config, dict):
        return False
    if agent_id == 'watch':
        sites = []
        for s in config.get('sites', []):
            s = str(s).strip()
            if not s:
                continue
            if not s.startswith(('http://', 'https://')):
                s = 'https://' + s
            if s not in sites:
                sites.append(s[:200])
        config = {'sites': sites[:50]}
    elif agent_id == 'server':
        config = {k: max(10, min(99, int(config.get(k, d)))) for k, d in AGENTS['server']['default_config'].items()}
    c = _conn()
    c.execute('UPDATE agent_state SET config=?, updated_at=? WHERE agent_id=?', (json.dumps(config), _now(), agent_id))
    c.commit()
    c.close()
    return True


def run_now(agent_id):
    """Startet den n8n-Workflow sofort (Webhook nur intern erreichbar, siehe nginx)."""
    meta = AGENTS.get(agent_id)
    if not meta or meta.get('kind') == 'internal':
        return {'success': False, 'error': 'Unbekannter Agent'}
    try:
        r = requests.post(N8N_INTERNAL + meta['trigger'], json={'source': 'siggi'}, timeout=10)
        if r.status_code >= 400:
            return {'success': False, 'error': f'n8n antwortet mit {r.status_code}: {r.text[:200]}'}
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': f'n8n nicht erreichbar: {e}'}


def decide_approval(approval_id, approve):
    c = _conn()
    row = c.execute("SELECT * FROM agent_approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
    if not row:
        c.close()
        return False
    if approve and row['agent_id'] in APPROVAL_ACTIONS:
        try:
            APPROVAL_ACTIONS[row['agent_id']](json.loads(row['payload'] or '{}'))
        except Exception as e:
            c.close()
            event(row['agent_id'], f"Freigabe konnte nicht ausgeführt werden: {e}", 'error')
            raise
    c.execute('UPDATE agent_approvals SET status=?, decided_at=? WHERE id=?',
              ('approved' if approve else 'rejected', _now(), approval_id))
    _log(c, row['agent_id'], 'success' if approve else 'info',
         f"Freigabe {'erteilt' if approve else 'abgelehnt'}: {row['title']}")
    left = c.execute("SELECT COUNT(*) FROM agent_approvals WHERE agent_id=? AND status='pending'", (row['agent_id'],)).fetchone()[0]
    if not left:
        c.execute("UPDATE agent_state SET status=CASE WHEN status='waiting' THEN 'ready' ELSE status END WHERE agent_id=?",
                  (row['agent_id'],))
    c.commit()
    c.close()
    return True


def is_n8n_up():
    try:
        return requests.get(N8N_INTERNAL + '/healthz', timeout=5).status_code == 200
    except Exception:
        return False


def _send_mail(subject, body):
    host, user, pw = os.environ.get('SMTP_HOST'), os.environ.get('SMTP_USER'), os.environ.get('SMTP_PASSWORD')
    if not (host and user and pw):
        print('[Agenten] Mail nicht gesendet: SMTP-Zugangsdaten fehlen')
        return
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = os.environ.get('MAIL_USER_2') or user
    try:
        with smtplib.SMTP(host, int(os.environ.get('SMTP_PORT', 587)), timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
    except Exception as e:
        print('[Agenten] Mail-Fehler:', e)
