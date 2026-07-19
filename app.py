from flask import Flask, jsonify, request, send_from_directory, send_file, g
from flask_cors import CORS
import os
import json
import subprocess
import time
import io
import requests
import sqlite3
import threading
import uuid
import re
import base64
from datetime import datetime, timedelta
import audit_engine as audit_eng
import audit_pdf

app = Flask(__name__, static_folder='/opt/stean', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB, genug für Base64-kodierte Flyer-Bilder
CORS(app, origins=['https://stean.info', 'https://www.stean.info'])

from flask import session, redirect
from dotenv import load_dotenv
import secrets as _secrets
from auth import authenticate_user

load_dotenv('/opt/stean/config/.env')
app.secret_key = os.environ.get('FLASK_SECRET') or _secrets.token_hex(32)

@app.route('/login')
def login_page():
    return send_from_directory('/opt/stean', 'login.html')

_login_attempts = {}
_login_attempts_lock = threading.Lock()
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300

def _login_rate_limited(ip):
    now = time.time()
    with _login_attempts_lock:
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_attempts[ip] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS

def _record_login_failure(ip):
    with _login_attempts_lock:
        _login_attempts.setdefault(ip, []).append(time.time())

@app.route('/api/login', methods=['POST'])
def api_login():
    ip = request.remote_addr
    if _login_rate_limited(ip):
        return jsonify({'error': 'too many attempts, try again later'}), 429
    data = request.json or {}
    username = data.get('username', '')
    password = data.get('password', '')
    if authenticate_user(username, password):
        session.clear()
        session['logged_in'] = True
        session['user'] = username
        session.permanent = True
        return jsonify({'success': True})
    _record_login_failure(ip)
    return jsonify({'error': 'invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

SECURITY_STATUS_PATH = '/opt/stean/security_status.json'

@app.route('/api/security/status')
def security_status():
    try:
        with open(SECURITY_STATUS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {'checked_at': None, 'overall': 'unknown', 'checks': []}

    now = time.time()
    with _login_attempts_lock:
        blocked_ips = [
            ip for ip, attempts in _login_attempts.items()
            if len([t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]) >= LOGIN_MAX_ATTEMPTS
        ]
        recent_failures = sum(
            len([t for t in attempts if now - t < LOGIN_WINDOW_SECONDS])
            for attempts in _login_attempts.values()
        )
    data['live_login_blocked_ips'] = len(blocked_ips)
    data['live_login_recent_failures'] = recent_failures
    return jsonify(data)

@app.before_request
def require_login():
    allowed = ('/login', '/api/login')
    if request.path in allowed:
        return None
    if request.path.startswith('/api/instagram/media/'):
        return None  # muss öffentlich erreichbar sein, damit Meta das Bild abrufen kann
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'unauthorized'}), 401
        return redirect('/login')

SETTINGS_PATH = '/opt/stean/settings.json'
DB_PATH = '/opt/stean/mails.db'

# Import Engines (falls vorhanden)
try:
    from calendar_engine import (
        get_calendar_context, get_upcoming_events, create_event as calendar_create_event,
        is_slot_allowed, find_free_slots, get_scheduling_rules_text
    )
    CALENDAR_AVAILABLE = True
except:
    CALENDAR_AVAILABLE = False

try:
    import internet_engine
    INTERNET_AVAILABLE = True
except:
    INTERNET_AVAILABLE = False

try:
    from solar_engine import get_device_info
    SOLAR_AVAILABLE = True
except:
    SOLAR_AVAILABLE = False

try:
    from ga4_engine import get_overview
    GA4_AVAILABLE = True
except:
    GA4_AVAILABLE = False

try:
    import gsc_engine
    GSC_AVAILABLE = True
except:
    GSC_AVAILABLE = False

try:
    import linkedin_engine
    LINKEDIN_AVAILABLE = True
except:
    LINKEDIN_AVAILABLE = False

try:
    import instagram_engine
    INSTAGRAM_AVAILABLE = True
except:
    INSTAGRAM_AVAILABLE = False

try:
    from flask_socketio import SocketIO
    import desktop_engine
    socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')
    desktop_engine.init(socketio)
    DESKTOP_AVAILABLE = True
except Exception as _desktop_import_err:
    socketio = None
    DESKTOP_AVAILABLE = False
    print(f'[Desktop] Nicht verfügbar: {_desktop_import_err}')

import memory_engine
memory_engine.init_memory_db()

SIGGI_SEND_ACCOUNT = 'team@chefblick.de'  # Siggi verschickt eigenständig geschriebene Mails immer von diesem Postfach
MAIL_TRUST_THRESHOLD_DEFAULT = 15  # so viele Freigaben/Korrekturen/Ablehnungen bis der Autopilot scharf geschaltet wird

def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except:
        return {}

def save_settings(settings):
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(settings, f, indent=2)

def init_audit_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS audit_history (
        id TEXT PRIMARY KEY,
        url TEXT,
        status TEXT,
        progress INTEGER DEFAULT 0,
        result TEXT,
        pdf_path TEXT,
        created_at TEXT
    )''')
    try:
        c.execute('ALTER TABLE audit_history ADD COLUMN pdf_path_customer TEXT')
    except sqlite3.OperationalError:
        pass  # Spalte existiert schon
    conn.commit()
    conn.close()

init_audit_table()

def init_mail_drafts_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS mail_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        to_addr TEXT,
        subject TEXT,
        body TEXT,
        account TEXT,
        status TEXT DEFAULT 'pending',
        edited INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    conn.commit()
    conn.close()

init_mail_drafts_table()

def init_contacts_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT NOT NULL,
        phone TEXT,
        company TEXT,
        notes TEXT,
        last_contact DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_contacts_table()

def init_actions_log_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS siggi_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool TEXT,
        input TEXT,
        output TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_actions_log_table()

def log_siggi_action(tool, tool_input, output):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'INSERT INTO siggi_actions (tool, input, output) VALUES (?, ?, ?)',
            (tool, json.dumps(tool_input, ensure_ascii=False)[:2000], str(output)[:2000])
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[ActionLog] Fehler: {e}')

def get_mail_trust_status():
    settings = load_settings()
    threshold = settings.get('mail_trust_threshold', MAIL_TRUST_THRESHOLD_DEFAULT)
    clean = settings.get('mail_trust_approved_clean', 0)
    edited = settings.get('mail_trust_approved_edited', 0)
    rejected = settings.get('mail_trust_rejected', 0)
    total = clean + edited + rejected
    enabled = settings.get('mail_auto_send_enabled', False)
    quality_rate = round((clean / total) * 100) if total else 0
    return {
        'count': clean,
        'threshold': threshold,
        'auto_send_enabled': enabled,
        'approved_clean': clean,
        'approved_edited': edited,
        'rejected': rejected,
        'quality_rate': quality_rate
    }

def register_mail_decision(kind):
    """kind: 'approved_clean' | 'approved_edited' | 'rejected'.
    Nur unbearbeitet ('approved_clean') versendete Mails zählen auf den Autopilot-Schwellenwert -
    bearbeitete oder abgelehnte Entwürfe zeigen, dass SIGGI noch nicht zuverlässig genug ist."""
    settings = load_settings()
    threshold = settings.get('mail_trust_threshold', MAIL_TRUST_THRESHOLD_DEFAULT)
    key = f'mail_trust_{kind}'
    settings[key] = settings.get(key, 0) + 1
    clean_count = settings.get('mail_trust_approved_clean', 0)
    if clean_count >= threshold:
        settings['mail_auto_send_enabled'] = True
    save_settings(settings)
    return settings.get('mail_auto_send_enabled', False)

def create_mail_draft(to_addr, subject, body):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO mail_drafts (to_addr, subject, body, account, status) VALUES (?, ?, ?, ?, ?)',
        (to_addr, subject, body, SIGGI_SEND_ACCOUNT, 'pending')
    )
    draft_id = c.lastrowid
    conn.commit()
    conn.close()
    return draft_id

def send_new_mail(to_addr, subject, body):
    """Verschickt eine neue E-Mail immer über SIGGI_SEND_ACCOUNT."""
    settings = load_settings()
    accounts = settings.get('accounts', {})
    if SIGGI_SEND_ACCOUNT not in accounts:
        return f'Postfach {SIGGI_SEND_ACCOUNT} ist nicht konfiguriert.'
    account = accounts[SIGGI_SEND_ACCOUNT]

    import smtplib
    from email.message import EmailMessage as _EmailMessage
    msg = _EmailMessage()
    msg['From'] = SIGGI_SEND_ACCOUNT
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.set_content(body)

    with smtplib.SMTP(account.get('smtp_server', 'smtp.ionos.de'), account.get('smtp_port', 587)) as server:
        server.starttls()
        server.login(SIGGI_SEND_ACCOUNT, account.get('password', ''))
        server.send_message(msg)

    return f"Mail an {to_addr} von {SIGGI_SEND_ACCOUNT} verschickt."

SIGGI_TOOLS = [
    {
        'name': 'merke_dir',
        'description': 'Speichert eine Information dauerhaft in deinem Gedächtnis, damit du sie in Zukunft immer kennst (z.B. Fakten, Vorlieben, laufende Projekte, Regeln).',
        'input_schema': {
            'type': 'object',
            'properties': {
                'inhalt': {'type': 'string', 'description': 'Was du dir merken sollst.'},
                'kategorie': {'type': 'string', 'description': 'Optionale Kategorie, z.B. "projekt", "regel", "fakt".'}
            },
            'required': ['inhalt']
        }
    },
    {
        'name': 'vergiss',
        'description': 'Löscht einen bestehenden Gedächtnis-Eintrag anhand seiner ID.',
        'input_schema': {
            'type': 'object',
            'properties': {'id': {'type': 'integer', 'description': 'ID des Eintrags aus deinem Gedächtnis.'}},
            'required': ['id']
        }
    },
    {
        'name': 'setze_erinnerung',
        'description': 'Setzt eine Erinnerung, die zu einer bestimmten Zeit als Windows-Benachrichtigung ausgelöst wird.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'nachricht': {'type': 'string', 'description': 'Woran erinnert werden soll.'},
                'wann': {'type': 'string', 'description': 'Natürlichsprachliche Zeitangabe auf Deutsch, z.B. "in 30 minuten", "morgen um 08:00", "heute abend".'}
            },
            'required': ['nachricht', 'wann']
        }
    },
    {
        'name': 'todo_hinzufuegen',
        'description': 'Fügt der Todo-Liste einen neuen Eintrag hinzu.',
        'input_schema': {
            'type': 'object',
            'properties': {'text': {'type': 'string'}},
            'required': ['text']
        }
    },
    {
        'name': 'kontakt_suchen',
        'description': (
            'Durchsucht die hinterlegten Kontakte (Name, E-Mail, Firma, Telefon) nach einem Suchbegriff. '
            'Immer zuerst aufrufen, wenn Stefan einen Empfänger nur mit Namen nennt (z.B. "Herr Müller", "die Firma Schmidt") '
            'und du dessen E-Mail-Adresse noch nicht kennst - rate niemals eine E-Mail-Adresse.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {'query': {'type': 'string', 'description': 'Name, Firma oder Teil der E-Mail-Adresse, nach der gesucht wird.'}},
            'required': ['query']
        }
    },
    {
        'name': 'sende_mail',
        'description': (
            f'Erstellt eine neue E-Mail, die immer von {SIGGI_SEND_ACCOUNT} verschickt wird. '
            'Solange der Autopilot noch nicht freigeschaltet ist, wird die Mail NICHT direkt verschickt, '
            'sondern als Entwurf gespeichert und wartet auf manuelle Freigabe von Stefan im Dashboard. '
            'Nur aufrufen, wenn Stefan im Chat ausdrücklich sagt, dass eine Mail verschickt werden soll '
            '(z.B. "schreib X eine Mail dass..."). Wenn nur ein Name genannt wird, ERST kontakt_suchen aufrufen '
            'um die E-Mail-Adresse zu finden. Bei fehlenden Angaben (Empfänger, Inhalt) nachfragen statt zu raten.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'empfaenger': {'type': 'string', 'description': 'E-Mail-Adresse des Empfängers.'},
                'betreff': {'type': 'string', 'description': 'Betreff der Mail.'},
                'text': {'type': 'string', 'description': 'Inhalt der Mail.'}
            },
            'required': ['empfaenger', 'betreff', 'text']
        }
    },
    {
        'name': 'websuche',
        'description': (
            'Sucht im Internet nach aktuellen Informationen (Google-Suche), Nachrichten zu einem Thema oder dem aktuellen Wetter. '
            'Nutzen, wenn Stefan nach etwas fragt, das du nicht aus deinem Wissen/Gedächtnis beantworten kannst - '
            'z.B. aktuelle Ereignisse, Wetter, Fakten über Dritte, Preise, etc.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'art': {'type': 'string', 'enum': ['suche', 'news', 'wetter'], 'description': '"suche" für allgemeine Google-Suche, "news" für aktuelle Nachrichten zu einem Thema, "wetter" für Wettervorhersage.'},
                'query': {'type': 'string', 'description': 'Suchbegriff, Nachrichten-Thema oder Ort (bei Wetter).'}
            },
            'required': ['art', 'query']
        }
    },
    {
        'name': 'freie_termine_vorschlagen',
        'description': (
            'Findet freie Zeitfenster im Kalender der nächsten Tage, unter Beachtung der Terminierungs-Regeln '
            '(Arbeitszeit, geblockte Zeiten, Freitags-Regel) und bereits belegter Termine. '
            'Immer aufrufen BEVOR du einen Termin mit jemandem ausmachst, wenn Stefan keine feste Uhrzeit vorgibt '
            '(z.B. "mach mal einen Termin mit X aus") - schlage dann 2-3 der gefundenen Zeiten vor statt zu raten.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'dauer_minuten': {'type': 'integer', 'description': 'Gewünschte Termindauer in Minuten, Standard 60.'},
                'tage_voraus': {'type': 'integer', 'description': 'Wie viele Tage im Voraus gesucht werden soll, Standard 7.'},
                'notfall': {'type': 'boolean', 'description': 'true wenn es ein dringender Ausnahmefall ist (erlaubt dann auch Freitag).'}
            },
            'required': []
        }
    },
    {
        'name': 'termin_anlegen',
        'description': (
            'Legt einen neuen Termin in Stefans Google Kalender an. Die Zeit wird automatisch gegen die '
            'Terminierungs-Regeln geprüft (nie vor 8 Uhr, nicht 11-14 Uhr, freitags nur im Notfall) - '
            'bei einem Verstoß wird NICHTS angelegt, sondern der Grund zurückgegeben. Private Termine (privat=true) '
            'sind von diesen Regeln ausgenommen und dürfen jederzeit angelegt werden. Nutze bei unklarer Zeit '
            'zuerst freie_termine_vorschlagen.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'titel': {'type': 'string', 'description': 'Titel des Termins.'},
                'wann': {'type': 'string', 'description': 'Natürlichsprachliche Zeitangabe auf Deutsch, z.B. "morgen um 14:00", "in 2 stunden", "heute abend".'},
                'dauer_minuten': {'type': 'integer', 'description': 'Dauer in Minuten, Standard 60.'},
                'beschreibung': {'type': 'string', 'description': 'Optionale Beschreibung/Notiz zum Termin.'},
                'notfall': {'type': 'boolean', 'description': 'true wenn es ein dringender Ausnahmefall ist (erlaubt dann auch Freitag).'},
                'privat': {'type': 'boolean', 'description': 'true wenn es ein privater Termin ist - dann gelten die Arbeitszeit-Regeln nicht, private Termine dürfen jederzeit angelegt werden.'}
            },
            'required': ['titel', 'wann']
        }
    },
    {
        'name': 'oeffne_im_browser',
        'description': (
            'Öffnet eine Webseite in einem neuen Browser-Tab bei Stefan (z.B. ein Suchergebnis, eine Kunden-Website, '
            'ein Dokument). Nur mit einer vollständigen, echten URL aufrufen (https://...), die entweder von Stefan '
            'genannt wurde oder aus einer vorherigen Websuche stammt - niemals eine erfundene URL.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'url': {'type': 'string', 'description': 'Vollständige URL, die geöffnet werden soll.'},
                'titel': {'type': 'string', 'description': 'Kurzer Titel/Grund, warum die Seite geöffnet wird.'}
            },
            'required': ['url']
        }
    },
    {
        'name': 'linkedin_posten',
        'description': (
            'Veröffentlicht einen Text-Beitrag auf Stefans LinkedIn-Profil (nicht als offizielle Unternehmensseite, '
            'sondern als Person Stefan Mutter - technische Einschränkung von LinkedIn). '
            'Nur aufrufen wenn Stefan ausdrücklich sagt, dass etwas auf LinkedIn gepostet werden soll. '
            'Zeig ihm den Text vorher nicht zwingend, aber fasse kurz zusammen was du gepostet hast.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {'text': {'type': 'string', 'description': 'Der vollständige Beitragstext.'}},
            'required': ['text']
        }
    },
    {
        'name': 'linkedin_kommentare_lesen',
        'description': 'Liest die Kommentare zum zuletzt von dir auf LinkedIn geposteten Beitrag.',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []}
    },
    {
        'name': 'linkedin_kommentieren',
        'description': 'Antwortet als Kommentar auf den zuletzt von dir auf LinkedIn geposteten Beitrag.',
        'input_schema': {
            'type': 'object',
            'properties': {'text': {'type': 'string', 'description': 'Der Kommentartext.'}},
            'required': ['text']
        }
    },
    {
        'name': 'desktop_agent_action',
        'description': (
            'Führt eine Aktion auf Stefans lokalem Windows-Desktop über den verbundenen Desktop-Agenten aus. '
            'Unkritische Aktionen (screenshot, read_file, list_dir) werden sofort ausgeführt. '
            'Kritische Aktionen (write_file, open_app, office_write, click, type_text, close_app) werden NICHT sofort '
            'ausgeführt, sondern erzeugen eine Bestätigungskarte im Chat - frag Stefan vorher IMMER kurz im Klartext, '
            'ob er die geplante Aktion wirklich so ausführen möchte, bevor du dieses Tool mit einer kritischen Aktion aufrufst. '
            'Wenn kein Desktop-Agent verbunden ist, informiere Stefan darüber statt es erneut zu versuchen.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['screenshot', 'read_file', 'list_dir', 'write_file', 'open_app',
                              'office_write', 'click', 'type_text', 'close_app'],
                    'description': 'Welche Aktion ausgeführt werden soll.'
                },
                'params': {
                    'type': 'object',
                    'description': (
                        'Parameter je nach Aktion, z.B. {"path": "..."} für read_file/list_dir, '
                        '{"path": "...", "content": "..."} für write_file, {"name": "winword"} für open_app, '
                        '{"app": "Word", "text": "...", "save_path": "..."} für office_write, '
                        '{"x": 100, "y": 200} für click, {"text": "..."} für type_text.'
                    )
                }
            },
            'required': ['action']
        }
    }
]

def run_siggi_tool(name, tool_input):
    """Führt ein von SIGGI aufgerufenes Tool aus, protokolliert es und gibt das Ergebnis als String zurück."""
    output = _run_siggi_tool_inner(name, tool_input)
    log_siggi_action(name, tool_input, output)
    return output

def _run_siggi_tool_inner(name, tool_input):
    try:
        if name == 'merke_dir':
            memory_engine.save_memory(tool_input['inhalt'], tool_input.get('kategorie', 'general'))
            return f"Gemerkt: {tool_input['inhalt']}"

        if name == 'vergiss':
            memory_engine.delete_memory(tool_input['id'])
            return f"Eintrag {tool_input['id']} gelöscht."

        if name == 'setze_erinnerung':
            remind_at = memory_engine.parse_reminder_time(tool_input['wann'])
            if not remind_at:
                return f"Konnte die Zeitangabe '{tool_input['wann']}' nicht verstehen."
            memory_engine.save_reminder(tool_input['nachricht'], remind_at.isoformat())
            return f"Erinnerung gesetzt: {tool_input['nachricht']} um {remind_at.strftime('%d.%m.%Y %H:%M')}"

        if name == 'todo_hinzufuegen':
            settings = load_settings()
            settings.setdefault('daily_todos', []).append(tool_input['text'])
            save_settings(settings)
            return f"Todo hinzugefügt: {tool_input['text']}"

        if name == 'kontakt_suchen':
            q = f"%{tool_input['query']}%"
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                'SELECT name, email, phone, company, notes FROM contacts '
                'WHERE name LIKE ? OR email LIKE ? OR company LIKE ? LIMIT 5',
                (q, q, q)
            )
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            if not rows:
                return f"Kein Kontakt gefunden für '{tool_input['query']}'."
            return json.dumps(rows, ensure_ascii=False)

        if name == 'sende_mail':
            trust = get_mail_trust_status()
            if trust['auto_send_enabled']:
                return send_new_mail(tool_input['empfaenger'], tool_input['betreff'], tool_input['text'])
            draft_id = create_mail_draft(tool_input['empfaenger'], tool_input['betreff'], tool_input['text'])
            return (
                f"Entwurf #{draft_id} an {tool_input['empfaenger']} erstellt und wartet auf deine Freigabe im Dashboard "
                f"(noch {max(trust['threshold'] - trust['count'], 0)} Freigaben bis der Autopilot scharf geschaltet wird)."
            )

        if name == 'websuche':
            if not INTERNET_AVAILABLE:
                return 'Internetzugriff ist auf diesem Server nicht verfügbar.'
            art = tool_input.get('art', 'suche')
            query = tool_input['query']
            if art == 'wetter':
                return json.dumps(internet_engine.get_weather(query or 'Hagen'), ensure_ascii=False)
            if art == 'news':
                return json.dumps(internet_engine.get_news(query), ensure_ascii=False)
            return json.dumps(internet_engine.google_search(query), ensure_ascii=False)

        if name == 'freie_termine_vorschlagen':
            if not CALENDAR_AVAILABLE:
                return 'Google Kalender ist nicht verfügbar (kein Token/Credentials hinterlegt).'
            slots = find_free_slots(
                duration_minutes=tool_input.get('dauer_minuten', 60),
                days_ahead=tool_input.get('tage_voraus', 7),
                is_emergency=tool_input.get('notfall', False)
            )
            if not slots:
                return 'Keine freien Zeitfenster gefunden, die den Terminierungs-Regeln entsprechen.'
            return '; '.join(s.strftime('%A %d.%m. %H:%M') for s, _ in slots)

        if name == 'termin_anlegen':
            if not CALENDAR_AVAILABLE:
                return 'Google Kalender ist nicht verfügbar (kein Token/Credentials hinterlegt).'
            start_dt = memory_engine.parse_reminder_time(tool_input['wann'])
            if not start_dt:
                return f"Konnte die Zeitangabe '{tool_input['wann']}' nicht verstehen."

            is_emergency = tool_input.get('notfall', False)
            is_private = tool_input.get('privat', False)
            allowed, reason = is_slot_allowed(start_dt, is_emergency, is_private)
            if not allowed:
                return f"Termin NICHT angelegt: {start_dt.strftime('%d.%m.%Y %H:%M')} Uhr verstößt gegen die Terminierungs-Regeln ({reason})."

            duration = tool_input.get('dauer_minuten', 60)
            end_dt = start_dt + timedelta(minutes=duration)
            created = calendar_create_event(
                tool_input['titel'], start_dt, end_dt, tool_input.get('beschreibung', '')
            )
            if not created:
                return 'Termin konnte nicht angelegt werden (Kalender-Verbindungsfehler).'
            return f"Termin '{tool_input['titel']}' angelegt für {start_dt.strftime('%d.%m.%Y %H:%M')} Uhr."

        if name == 'oeffne_im_browser':
            url = tool_input.get('url', '')
            if not url.startswith(('http://', 'https://')):
                return 'Ungültige URL - muss mit http:// oder https:// beginnen.'
            pending = getattr(g, 'pending_actions', None)
            if pending is not None:
                pending.append({'type': 'open_url', 'url': url, 'title': tool_input.get('titel', url)})
            return f"Öffne {url} in einem neuen Tab bei Stefan."

        if name == 'linkedin_posten':
            if not LINKEDIN_AVAILABLE:
                return 'LinkedIn-Integration ist auf diesem Server nicht verfügbar.'
            return linkedin_engine.post_share(tool_input['text'])

        if name == 'linkedin_kommentare_lesen':
            if not LINKEDIN_AVAILABLE:
                return 'LinkedIn-Integration ist auf diesem Server nicht verfügbar.'
            return linkedin_engine.get_comments()

        if name == 'linkedin_kommentieren':
            if not LINKEDIN_AVAILABLE:
                return 'LinkedIn-Integration ist auf diesem Server nicht verfügbar.'
            return linkedin_engine.reply_to_comment(tool_input['text'])

        if name == 'desktop_agent_action':
            if not DESKTOP_AVAILABLE:
                return 'Desktop-Agent-Integration ist auf diesem Server nicht verfügbar.'
            action = tool_input.get('action')
            params = tool_input.get('params', {}) or {}
            if not desktop_engine.is_agent_connected():
                return 'Kein Desktop-Agent verbunden. Stefan muss den lokalen Agent auf seinem PC starten und koppeln (Einstellungen > Desktop-Zugriff).'
            if action in desktop_engine.RISKY_ACTIONS:
                action_id = desktop_engine.create_pending_action(action, params)
                desc = desktop_engine.describe_action(action, params)
                return (
                    f"BESTAETIGUNG_ERFORDERLICH id={action_id}: {desc}. "
                    "Diese Aktion wird erst ausgeführt, nachdem Stefan sie über die Bestätigungskarte im Chat freigegeben hat."
                )
            result = desktop_engine.execute_action(action, params)
            if result.get('ok'):
                return f"Aktion '{action}' ausgeführt: {result.get('data', 'OK')}"
            return f"Fehler bei Aktion '{action}': {result.get('error', 'unbekannt')}"

        return f"Unbekanntes Tool: {name}"
    except Exception as e:
        return f"Fehler beim Ausführen von {name}: {e}"

def get_mail_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        stats = {}
        for cat in ['inbox', 'callbacks', 'invoices', 'knowledge_gap']:
            c.execute(f'SELECT COUNT(*) FROM mails WHERE category=? AND read=0 AND deleted=0', (cat,))
            count = c.fetchone()[0]
            stats[cat] = count
        c.execute('SELECT COUNT(*) FROM mails WHERE deleted=0')
        stats['total'] = c.fetchone()[0]
        conn.close()
        return stats
    except:
        return {'inbox': 0, 'callbacks': 0, 'invoices': 0, 'knowledge_gap': 0, 'total': 0}

def save_chat_memory(user_msg, siggi_reply):
    """Speichert Chat in Datenbank"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'INSERT INTO chat_history (user_message, siggi_response) VALUES (?, ?)',
            (user_msg, siggi_reply)
        )
        conn.commit()
        conn.close()
    except:
        pass

def get_chat_context(limit=5):
    """Holt letzte N Gespräche als Kontext"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'SELECT user_message, siggi_response FROM chat_history ORDER BY id DESC LIMIT ?',
            (limit,)
        )
        chats = c.fetchall()
        conn.close()
        
        if not chats:
            return ""
        
        context = "\nRÜCKBLICK AUF LETZTE GESPRÄCHE:\n"
        for msg, reply in reversed(chats):
            context += f"Stefan: {msg[:100]}...\n"
            context += f"SIGGI: {reply[:100]}...\n"
        return context
    except:
        return ""

def get_siggi_memories():
    """Holt wichtige gelernte Infos"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT key, value FROM siggi_memory ORDER BY importance DESC LIMIT 5')
        memories = c.fetchall()
        conn.close()
        
        if not memories:
            return ""
        
        context = "\nWAS SIGGI ÜBER STEFAN WEISS:\n"
        for key, value in memories:
            context += f"- {key}: {value}\n"
        return context
    except:
        return ""

# ─── Routes ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    resp = send_from_directory('/opt/stean', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

def _strip_markdown_for_tts(text):
    """Entfernt Markdown-Formatierung, damit edge-tts nicht 'Stern Stern' etc. vorliest."""
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)  # Codeblöcke
    text = re.sub(r'`([^`]*)`', r'\1', text)                # Inline-Code
    text = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', text)      # fett+kursiv
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)          # fett
    text = re.sub(r'\*([^*]+)\*', r'\1', text)               # kursiv
    text = re.sub(r'__([^_]+)__', r'\1', text)               # fett (Unterstrich)
    text = re.sub(r'_([^_]+)_', r'\1', text)                 # kursiv (Unterstrich)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # Überschriften
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)   # Listenpunkte
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)     # Links
    text = re.sub(r'[#*_~`]', '', text)                       # Rest-Sonderzeichen
    return text.strip()


@app.route('/api/voice/speak', methods=['POST'])
def voice_speak():
    try:
        data = request.json or {}
        text = _strip_markdown_for_tts(data.get('text', 'Hallo'))
        settings = load_settings()
        voice = settings.get('tts_voice', 'de-DE-ConradNeural')
        pitch = settings.get('tts_pitch', '+0Hz')
        rate = settings.get('tts_rate', '+0%')
        output_file = f'/tmp/tts_{int(time.time())}.mp3'
        cmd = ['edge-tts', '--voice', voice, '--pitch', pitch, '--rate', rate,
               '--text', text, '--write-media', output_file]
        result = subprocess.run(cmd, shell=False, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(output_file):
            with open(output_file, 'rb') as f:
                audio_data = f.read()
            os.remove(output_file)
            return send_file(
                io.BytesIO(audio_data),
                mimetype='audio/mpeg',
                as_attachment=False
            )
        else:
            return jsonify({'error': result.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/jarvis/chat', methods=['POST'])
def jarvis_chat():
    data = request.json or {}
    message = data.get('message', '').strip()
    settings = load_settings()
    g.pending_actions = []

    api_key = settings.get('anthropic_api_key', '')
    if not api_key or not message:
        return jsonify({'reply': 'Hallo Stefan! Was kann ich für dich tun?'})

    try:
        mail_stats = get_mail_stats()

        # Build system prompt with all available data
        system_prompt = settings.get('ai_character', 'Du bist SIGGI')
        system_prompt += "\n\nSTATUS:\n"
        system_prompt += f"- Ungelesene Mails: {mail_stats.get('inbox', 0)}\n"
        system_prompt += f"- Callbacks: {mail_stats.get('callbacks', 0)}\n"
        system_prompt += f"- Total: {mail_stats.get('total', 0)}\n"

        # Add Calendar if available
        if CALENDAR_AVAILABLE:
            try:
                cal_context = get_calendar_context()
                system_prompt += f"\n{cal_context}\n"
                system_prompt += f"\n{get_scheduling_rules_text()}\n"
            except:
                pass

        # Add Chat History & Memories
        system_prompt += get_chat_context(3)
        system_prompt += get_siggi_memories()
        system_prompt += "\n" + memory_engine.get_memory_context()
        system_prompt += "\n" + memory_engine.get_upcoming_reminders()
        if GSC_AVAILABLE:
            try:
                system_prompt += "\n" + gsc_engine.get_gsc_context()
            except:
                pass
        if GA4_AVAILABLE:
            try:
                from ga4_engine import get_ga4_context
                system_prompt += "\n" + get_ga4_context()
            except:
                pass
        system_prompt += (
            "\nANWEISUNG: Keine Signatur! Antworte direkt. "
            "Nutze die verfügbaren Tools proaktiv, wenn Stefan dir etwas zum Merken, Vergessen, "
            "Erinnern, als Todo oder als zu versendende Mail sagt - frag nicht erst nach, sondern handle direkt."
        )

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01'
        }
        messages = [{'role': 'user', 'content': message}]

        # Tool-Use-Loop: SIGGI darf mehrfach Tools aufrufen bevor er final antwortet
        reply = ''
        for _ in range(5):
            response = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers=headers,
                json={
                    'model': 'claude-opus-4-1-20250805',
                    'max_tokens': 1500,
                    'system': system_prompt,
                    'tools': SIGGI_TOOLS,
                    'messages': messages
                },
                timeout=15
            )

            if response.status_code != 200:
                return jsonify({'reply': 'Interessante Frage!'})

            result = response.json()
            content_blocks = result.get('content', [])
            messages.append({'role': 'assistant', 'content': content_blocks})

            if result.get('stop_reason') == 'tool_use':
                tool_results = []
                for block in content_blocks:
                    if block.get('type') == 'tool_use':
                        output = run_siggi_tool(block['name'], block.get('input', {}))
                        tool_results.append({
                            'type': 'tool_result',
                            'tool_use_id': block['id'],
                            'content': output
                        })
                messages.append({'role': 'user', 'content': tool_results})
                continue

            reply = ''.join(b.get('text', '') for b in content_blocks if b.get('type') == 'text').strip()
            break

        if not reply:
            reply = 'Erledigt!'

        # Entferne ALLE Varianten der Signatur
        lines_to_remove = [
            'Diese Nachricht wurde von SIGGI (KI) verfasst',
            'Stefan meldet sich persönlich wenn nötig',
            '*Diese Nachricht',
            'KI) verfasst'
        ]

        for line_part in lines_to_remove:
            reply = reply.replace(line_part, '')

        reply = reply.strip()
        # Entferne leere Absätze
        reply = '\n'.join([l for l in reply.split('\n') if l.strip()])

        # Speichere in Gedächtnis
        save_chat_memory(message, reply)

        return jsonify({'reply': reply, 'actions': g.pending_actions})
    except Exception as e:
        return jsonify({'reply': f'Fehler: {str(e)[:30]}'})


@app.route('/api/actions-log')
def actions_log():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    limit = request.args.get('limit', 50, type=int)
    c.execute('SELECT * FROM siggi_actions ORDER BY id DESC LIMIT ?', (limit,))
    actions = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'actions': actions})

@app.route('/api/mail-drafts')
def list_mail_drafts():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM mail_drafts WHERE status='pending' ORDER BY created_at DESC")
    drafts = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'drafts': drafts, 'trust': get_mail_trust_status()})


@app.route('/api/mail-drafts/<int:draft_id>/edit', methods=['POST'])
def edit_mail_draft(draft_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM mail_drafts WHERE id=?', (draft_id,))
    row = c.fetchone()
    if not row or row['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404
    draft = dict(row)

    data = request.json or {}
    to_addr = data.get('to_addr', draft['to_addr'])
    subject = data.get('subject', draft['subject'])
    body = data.get('body', draft['body'])

    changed = (to_addr != draft['to_addr']) or (subject != draft['subject']) or (body != draft['body'])
    edited_flag = 1 if (changed or draft['edited']) else 0

    c.execute(
        'UPDATE mail_drafts SET to_addr=?, subject=?, body=?, edited=? WHERE id=?',
        (to_addr, subject, body, edited_flag, draft_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'changed': changed})


@app.route('/api/mail-drafts/<int:draft_id>/approve', methods=['POST'])
def approve_mail_draft(draft_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM mail_drafts WHERE id=?', (draft_id,))
    row = c.fetchone()
    if not row or row['status'] != 'pending':
        conn.close()
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404
    draft = dict(row)

    try:
        result = send_new_mail(draft['to_addr'], draft['subject'], draft['body'])
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

    c.execute("UPDATE mail_drafts SET status='sent', decided_at=datetime('now') WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()

    kind = 'approved_edited' if draft['edited'] else 'approved_clean'
    auto_send_enabled = register_mail_decision(kind)
    return jsonify({'success': True, 'message': result, 'trust': get_mail_trust_status(), 'auto_send_enabled': auto_send_enabled})


@app.route('/api/mail-drafts/<int:draft_id>/reject', methods=['POST'])
def reject_mail_draft(draft_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status FROM mail_drafts WHERE id=?", (draft_id,))
    row = c.fetchone()
    if not row or row[0] != 'pending':
        conn.close()
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404

    c.execute("UPDATE mail_drafts SET status='rejected', decided_at=datetime('now') WHERE id=?", (draft_id,))
    conn.commit()
    conn.close()
    register_mail_decision('rejected')
    return jsonify({'success': True, 'trust': get_mail_trust_status()})

@app.route('/api/calendar/today')
def calendar_today():
    if not CALENDAR_AVAILABLE:
        return jsonify({'error': 'Calendar not available'}), 503
    try:
        from calendar_engine import get_todays_events
        events = get_todays_events()
        return jsonify({'events': events or []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/calendar/upcoming')
def calendar_upcoming():
    if not CALENDAR_AVAILABLE:
        return jsonify({'error': 'Calendar not available'}), 503
    try:
        from calendar_engine import get_upcoming_events
        days = request.args.get('days', 3, type=int)
        events = get_upcoming_events(days)
        return jsonify({'events': events or []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/solar/status')
def solar_status():
    if not SOLAR_AVAILABLE:
        return jsonify({'error': 'Solar not available'}), 503
    try:
        from solar_engine import get_device_info
        info = get_device_info()
        return jsonify(info or {})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics/overview')
def analytics_overview():
    if not GA4_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503
    try:
        from ga4_engine import get_overview
        days = request.args.get('days', 28, type=int)
        data = get_overview(days)
        return jsonify(data or {})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/gsc/overview')
def gsc_overview():
    if not GSC_AVAILABLE:
        return jsonify({'error': 'Search Console not available'}), 503
    try:
        days = request.args.get('days', 28, type=int)
        overview = gsc_engine.get_overview(days)
        keywords = gsc_engine.get_top_keywords(days, 10)
        return jsonify({'overview': overview or {}, 'keywords': keywords})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings')
def get_settings():
    settings = load_settings()
    return jsonify({k: v for k, v in settings.items() if k not in ['anthropic_api_key']})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    try:
        data = request.get_json() or {}
        settings = load_settings()
        for key in data:
            if key != 'anthropic_api_key':
                settings[key] = data[key]
        save_settings(settings)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/mails/<category>')
def get_mails(category):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        if category == 'trash':
            c.execute('SELECT * FROM mails WHERE deleted=1 ORDER BY date DESC LIMIT 50')
        elif category == 'sent':
            c.execute('SELECT * FROM mails WHERE sent=1 AND deleted=0 ORDER BY date DESC LIMIT 50')
        else:
            c.execute('SELECT * FROM mails WHERE category=? AND deleted=0 ORDER BY date DESC LIMIT 50', (category,))
        
        cols = [description[0] for description in c.description]
        mails = [dict(zip(cols, row)) for row in c.fetchall()]
        conn.close()
        
        return jsonify(mails)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/mail/<mail_id>')
def get_mail(mail_id):
    return jsonify({})

@app.route('/api/mail/<mail_id>/delete', methods=['POST'])
def delete_mail(mail_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE mails SET deleted=1 WHERE id=?', (mail_id,))
        conn.commit()
        affected = c.rowcount
        conn.close()
        return jsonify({'success': True, 'affected': affected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sent/<mail_id>/delete', methods=['POST'])
def delete_sent(mail_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE mails SET deleted=1 WHERE id=?', (mail_id,))
        conn.commit()
        affected = c.rowcount
        conn.close()
        return jsonify({'success': True, 'affected': affected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/mail/<mail_id>/move', methods=['POST'])
def move_mail(mail_id):
    try:
        data = request.json or {}
        category = data.get('category')
        if not category:
            return jsonify({'error': 'category required'}), 400
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE mails SET category=?, deleted=0 WHERE id=?', (category, mail_id))
        conn.commit()
        affected = c.rowcount
        conn.close()
        return jsonify({'success': True, 'affected': affected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/mail/<mail_id>/purge', methods=['POST'])
def purge_mail(mail_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM mails WHERE id=?', (mail_id,))
        conn.commit()
        affected = c.rowcount
        conn.close()
        return jsonify({'success': True, 'affected': affected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/spam_sender', methods=['POST'])
def add_spam_sender():
    try:
        data = request.json or {}
        sender = (data.get('sender') or '').strip().lower()
        if not sender:
            return jsonify({'error': 'sender required'}), 400
        settings = load_settings()
        spam_senders = settings.get('spam_senders', [])
        if sender not in spam_senders:
            spam_senders.append(sender)
        settings['spam_senders'] = spam_senders
        save_settings(settings)
        return jsonify({'success': True, 'spam_senders': spam_senders})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/counts')
def get_counts():
    return jsonify(get_mail_stats())

def _run_audit_background(audit_id, url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    def progress_cb(pct):
        try:
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute('UPDATE audit_history SET progress=? WHERE id=?', (pct, audit_id))
            conn2.commit()
            conn2.close()
        except Exception:
            pass

    try:
        settings = load_settings()
        pagespeed_key = settings.get('pagespeed_api_key', '') or None
        anthropic_key = settings.get('anthropic_api_key', '') or None
        result = audit_eng.run_full_audit(url, progress_cb=progress_cb, pagespeed_api_key=pagespeed_key,
                                           anthropic_api_key=anthropic_key)

        pdf_path = None
        pdf_path_customer = None
        if not result.get('error'):
            domain_slug = re.sub(r'[^a-zA-Z0-9]+', '_', url.replace('https://', '').replace('http://', '')).strip('_')
            pdf_path = os.path.join(audit_eng.AUDIT_RESULTS_PATH, f"audit_{domain_slug}_{audit_id}.pdf")
            audit_pdf.generate_audit_pdf(result, audit_eng.LOGO_PATH, audit_eng.CONTACT, pdf_path,
                                          customer_version=False)
            pdf_path_customer = os.path.join(audit_eng.AUDIT_RESULTS_PATH, f"audit_{domain_slug}_{audit_id}_kunde.pdf")
            audit_pdf.generate_audit_pdf(result, audit_eng.LOGO_PATH, audit_eng.CONTACT, pdf_path_customer,
                                          customer_version=True)

        status = 'error' if result.get('error') else 'done'
        c.execute('UPDATE audit_history SET status=?, progress=100, result=?, pdf_path=?, pdf_path_customer=? WHERE id=?',
                  (status, json.dumps(result, ensure_ascii=False), pdf_path, pdf_path_customer, audit_id))
        conn.commit()

        if status == 'done':
            final_url = result.get('url', url)
            t2 = threading.Thread(target=_fetch_pagespeed_background,
                                   args=(audit_id, final_url, pagespeed_key), daemon=True)
            t2.start()
    except Exception as e:
        c.execute('UPDATE audit_history SET status=?, progress=100, result=? WHERE id=?',
                  ('error', json.dumps({'error': str(e)}), audit_id))
        conn.commit()
    finally:
        conn.close()


def _fetch_pagespeed_background(audit_id, url, pagespeed_key):
    """Holt PageSpeed-Daten separat nach, ohne den Audit zu blockieren (kann 30-90s dauern)."""
    try:
        pagespeed_full = audit_eng.audit_ai.fetch_pagespeed_both(url, pagespeed_key)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT result FROM audit_history WHERE id=?', (audit_id,))
        row = c.fetchone()
        if row and row[0]:
            result = json.loads(row[0])
            result['pagespeed'] = pagespeed_full or {'success': False, 'mobile': {}, 'desktop': {}}
            c.execute('UPDATE audit_history SET result=? WHERE id=?',
                      (json.dumps(result, ensure_ascii=False), audit_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f'[PageSpeed-Hintergrund] Fehler: {e}')


@app.route('/api/audit/start', methods=['POST'])
def start_audit():
    try:
        data = request.json or {}
        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({'error': 'url required'}), 400

        audit_id = uuid.uuid4().hex[:12]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT INTO audit_history (id, url, status, progress, created_at) VALUES (?,?,?,?,?)',
                  (audit_id, url, 'running', 0, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        t = threading.Thread(target=_run_audit_background, args=(audit_id, url), daemon=True)
        t.start()

        return jsonify({'success': True, 'audit_id': audit_id, 'job_id': audit_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/status/<audit_id>')
def audit_status(audit_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT status, progress, result, pdf_path, url FROM audit_history WHERE id=?', (audit_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'not found'}), 404
        status, progress, result_json, pdf_path, url = row
        result = json.loads(result_json) if result_json else {}
        resp = {
            'status': status, 'progress': progress, 'url': url,
            'ai_result': result.get('ai_result'),
            'html_analysis': result.get('html_analysis'),
            'pagespeed': result.get('pagespeed'),
            'summary': result.get('summary'),
            'pdf_filename': os.path.basename(pdf_path) if pdf_path else None,
        }
        if status == 'error':
            resp['error'] = result.get('error', 'Unbekannter Fehler')
        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/history')
def audit_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, url, status, pdf_path, created_at FROM audit_history WHERE pdf_path IS NOT NULL ORDER BY created_at DESC LIMIT 50')
        rows = c.fetchall()
        conn.close()
        return jsonify([{
            'id': r[0], 'url': r[1], 'status': r[2],
            'filename': os.path.basename(r[3]) if r[3] else None,
            'created_at': r[4]
        } for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/download/<audit_id>')
@app.route('/api/audit/pdf/<audit_id>')
def audit_pdf_download(audit_id):
    try:
        want_customer = request.args.get('version') == 'kunde'
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT pdf_path, pdf_path_customer, url FROM audit_history WHERE id=?', (audit_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'PDF nicht gefunden'}), 404
        pdf_path, pdf_path_customer, url = row
        if want_customer:
            pdf_path = pdf_path_customer or pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF nicht gefunden'}), 404
        domain = url.replace('https://', '').replace('http://', '').split('/')[0]
        suffix = '_Kundenversion' if want_customer else ''
        dl_name = f"Website-Analyse_{domain}{suffix}.pdf"
        return send_from_directory(os.path.dirname(pdf_path), os.path.basename(pdf_path),
                                    as_attachment=True, download_name=dl_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/<audit_id>/delete', methods=['POST'])
def audit_delete(audit_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT pdf_path, pdf_path_customer FROM audit_history WHERE id=?', (audit_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Nicht gefunden'}), 404
        for p in row:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        c.execute('DELETE FROM audit_history WHERE id=?', (audit_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/send/<audit_id>', methods=['POST'])
def audit_send(audit_id):
    try:
        data = request.json or {}
        to_addr = (data.get('to') or '').strip()
        if not to_addr:
            return jsonify({'error': 'to required'}), 400

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT pdf_path, pdf_path_customer, url FROM audit_history WHERE id=?', (audit_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'PDF nicht gefunden'}), 404
        pdf_path_internal, pdf_path_customer, url = row
        # Per Mail geht IMMER die Kundenversion raus, nie die interne Vollversion mit Loesungen -
        # Faellt auf die interne Version zurueck, falls sie (bei alten Audits) noch nicht existiert.
        pdf_path = pdf_path_customer or pdf_path_internal
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF nicht gefunden'}), 404

        settings = load_settings()
        accounts = settings.get('accounts', {})
        account_email = next(iter(accounts), None)
        acc_config = accounts.get(account_email, {}) if account_email else {}
        if not account_email:
            return jsonify({'error': 'Kein E-Mail-Konto konfiguriert'}), 500

        import smtplib
        from email.message import EmailMessage as _EmailMessage
        msg = _EmailMessage()
        msg['Subject'] = f"Ihre kostenlose Website-Analyse - {url}"
        msg['From'] = account_email
        msg['To'] = to_addr
        signature = acc_config.get('signature', '')
        body = (f"Guten Tag,\n\nanbei erhalten Sie die Analyse Ihrer Website {url}.\n"
                f"Wir haben uns Ihre Seite genau angeschaut und zeigen Ihnen im PDF, wo bereits alles passt "
                f"und wo wir Optimierungspotenzial sehen.\n\nBei Fragen melden Sie sich gerne bei uns.\n\n{signature}")
        msg.set_content(body)

        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()
        domain = url.replace('https://', '').replace('http://', '').split('/')[0]
        msg.add_attachment(pdf_data, maintype='application', subtype='pdf',
                           filename=f"Website-Analyse_{domain}.pdf")

        with smtplib.SMTP(acc_config.get('smtp_server', 'smtp.ionos.de'), acc_config.get('smtp_port', 587)) as server:
            server.starttls()
            server.login(account_email, acc_config.get('password', ''))
            server.send_message(msg)

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/instagram/settings', methods=['GET', 'POST'])
def instagram_settings():
    if request.method == 'POST':
        data = request.json or {}
        try:
            settings = load_settings()
            existing = settings.get('instagram_settings', {})
            if not data.get('access_token'):
                data.pop('access_token', None)  # leer gelassen = nicht ändern
            existing.update(data)
            settings['instagram_settings'] = existing
            save_settings(settings)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    settings = load_settings()
    ig = dict(settings.get('instagram_settings', {}))
    ig['access_token_set'] = bool(ig.pop('access_token', None))  # Token nie im Klartext zurückgeben
    return jsonify(ig)

@app.route('/api/instagram/queue')
def instagram_queue():
    if not INSTAGRAM_AVAILABLE:
        return jsonify([])
    return jsonify(instagram_engine.get_ig_queue())

@app.route('/api/instagram/history')
def instagram_history():
    if not INSTAGRAM_AVAILABLE:
        return jsonify([])
    return jsonify(instagram_engine.get_ig_history())

@app.route('/api/instagram/upload', methods=['POST'])
def instagram_upload():
    if not INSTAGRAM_AVAILABLE:
        return jsonify({'success': False, 'error': 'Instagram-Integration nicht verfügbar.'}), 503
    try:
        data = request.get_json() or {}
        filename = data.get('filename', '')
        b64 = data.get('data', '')
        if not filename or not b64:
            return jsonify({'success': False, 'error': 'Dateiname oder Daten fehlen.'}), 400
        raw = base64.b64decode(b64)
        result = instagram_engine.save_uploaded_flyer(filename, raw)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/instagram/delete_flyer', methods=['POST'])
def instagram_delete_flyer():
    if not INSTAGRAM_AVAILABLE:
        return jsonify({'success': False, 'error': 'Instagram-Integration nicht verfügbar.'}), 503
    data = request.get_json() or {}
    result = instagram_engine.delete_flyer(data.get('filename', ''))
    return jsonify(result)

@app.route('/api/instagram/resolve_path', methods=['POST'])
def instagram_resolve_path():
    if not INSTAGRAM_AVAILABLE:
        return jsonify({'folder': None})
    data = request.get_json() or {}
    folder = instagram_engine.resolve_path_by_filename(data.get('filename', ''))
    return jsonify({'folder': folder})

@app.route('/api/instagram/media/<path:filename>')
def instagram_media(filename):
    """Öffentliche Route (kein Login nötig), damit Meta die Bilddatei per Graph-API abrufen kann."""
    if not INSTAGRAM_AVAILABLE:
        return jsonify({'error': 'Nicht verfügbar'}), 404
    path = instagram_engine.media_file_path(filename)
    if not path:
        return jsonify({'error': 'Nicht gefunden'}), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/api/instagram/post_now', methods=['POST'])
def instagram_post_now():
    if not INSTAGRAM_AVAILABLE:
        return jsonify({'success': False, 'error': 'Instagram-Integration nicht verfügbar.'}), 503
    public_base_url = os.environ.get('PUBLIC_BASE_URL') or request.url_root
    result = instagram_engine.post_next_in_queue(public_base_url)
    return jsonify(result)

@app.route('/api/desktop/status')
def desktop_status():
    if not DESKTOP_AVAILABLE:
        return jsonify({'paired': False, 'connected': False})
    return jsonify({
        'paired': desktop_engine.is_paired(),
        'connected': desktop_engine.is_agent_connected()
    })

@app.route('/api/desktop/pair', methods=['POST'])
def desktop_pair():
    if not DESKTOP_AVAILABLE:
        return jsonify({'error': 'Desktop-Agent nicht verfügbar.'}), 503
    token = desktop_engine.generate_pairing_token()
    return jsonify({'token': token})

@app.route('/api/desktop/unpair', methods=['POST'])
def desktop_unpair():
    if not DESKTOP_AVAILABLE:
        return jsonify({'ok': False}), 503
    desktop_engine.revoke_pairing_token()
    return jsonify({'ok': True})

@app.route('/api/desktop/confirm/<action_id>', methods=['POST'])
def desktop_confirm(action_id):
    if not DESKTOP_AVAILABLE:
        return jsonify({'ok': False, 'error': 'Desktop-Agent nicht verfügbar.'}), 503
    return jsonify(desktop_engine.confirm_pending_action(action_id))

@app.route('/api/desktop/cancel/<action_id>', methods=['POST'])
def desktop_cancel(action_id):
    if not DESKTOP_AVAILABLE:
        return jsonify({'ok': False}), 503
    return jsonify(desktop_engine.cancel_pending_action(action_id))

@app.route('/api/desktop/pending/<action_id>')
def desktop_pending(action_id):
    if not DESKTOP_AVAILABLE:
        return jsonify({'found': False}), 404
    entry = desktop_engine.get_pending_action(action_id)
    if not entry:
        return jsonify({'found': False}), 404
    return jsonify({'found': True, 'description': entry['description'], 'action': entry['action']})

@app.route('/api/stats/daily')
def stats_daily():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')

        c.execute("SELECT COUNT(*) FROM mails WHERE created_at LIKE ?", (f'{today}%',))
        received = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM sent_mails WHERE sent_at LIKE ?", (f'{today}%',))
        sent = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM mails WHERE is_new_customer=1 AND created_at LIKE ?", (f'{today}%',))
        new_customers = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM sent_mails WHERE subject LIKE 'Nachfrage:%' AND sent_at LIKE ?", (f'{today}%',))
        followups = c.fetchone()[0]

        c.execute("SELECT subject, to_addr, sent_at FROM sent_mails WHERE sent_at LIKE ? ORDER BY sent_at DESC LIMIT 20", (f'{today}%',))
        recent_sent = [{'subject': r[0], 'to_addr': r[1], 'sent_at': r[2]} for r in c.fetchall()]

        conn.close()
        return jsonify({
            'received': received,
            'sent': sent,
            'new_customers': new_customers,
            'followups': followups,
            'recent_sent': recent_sent
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/contacts', methods=['GET', 'POST'])
def contacts_collection():
    if request.method == 'POST':
        return _create_contact()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM contacts ORDER BY name COLLATE NOCASE')
    contacts = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(contacts)

def _create_contact():
    data = request.json or {}
    email = (data.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'E-Mail ist Pflichtfeld'}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO contacts (name, email, phone, company, notes) VALUES (?, ?, ?, ?, ?)',
        (data.get('name', ''), email, data.get('phone', ''), data.get('company', ''), data.get('notes', ''))
    )
    contact_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': contact_id})

@app.route('/api/contacts/<int:contact_id>', methods=['POST'])
def update_contact(contact_id):
    data = request.json or {}
    email = (data.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'E-Mail ist Pflichtfeld'}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM contacts WHERE id=?', (contact_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({'error': 'Kontakt nicht gefunden'}), 404
    c.execute(
        'UPDATE contacts SET name=?, email=?, phone=?, company=?, notes=? WHERE id=?',
        (data.get('name', ''), email, data.get('phone', ''), data.get('company', ''), data.get('notes', ''), contact_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/contacts/<int:contact_id>/delete', methods=['POST'])
def delete_contact(contact_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM contacts WHERE id=?', (contact_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    if not affected:
        return jsonify({'error': 'Kontakt nicht gefunden'}), 404
    return jsonify({'success': True})

@app.route('/api/trash/empty', methods=['POST'])
def empty_trash():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM mails WHERE deleted=1')
        conn.commit()
        affected = c.rowcount
        conn.close()
        return jsonify({'success': True, 'affected': affected})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fetch', methods=['POST'])
def manual_fetch():
    return jsonify({'success': True, 'new_mails': 0})

@app.route('/api/linkedin/auth')
def linkedin_auth():
    if not LINKEDIN_AVAILABLE or not linkedin_engine.is_configured():
        return jsonify({'error': 'LinkedIn ist nicht konfiguriert (Client ID/Secret fehlen in settings.json)'}), 503
    from flask import redirect
    return redirect(linkedin_engine.get_authorize_url())

@app.route('/api/linkedin/callback')
def linkedin_callback():
    error = request.args.get('error')
    if error:
        return f"LinkedIn-Anmeldung abgebrochen: {request.args.get('error_description', error)}", 400
    code = request.args.get('code')
    if not code:
        return 'Kein Autorisierungs-Code erhalten.', 400
    try:
        linkedin_engine.exchange_code_for_token(code)
        return '✅ LinkedIn erfolgreich verbunden! Du kannst dieses Tab jetzt schließen und zu Siggi zurückkehren.'
    except Exception as e:
        return f'Fehler beim Verbinden: {e}', 500

@app.route('/api/linkedin/status')
def linkedin_status():
    if not LINKEDIN_AVAILABLE:
        return jsonify({'available': False})
    return jsonify({'available': True, 'connected': linkedin_engine.is_connected()})

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'calendar': CALENDAR_AVAILABLE, 'solar': SOLAR_AVAILABLE, 'ga4': GA4_AVAILABLE, 'gsc': GSC_AVAILABLE})

# ─── Erinnerungen: Hintergrund-Loop ────────────────────────────────────────
# Der Server läuft auf Linux - memory_engine.send_windows_notification() (win10toast/
# PowerShell) funktioniert dort nicht. Fällige Erinnerungen gehen deshalb per Mail an
# SIGGI_SEND_ACCOUNT raus - die landen dann ganz normal im Posteingang im Dashboard.

def _fire_due_reminders():
    for r in memory_engine.get_due_reminders():
        try:
            send_new_mail(SIGGI_SEND_ACCOUNT, f"⏰ Erinnerung: {r['message']}", r['message'])
        except Exception as e:
            print(f'[Reminder] Mail-Fehler: {e}')
        memory_engine.mark_reminder_done(r['id'])

def _reminder_loop():
    # Nur ein Worker-Prozess soll den Loop tatsächlich ausführen (gunicorn startet mehrere).
    try:
        import fcntl
        lock_file = open('/tmp/siggi_reminder_loop.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return  # anderer Worker hält den Lock bereits, oder fcntl nicht verfügbar (z.B. Windows)

    while True:
        try:
            _fire_due_reminders()
        except Exception as e:
            print(f'[Reminder] Loop-Fehler: {e}')
        time.sleep(60)

threading.Thread(target=_reminder_loop, daemon=True).start()

# ─── LinkedIn: neue Kommentare erkennen & Antwortvorschlag mailen ──────────
# Postet NICHT automatisch (öffentlich sichtbar, daher wie bei Mails vorsichtig) -
# schickt stattdessen eine Mail mit KI-Antwortvorschlag, den Stefan im Chat freigeben kann.

LINKEDIN_SEEN_COMMENTS_PATH = '/opt/stean/linkedin_seen_comments.json'


def _load_seen_comment_ids():
    if not os.path.exists(LINKEDIN_SEEN_COMMENTS_PATH):
        return set()
    try:
        with open(LINKEDIN_SEEN_COMMENTS_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen_comment_ids(ids):
    with open(LINKEDIN_SEEN_COMMENTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(list(ids)[-500:], f)


def _suggest_linkedin_reply(comment_text, settings):
    api_key = settings.get('anthropic_api_key', '')
    if not api_key:
        return '(Kein API-Key konfiguriert - kein Vorschlag möglich.)'
    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 200,
                'system': 'Du bist SIGGI, der Assistent von Stefan Mutter (ChefBlick). Schreib eine kurze, freundliche, professionelle Antwort auf einen LinkedIn-Kommentar. Max 3 Sätze, keine Signatur.',
                'messages': [{'role': 'user', 'content': f'Kommentar: {comment_text}'}]
            },
            timeout=20
        )
        return resp.json()['content'][0]['text'].strip()
    except Exception as e:
        return f'(Vorschlag fehlgeschlagen: {e})'


def _check_linkedin_comments():
    if not LINKEDIN_AVAILABLE or not linkedin_engine.is_connected():
        return
    settings = load_settings()
    seen = _load_seen_comment_ids()
    new_seen = set(seen)

    for post in linkedin_engine.get_recent_posts(5):
        try:
            comments = linkedin_engine.fetch_comments_raw(post['urn'])
        except Exception as e:
            print(f'[LinkedIn] Fehler beim Lesen der Kommentare: {e}')
            continue

        for c in comments:
            cid = c.get('id') or f"{post['urn']}::{c.get('text', '')[:30]}"
            if cid in seen:
                continue
            new_seen.add(cid)

            suggestion = _suggest_linkedin_reply(c.get('text', ''), settings)
            body = (
                f"Neuer Kommentar zu deinem LinkedIn-Beitrag:\n\n"
                f"\"{c.get('text', '')}\"\n\n"
                f"KI-Antwortvorschlag:\n\"{suggestion}\"\n\n"
                f"Wenn dir der Vorschlag gefällt, sag mir im Chat einfach \"antworte auf LinkedIn mit: [Text]\"."
            )
            try:
                send_new_mail(SIGGI_SEND_ACCOUNT, '💬 Neuer LinkedIn-Kommentar', body)
            except Exception as e:
                print(f'[LinkedIn] Mail-Fehler: {e}')

    if new_seen != seen:
        _save_seen_comment_ids(new_seen)


def _linkedin_comment_loop():
    try:
        import fcntl
        lock_file = open('/tmp/siggi_linkedin_loop.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return

    while True:
        try:
            _check_linkedin_comments()
        except Exception as e:
            print(f'[LinkedIn] Loop-Fehler: {e}')
        time.sleep(300)  # alle 5 Minuten


threading.Thread(target=_linkedin_comment_loop, daemon=True).start()

# ─── Instagram: Auto-Post-Loop ─────────────────────────────────────────────

def _instagram_auto_post_loop():
    if not INSTAGRAM_AVAILABLE:
        return
    try:
        import fcntl
        lock_file = open('/tmp/siggi_instagram_loop.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return  # anderer Worker hält den Lock bereits

    public_base_url = os.environ.get('PUBLIC_BASE_URL', 'https://www.stean.info')
    while True:
        try:
            instagram_engine.maybe_auto_post(public_base_url)
        except Exception as e:
            print(f'[Instagram] Auto-Post-Loop-Fehler: {e}')
        time.sleep(60)

threading.Thread(target=_instagram_auto_post_loop, daemon=True).start()

if __name__ == '__main__':
    if socketio:
        socketio.run(app, port=8080, debug=False)
    else:
        app.run(port=8080, debug=False)
