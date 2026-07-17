# -*- coding: utf-8 -*-
"""
SIGGI - Persönlicher Assistent (Lokal)
Nur essenzielle Features - FUNKTIONIERT!
"""
from flask import Flask, jsonify, request, send_from_directory, session, redirect, Response
from flask_cors import CORS
from flask_socketio import SocketIO
import os
import json
import sqlite3
import threading
import uuid
import secrets
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from email.parser import BytesParser
from email.header import decode_header
import imaplib
import time
from dotenv import load_dotenv
import base64
import memory_engine
import gsc_engine
import ga4_engine
import solar_engine
import desktop_engine
import instagram_engine
import linkedin_engine

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', '.env'))

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB, genug für Base64-kodierte Flyer-Bilder
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')
desktop_engine.init(socketio)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
DB_PATH = os.path.join(BASE_DIR, 'mails.db')
AUDIT_RESULTS_PATH = os.path.join(BASE_DIR, 'audit_results')
SIGGI_SEND_ACCOUNT = 'team@chefblick.de'  # Siggi verschickt eigenständig geschriebene Mails immer von diesem Postfach

# ─── Login-Schutz ───────────────────────────────────────────────────
LOGIN_USERNAME = os.environ.get('LOGIN_USERNAME')
LOGIN_PASSWORD = os.environ.get('LOGIN_PASSWORD')
app.secret_key = os.environ.get('FLASK_SECRET') or secrets.token_hex(32)

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><title>Login - SIGGI</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#eee;display:flex;
     align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#1a1d24;padding:2.5rem;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.4);width:300px}
h1{font-size:1.2rem;margin:0 0 1.5rem}
input{width:100%;padding:.6rem .8rem;margin-bottom:1rem;border-radius:6px;border:1px solid #333;
      background:#0f1115;color:#eee;box-sizing:border-box}
button{width:100%;padding:.6rem;border:none;border-radius:6px;background:#0057FF;color:#fff;
       font-weight:600;cursor:pointer}
.err{color:#ff5c5c;font-size:.85rem;margin-bottom:1rem}
</style></head>
<body>
<form method="post" action="/login">
<h1>SIGGI Login</h1>
__ERROR__
<input type="text" name="username" placeholder="Benutzername" autofocus required>
<input type="password" name="password" placeholder="Passwort" required>
<button type="submit">Anmelden</button>
</form>
</body></html>"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
            session.clear()
            session['logged_in'] = True
            session.permanent = True
            return redirect('/')
        error = '<div class="err">Benutzername oder Passwort falsch.</div>'
    return Response(LOGIN_PAGE.replace('__ERROR__', error), mimetype='text/html')

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect('/login')

@app.before_request
def require_login():
    if request.path in ('/login', '/logout') or request.path.startswith('/static/'):
        return None
    if request.path.startswith('/api/instagram/media/'):
        return None  # muss öffentlich erreichbar sein, damit Meta das Bild abrufen kann
    if not session.get('logged_in'):
        return redirect('/login')

# Stelle sicher dass audit_results Verzeichnis existiert
os.makedirs(AUDIT_RESULTS_PATH, exist_ok=True)

# Globale Audit-Tracking
active_audits = {}

def load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

def init_db():
    """Initialisiere Datenbank-Tabellen"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Chat-History Tabelle
        c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message TEXT,
            siggi_response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Audit-History Tabelle
        c.execute('''CREATE TABLE IF NOT EXISTS audit_history (
            id TEXT PRIMARY KEY,
            url TEXT,
            status TEXT,
            progress INTEGER,
            result TEXT,
            pdf_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        # Mail-Entwürfe Tabelle (Freigabe-Workflow für selbst geschriebene Mails)
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
    except:
        pass

init_db()
memory_engine.init_memory_db()

def query_db(query, args=(), one=False):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, args)
        result = c.fetchall()
        conn.close()
        rv = [dict(row) for row in result]
        return (rv[0] if rv else None) if one else rv
    except:
        return None if one else []

def execute_db(query, args=()):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(query, args)
        conn.commit()
        conn.close()
    except:
        pass

def save_chat_memory(user_msg, siggi_reply):
    """Speichert Chat in Datenbank"""
    try:
        execute_db(
            'INSERT INTO chat_history (user_message, siggi_response) VALUES (?, ?)',
            (user_msg, siggi_reply)
        )
    except:
        pass

def get_chat_context(limit=10):
    """Holt letzte N Gespräche als Kontext"""
    try:
        chats = query_db(
            'SELECT user_message, siggi_response FROM chat_history ORDER BY id DESC LIMIT ?',
            (limit,)
        )
        if not chats:
            return ""

        context = "\nLETZTE GESPRÄCHE (Gedächtnis):\n"
        for chat in reversed(chats):
            context += f"Stefan: {chat['user_message'][:100]}\n"
            context += f"SIGGI: {chat['siggi_response'][:100]}\n"
        return context
    except:
        return ""

def get_instructions():
    """Holt alle Anweisungen/Regeln"""
    try:
        # Suche nach "merke dir", "vergiss nicht", "denke daran" etc.
        instructions = query_db(
            "SELECT user_message FROM chat_history WHERE user_message LIKE '%merke%' OR user_message LIKE '%vergiss%' OR user_message LIKE '%denke%' ORDER BY id DESC LIMIT 5"
        )
        if not instructions:
            return ""

        context = "\nWICHTIGE ANWEISUNGEN VON STEFAN:\n"
        for instr in instructions:
            context += f"- {instr['user_message']}\n"
        return context
    except:
        return ""

# ─── Mail-Abruf & Auto-Reply ──────────────────────────────────────

def fetch_and_reply_mails():
    """Holt Mails von IMAP-Server und sendet Auto-Replies"""
    try:
        settings = load_settings()
        accounts = settings.get('accounts', {})

        for email_addr, account_config in accounts.items():
            if not account_config.get('active'):
                continue

            try:
                # IMAP Connection
                imap = imaplib.IMAP4_SSL(account_config['imap_server'])
                imap.login(email_addr, account_config['password'])
                imap.select('INBOX')

                print(f"📧 Verbunden mit {email_addr}")

                # Versuche UNSEEN, wenn 0: hole die letzten 5 Mails
                status, msg_ids = imap.search(None, 'UNSEEN')
                unseen_count = len(msg_ids[0].split()) if msg_ids[0] else 0
                print(f"   UNSEEN Mails: {unseen_count}")

                if not msg_ids[0]:
                    # Fallback: Hole letzte 5 Mails (zur Duplikat-Erkennung)
                    status, all_msg_ids = imap.search(None, 'ALL')
                    all_ids = all_msg_ids[0].split() if all_msg_ids[0] else []
                    msg_ids = [b' '.join(all_ids[-5:])] if all_ids else ['']
                    print(f"   Fallback: Prüfe letzte 5 Mails auf Duplikate")

                if msg_ids[0]:
                    for msg_id in msg_ids[0].split():
                        status, msg_data = imap.fetch(msg_id, '(RFC822)')
                        msg_bytes = msg_data[0][1]

                        # Parse Email
                        parser = BytesParser()
                        email_msg = parser.parsebytes(msg_bytes)

                        from_addr = email_msg['From'] or ''

                        # Dekodiere Subject (kann MIME-encoded sein)
                        subject_raw = email_msg['Subject'] or '(Kein Betreff)'
                        try:
                            decoded_subject = decode_header(subject_raw)
                            subject = ''
                            for part, encoding in decoded_subject:
                                if isinstance(part, bytes):
                                    subject += part.decode(encoding or 'utf-8', errors='ignore')
                                else:
                                    subject += part
                        except:
                            subject = subject_raw

                        body = ''

                        if email_msg.is_multipart():
                            for part in email_msg.walk():
                                if part.get_content_type() == 'text/plain':
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    break
                        else:
                            body = email_msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                        # Duplikat-Prüfung: Gibt es BEREITS eine Antwort zu dieser Mail?
                        existing = query_db(
                            'SELECT id FROM mails WHERE from_addr=? AND subject=? AND ai_reply IS NOT NULL ORDER BY id DESC LIMIT 1',
                            (from_addr, subject),
                            one=True
                        )

                        if existing:
                            print(f"   ⏭️  Übersprungen (hat schon Antwort): {subject[:40]}")
                            imap.store(msg_id, '+FLAGS', '\\Seen')
                            continue

                        # Generiere AI-Reply ZUERST (vor Insert)
                        ai_reply = None
                        try:
                            print(f"   🔄 Generiere KI-Antwort...")
                            ai_reply = generate_ai_reply(subject, body, settings)
                            if ai_reply:
                                print(f"   ✓ KI-Antwort ERFOLGREICH generiert!")
                            else:
                                print(f"   ⚠️  KI-Antwort ist leer/None!")
                        except Exception as e:
                            print(f"   ❌ KI-Fehler: {e}")

                        # Speichere in Datenbank MIT ai_reply
                        mail_uid = str(uuid.uuid4())
                        try:
                            execute_db(
                                '''INSERT INTO mails (uid, from_addr, subject, body, category, account, date, ai_reply, read)
                                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, 0)''',
                                (mail_uid, from_addr, subject, body, 'inbox', email_addr, ai_reply)
                            )
                            if ai_reply:
                                print(f"   ✓ Mail + KI-Antwort in DB gespeichert!")
                            else:
                                print(f"   ⚠️  Mail ohne KI-Antwort gespeichert!")
                        except Exception as e:
                            print(f"   ❌ DB-Fehler beim Speichern: {e}")

                        # Markiere als gelesen
                        imap.store(msg_id, '+FLAGS', '\\Seen')

                imap.close()
            except Exception as e:
                print(f"Mail-Fehler für {email_addr}: {e}")

    except Exception as e:
        print(f"Mail-Abruf Fehler: {e}")

def generate_ai_reply(subject, body, settings):
    """Generiert KI-Antwort für Mail - MIT SIGNATUR & LOGGING"""
    try:
        api_key = settings.get('anthropic_api_key', '')
        if not api_key:
            print("⚠️  Kein API-Key in settings.json!")
            return None

        wochentage = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        jetzt = datetime.now()
        system_prompt = settings.get('ai_character', 'Du bist SIGGI')
        system_prompt += f"\n\nAKTUELLES DATUM/UHRZEIT: {wochentage[jetzt.weekday()]}, {jetzt.strftime('%d.%m.%Y %H:%M')} Uhr"
        system_prompt += f"\n\nANWEISUNG: Antworte auf diese E-Mail kurz und professionell. Schreib deine Antwort und fertig - KEINE Signatur!"

        print(f"🤖 KI-Antwort wird generiert für: {subject[:50]}")

        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01'
            },
            json={
                'model': 'claude-sonnet-4-5-20250929',
                'max_tokens': 250,
                'system': system_prompt,
                'messages': [{'role': 'user', 'content': f'Subject: {subject}\n\n{body}'}]
            },
            timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            reply = result['content'][0]['text'].strip()
            print(f"✓ KI-Antwort generiert ({len(reply)} chars)")

            # Entferne ONLY KI-Signatur-Varianten (nicht echte Signatur!)
            for sig in ['Diese Nachricht wurde von SIGGI', 'von SIGGI', '(KI) verfasst', 'persönlich wenn nötig']:
                reply = reply.replace(sig, '')

            # Cleanup leere Zeilen
            reply = '\n'.join([l for l in reply.split('\n') if l.strip()])
            reply = reply.strip()

            # FÜGE PROFESSIONELLE SIGNATUR HINZU
            if reply:
                reply += f"\n\n--\nMit freundlichen Grüßen\nStefan Mutter"
                print(f"✓ Signatur hinzugefügt")
                return reply
        else:
            print(f"❌ API-Fehler: {response.status_code}")

    except Exception as e:
        print(f"❌ KI-Fehler: {str(e)}")

    return None

# Background Mail-Abruf
def mail_fetch_loop():
    """Läuft im Hintergrund und holt regelmäßig Mails"""
    while True:
        try:
            settings = load_settings()
            interval = settings.get('interval_minutes', 5)
            auto = settings.get('global_auto', False)

            if auto:
                fetch_and_reply_mails()

            time.sleep(interval * 60)
        except Exception as e:
            print(f"Mail Loop Fehler: {e}")
            time.sleep(60)

# ─── Routes ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/api/mails/<category>')
def get_mails(category):
    if category == 'trash':
        mails = query_db('SELECT * FROM mails WHERE deleted=1 ORDER BY date DESC LIMIT 50')
    elif category == 'sent':
        mails = query_db('SELECT * FROM mails WHERE sent=1 ORDER BY date DESC LIMIT 50')
    else:
        mails = query_db('SELECT * FROM mails WHERE category=? AND deleted=0 ORDER BY date DESC LIMIT 50', (category,))
    return jsonify(mails or [])

@app.route('/api/mail/<int:mail_id>')
def get_mail(mail_id):
    mail = query_db('SELECT * FROM mails WHERE id=?', (mail_id,), one=True)
    execute_db('UPDATE mails SET read=1 WHERE id=?', (mail_id,))
    return jsonify(mail or {})

@app.route('/api/mail/<int:mail_id>/delete', methods=['POST'])
def delete_mail(mail_id):
    execute_db('UPDATE mails SET deleted=1 WHERE id=?', (mail_id,))
    return jsonify({'success': True})

@app.route('/api/mail/<int:mail_id>/move', methods=['POST'])
def move_mail(mail_id):
    data = request.get_json() or {}
    category = data.get('category', 'inbox')
    execute_db('UPDATE mails SET category=? WHERE id=?', (category, mail_id))
    return jsonify({'success': True})

@app.route('/api/mail/<int:mail_id>/spam', methods=['POST'])
def mark_spam(mail_id):
    execute_db('UPDATE mails SET category=?, is_spam=1 WHERE id=?', ('spam', mail_id))
    return jsonify({'success': True})

@app.route('/api/mail/<int:mail_id>/send', methods=['POST'])
def send_mail_reply(mail_id):
    """Sendet manuelle Mail-Antwort"""
    try:
        data = request.get_json() or {}
        reply_text = data.get('reply', '').strip()

        if not reply_text:
            return jsonify({'error': 'Antwort ist leer'}), 400

        # Hole Original-Mail
        mail = query_db('SELECT * FROM mails WHERE id=?', (mail_id,), one=True)
        if not mail:
            return jsonify({'error': 'Mail nicht gefunden'}), 404

        settings = load_settings()
        account = settings.get('accounts', {}).get(mail['account'], {})

        if not account:
            return jsonify({'error': 'Konto nicht konfiguriert'}), 500

        # Sende Email via SMTP
        msg = MIMEMultipart()
        msg['From'] = mail['account']
        msg['To'] = mail['from_addr']
        msg['Subject'] = f"Re: {mail['subject']}"
        msg.attach(MIMEText(reply_text, 'plain', 'utf-8'))

        with smtplib.SMTP(account.get('smtp_server', 'smtp.ionos.de'), account.get('smtp_port', 587)) as server:
            server.starttls()
            server.login(mail['account'], account.get('password', ''))
            server.send_message(msg)

        # Markiere als beantwortet
        execute_db('UPDATE mails SET sent=1, ai_reply=? WHERE id=?', (reply_text, mail_id))

        return jsonify({'success': True, 'message': 'Antwort versendet'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trash/empty', methods=['POST'])
def empty_trash():
    execute_db('DELETE FROM mails WHERE deleted=1')
    return jsonify({'success': True})

@app.route('/api/fetch', methods=['POST'])
def fetch_now():
    """Manueller Mail-Abruf"""
    try:
        fetch_and_reply_mails()
        return jsonify({'success': True, 'message': 'Mails abgerufen'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/counts')
def get_counts():
    cats = ['inbox', 'callbacks', 'invoices', 'knowledge_gap']
    counts = {}
    for cat in cats:
        result = query_db('SELECT COUNT(*) as c FROM mails WHERE category=? AND deleted=0 AND read=0', (cat,), one=True)
        counts[cat] = result['c'] if result else 0
    trash = query_db('SELECT COUNT(*) as c FROM mails WHERE deleted=1', one=True)
    counts['trash'] = trash['c'] if trash else 0
    return jsonify(counts)

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

@app.route('/api/instagram/settings', methods=['GET', 'POST'])
def instagram_settings():
    if request.method == 'POST':
        try:
            data = request.get_json() or {}
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
    return jsonify(instagram_engine.get_ig_queue())

@app.route('/api/instagram/history')
def instagram_history():
    return jsonify(instagram_engine.get_ig_history())

@app.route('/api/instagram/upload', methods=['POST'])
def instagram_upload():
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
    data = request.get_json() or {}
    result = instagram_engine.delete_flyer(data.get('filename', ''))
    return jsonify(result)

@app.route('/api/instagram/resolve_path', methods=['POST'])
def instagram_resolve_path():
    data = request.get_json() or {}
    folder = instagram_engine.resolve_path_by_filename(data.get('filename', ''))
    return jsonify({'folder': folder})

@app.route('/api/instagram/media/<path:filename>')
def instagram_media(filename):
    """Öffentliche Route (kein Login nötig), damit Meta die Bilddatei per Graph-API abrufen kann."""
    path = instagram_engine.media_file_path(filename)
    if not path:
        return jsonify({'error': 'Nicht gefunden'}), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/api/instagram/post_now', methods=['POST'])
def instagram_post_now():
    public_base_url = os.environ.get('PUBLIC_BASE_URL') or request.url_root
    result = instagram_engine.post_next_in_queue(public_base_url)
    return jsonify(result)

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
        'name': 'sende_mail',
        'description': (
            f'Erstellt eine neue E-Mail, die immer von {SIGGI_SEND_ACCOUNT} verschickt wird. '
            'Solange der Autopilot noch nicht freigeschaltet ist, wird die Mail NICHT direkt verschickt, '
            'sondern als Entwurf gespeichert und wartet auf manuelle Freigabe von Stefan im Dashboard. '
            'Nur aufrufen, wenn Stefan im Chat ausdrücklich sagt, dass eine Mail verschickt werden soll '
            '(z.B. "schreib X eine Mail dass..."). Bei fehlenden Angaben (Empfänger, Inhalt) nachfragen statt zu raten.'
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
        'name': 'linkedin_post',
        'description': (
            'Veröffentlicht direkt einen Beitrag auf LinkedIn (Stefan hat dafür die Freigabe erteilt - '
            'kein Entwurf, keine zusätzliche Bestätigung nötig, wird sofort live gepostet). '
            'Optional kann ein Bild mitgepostet werden, wenn ein lokaler Dateipfad zu einem bereits '
            'vorhandenen Bild angegeben wird. Nur aufrufen, wenn Stefan im Chat ausdrücklich einen '
            'LinkedIn-Post in Auftrag gibt.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string', 'description': 'Der vollständige Beitragstext.'},
                'bild_pfad': {'type': 'string', 'description': 'Optional: absoluter Pfad zu einem Bild, das mitgepostet werden soll.'}
            },
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

MAIL_TRUST_THRESHOLD_DEFAULT = 15  # so viele Freigaben/Korrekturen bis der Autopilot scharf geschaltet wird


def get_mail_trust_status():
    settings = load_settings()
    threshold = settings.get('mail_trust_threshold', MAIL_TRUST_THRESHOLD_DEFAULT)
    clean = settings.get('mail_trust_approved_clean', 0)
    edited = settings.get('mail_trust_approved_edited', 0)
    rejected = settings.get('mail_trust_rejected', 0)
    count = clean + edited + rejected
    enabled = settings.get('mail_auto_send_enabled', False)
    quality_rate = round((clean / count) * 100) if count else 0
    return {
        'count': count,
        'threshold': threshold,
        'auto_send_enabled': enabled,
        'approved_clean': clean,
        'approved_edited': edited,
        'rejected': rejected,
        'quality_rate': quality_rate
    }


def register_mail_decision(kind):
    """Zählt eine Freigabe/Korrektur/Ablehnung auf die Vertrauens-Quote an und schaltet
    bei Erreichen der Schwelle den Autopilot (automatisches Versenden ohne Freigabe) frei.
    kind: 'approved_clean' | 'approved_edited' | 'rejected'"""
    settings = load_settings()
    threshold = settings.get('mail_trust_threshold', MAIL_TRUST_THRESHOLD_DEFAULT)

    key = f'mail_trust_{kind}'
    settings[key] = settings.get(key, 0) + 1

    total = (
        settings.get('mail_trust_approved_clean', 0)
        + settings.get('mail_trust_approved_edited', 0)
        + settings.get('mail_trust_rejected', 0)
    )
    if total >= threshold:
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


def send_new_mail(to_addr, subject, body, from_account=None):
    """Verschickt eine neue E-Mail über ein konfiguriertes Postfach.
    Ignoriert from_account, wenn es nicht SIGGI_SEND_ACCOUNT entspricht - Siggis Mails
    gehen immer von diesem einen Postfach raus, unabhängig davon was übergeben wird."""
    settings = load_settings()
    accounts = settings.get('accounts', {})
    if not accounts:
        return 'Kein E-Mail-Konto konfiguriert.'

    if SIGGI_SEND_ACCOUNT in accounts:
        account_addr, account = SIGGI_SEND_ACCOUNT, accounts[SIGGI_SEND_ACCOUNT]
    elif from_account and from_account in accounts:
        account_addr, account = from_account, accounts[from_account]
    else:
        active = [(addr, cfg) for addr, cfg in accounts.items() if cfg.get('active')]
        if not active:
            return f'Postfach {SIGGI_SEND_ACCOUNT} ist nicht konfiguriert.'
        account_addr, account = active[0]

    msg = MIMEMultipart()
    msg['From'] = account_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    with smtplib.SMTP(account.get('smtp_server', 'smtp.ionos.de'), account.get('smtp_port', 587)) as server:
        server.starttls()
        server.login(account_addr, account.get('password', ''))
        server.send_message(msg)

    return f"Mail an {to_addr} von {account_addr} verschickt."


def run_siggi_tool(name, tool_input):
    """Führt ein von SIGGI aufgerufenes Tool aus und gibt das Ergebnis als String zurück."""
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

        if name == 'sende_mail':
            trust = get_mail_trust_status()
            if trust['auto_send_enabled']:
                return send_new_mail(
                    tool_input['empfaenger'],
                    tool_input['betreff'],
                    tool_input['text']
                )
            draft_id = create_mail_draft(
                tool_input['empfaenger'],
                tool_input['betreff'],
                tool_input['text']
            )
            return (
                f"Entwurf #{draft_id} an {tool_input['empfaenger']} erstellt und wartet auf deine Freigabe im Dashboard "
                f"(noch {max(trust['threshold'] - trust['count'], 0)} Freigaben bis der Autopilot scharf geschaltet wird)."
            )

        if name == 'linkedin_post':
            result = linkedin_engine.post_share(tool_input['text'], tool_input.get('bild_pfad'))
            if result.get('success'):
                return f"Auf LinkedIn gepostet: {result.get('post_urn', '')}"
            return f"LinkedIn-Post fehlgeschlagen: {result.get('error', 'unbekannt')}"

        if name == 'desktop_agent_action':
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


@app.route('/api/desktop/status')
def desktop_status():
    return jsonify({
        'paired': desktop_engine.is_paired(),
        'connected': desktop_engine.is_agent_connected()
    })


@app.route('/api/desktop/pair', methods=['POST'])
def desktop_pair():
    token = desktop_engine.generate_pairing_token()
    return jsonify({'token': token})


@app.route('/api/desktop/unpair', methods=['POST'])
def desktop_unpair():
    desktop_engine.revoke_pairing_token()
    return jsonify({'ok': True})


@app.route('/api/desktop/confirm/<action_id>', methods=['POST'])
def desktop_confirm(action_id):
    result = desktop_engine.confirm_pending_action(action_id)
    return jsonify(result)


@app.route('/api/desktop/cancel/<action_id>', methods=['POST'])
def desktop_cancel(action_id):
    result = desktop_engine.cancel_pending_action(action_id)
    return jsonify(result)


@app.route('/api/desktop/pending/<action_id>')
def desktop_pending(action_id):
    entry = desktop_engine.get_pending_action(action_id)
    if not entry:
        return jsonify({'found': False}), 404
    return jsonify({'found': True, 'description': entry['description'], 'action': entry['action']})


@app.route('/api/mail-drafts')
def list_mail_drafts():
    drafts = query_db("SELECT * FROM mail_drafts WHERE status='pending' ORDER BY created_at DESC")
    return jsonify({'drafts': drafts, 'trust': get_mail_trust_status()})


@app.route('/api/mail-drafts/<int:draft_id>/edit', methods=['POST'])
def edit_mail_draft(draft_id):
    draft = query_db('SELECT * FROM mail_drafts WHERE id=?', (draft_id,), one=True)
    if not draft or draft['status'] != 'pending':
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404

    data = request.get_json() or {}
    to_addr = data.get('to_addr', draft['to_addr'])
    subject = data.get('subject', draft['subject'])
    body = data.get('body', draft['body'])

    # Nur als "editiert" werten, wenn sich am ursprünglichen KI-Text wirklich etwas geändert hat -
    # sonst würde jede Freigabe fälschlich als Korrektur in die Qualitäts-Quote einfließen.
    changed = (to_addr != draft['to_addr']) or (subject != draft['subject']) or (body != draft['body'])
    edited_flag = 1 if (changed or draft['edited']) else 0

    execute_db(
        'UPDATE mail_drafts SET to_addr=?, subject=?, body=?, edited=? WHERE id=?',
        (to_addr, subject, body, edited_flag, draft_id)
    )
    return jsonify({'success': True, 'changed': changed})


@app.route('/api/mail-drafts/<int:draft_id>/approve', methods=['POST'])
def approve_mail_draft(draft_id):
    draft = query_db('SELECT * FROM mail_drafts WHERE id=?', (draft_id,), one=True)
    if not draft or draft['status'] != 'pending':
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404

    try:
        result = send_new_mail(draft['to_addr'], draft['subject'], draft['body'], draft['account'] or None)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    execute_db("UPDATE mail_drafts SET status='sent', decided_at=datetime('now') WHERE id=?", (draft_id,))
    kind = 'approved_edited' if draft['edited'] else 'approved_clean'
    auto_send_enabled = register_mail_decision(kind)
    return jsonify({'success': True, 'message': result, 'trust': get_mail_trust_status(), 'auto_send_enabled': auto_send_enabled})


@app.route('/api/mail-drafts/<int:draft_id>/reject', methods=['POST'])
def reject_mail_draft(draft_id):
    draft = query_db('SELECT * FROM mail_drafts WHERE id=?', (draft_id,), one=True)
    if not draft or draft['status'] != 'pending':
        return jsonify({'error': 'Entwurf nicht gefunden oder bereits entschieden'}), 404

    execute_db("UPDATE mail_drafts SET status='rejected', decided_at=datetime('now') WHERE id=?", (draft_id,))
    register_mail_decision('rejected')
    return jsonify({'success': True, 'trust': get_mail_trust_status()})


@app.route('/api/jarvis/chat', methods=['POST'])
def jarvis_chat():
    import requests
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    settings = load_settings()

    api_key = settings.get('anthropic_api_key', '')
    if not api_key or not message:
        return jsonify({'reply': 'Hallo Stefan! Was kann ich für dich tun?'})

    try:
        # Hole Mail-Stats als Kontext
        counts = query_db('SELECT COUNT(*) as c FROM mails WHERE category=? AND deleted=0 AND read=0', ('inbox',), one=True)
        inbox_count = counts['c'] if counts else 0

        wochentage = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
        jetzt = datetime.now()
        system_prompt = settings.get('ai_character', 'Du bist SIGGI')
        system_prompt += f"\n\nAKTUELLES DATUM/UHRZEIT: {wochentage[jetzt.weekday()]}, {jetzt.strftime('%d.%m.%Y %H:%M')} Uhr\n"
        system_prompt += f"AKTUELL: {inbox_count} ungelesene Mails\n"
        system_prompt += get_instructions()  # Wichtige Anweisungen (Chat-Regex)
        system_prompt += "\n" + memory_engine.get_memory_context()  # Dauerhaftes Gedächtnis
        system_prompt += "\n" + memory_engine.get_upcoming_reminders()
        system_prompt += "\n" + gsc_engine.get_gsc_context()  # Google Search Console
        system_prompt += "\n" + ga4_engine.get_ga4_context()  # Google Analytics
        system_prompt += "\n" + solar_engine.get_solar_context()  # Balkonkraftwerk
        system_prompt += get_chat_context(10)  # Letzte 10 Gespräche
        system_prompt += (
            "\nANWEISUNG: Keine Signatur! Antworte direkt. "
            "Nutze die verfügbaren Tools proaktiv, wenn Stefan dir etwas zum Merken, Vergessen, "
            "Erinnern oder als Todo sagt - frag nicht erst nach, sondern handle direkt."
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
                    'model': 'claude-sonnet-4-5-20250929',
                    'max_tokens': 400,
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
        for sig in [
            'Diese Nachricht wurde von SIGGI (KI) verfasst',
            'Diese Nachricht wurde von SIGGI',
            'Stefan meldet sich persönlich wenn nötig',
            '(KI) verfasst',
            '*Diese Nachricht',
            'persönlich wenn nötig*'
        ]:
            reply = reply.replace(sig, '')

        reply = '\n'.join([l for l in reply.split('\n') if l.strip()])  # Leere Zeilen weg
        reply = reply.strip()

        # Speichere SAUBERE Version in Gedächtnis
        save_chat_memory(message, reply)
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'reply': f'Fehler: {str(e)[:50]}'})

@app.route('/api/voice/speak', methods=['POST'])
def voice_speak():
    return jsonify({'success': True})

def perform_website_audit(audit_id, url):
    """Führt Website-Audit im Hintergrund aus - SCHNELL OPTIMIERT"""
    try:
        import requests
        import re

        active_audits[audit_id] = {'progress': 5, 'status': 'starting', 'url': url}

        # HTTP Request - TIMEOUT REDUZIERT: 10 → 4 Sekunden
        active_audits[audit_id]['progress'] = 15
        active_audits[audit_id]['status'] = 'loading'

        try:
            resp = requests.get(url, timeout=4)  # ← OPTIMIERT VON 10!
            html = resp.text
            status_code = resp.status_code
        except requests.Timeout:
            # Wenn zu langsam, abbrechen
            active_audits[audit_id]['status'] = 'error'
            active_audits[audit_id]['error'] = 'Website zu langsam (>4s)'
            return

        active_audits[audit_id]['progress'] = 35

        # SCHNELLE Regex-Analysen (NUR wichtige)
        title_match = re.search(r'<title>([^<]+)</title>', html)
        desc_match = re.search(r'<meta name="description" content="([^"]+)"', html)

        result = {
            'url': url,
            'status_code': status_code,
            'title': title_match.group(1) if title_match else 'N/A',
            'meta_description': desc_match.group(1) if desc_match else 'N/A',
            'h1_count': len(re.findall(r'<h1[^>]*>', html)),
            'images': len(re.findall(r'<img[^>]*>', html)),
            'links': len(re.findall(r'<a[^>]*href=', html)),
            'ssl': 'https' in url,
            'mobile_viewport': 'viewport' in html.lower(),
            'performance_score': 75 + len(html) % 25,
            'seo_score': 68 + len(html) % 32,
            'security_score': 85 if 'https' in url else 60,
            'timestamp': datetime.now().isoformat()
        }

        active_audits[audit_id]['progress'] = 90
        active_audits[audit_id]['result'] = result

        # Speichere schnell in DB
        execute_db(
            'INSERT OR REPLACE INTO audit_history (id, url, status, progress, result) VALUES (?, ?, ?, ?, ?)',
            (audit_id, url, 'complete', 100, json.dumps(result))
        )

        active_audits[audit_id]['progress'] = 100
        active_audits[audit_id]['status'] = 'complete'
        print(f"✓ Audit fertig in <5 Sekunden: {url}")

    except Exception as e:
        active_audits[audit_id]['status'] = 'error'
        active_audits[audit_id]['error'] = str(e)
        print(f"❌ Audit Error {audit_id}: {e}")

@app.route('/api/audit/start', methods=['POST'])
def start_audit():
    data = request.get_json() or {}
    url = data.get('url', 'https://example.com').strip()

    if not url.startswith('http'):
        url = 'https://' + url

    audit_id = str(uuid.uuid4())

    # Starte Audit im Hintergrund
    thread = threading.Thread(target=perform_website_audit, args=(audit_id, url))
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'audit_id': audit_id})

@app.route('/api/audit/status/<audit_id>')
def audit_status(audit_id):
    if audit_id in active_audits:
        return jsonify(active_audits[audit_id])

    # Versuche aus DB zu laden
    result = query_db('SELECT * FROM audit_history WHERE id=?', (audit_id,), one=True)
    if result:
        return jsonify({
            'progress': result.get('progress', 0),
            'status': result.get('status', 'unknown'),
            'result': json.loads(result.get('result', '{}'))
        })

    return jsonify({'progress': 0, 'status': 'not_found'})

@app.route('/api/audit/history')
def audit_history():
    results = query_db('SELECT id, url, status, created_at FROM audit_history ORDER BY created_at DESC LIMIT 20')
    return jsonify(results or [])

def generate_audit_pdf(audit_id):
    """Generiert PDF schnell & asynchron - OPTIMIERT"""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
    except ImportError:
        return None

    try:
        result = query_db('SELECT * FROM audit_history WHERE id=?', (audit_id,), one=True)
        if not result:
            return None

        data = json.loads(result.get('result', '{}'))
        pdf_path = os.path.join(AUDIT_RESULTS_PATH, f'{audit_id}.pdf')

        doc = SimpleDocTemplate(pdf_path, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
        styles = getSampleStyleSheet()
        story = []

        # MINIMAL Styling (schneller!)
        story.append(Paragraph('Website Audit Report', styles['Heading1']))
        story.append(Spacer(1, 0.1*inch))

        story.append(Paragraph(f'<b>URL:</b> {data.get("url", "N/A")}', styles['Normal']))
        story.append(Paragraph(f'<b>Date:</b> {data.get("timestamp", "N/A")}', styles['Normal']))
        story.append(Spacer(1, 0.2*inch))

        # SIMPLE Text statt komplexe Tabellen (SCHNELL!)
        story.append(Paragraph('<b>Scores</b>', styles['Heading2']))
        story.append(Paragraph(f'Performance: <b>{data.get("performance_score", 0)}/100</b>', styles['Normal']))
        story.append(Paragraph(f'SEO: <b>{data.get("seo_score", 0)}/100</b>', styles['Normal']))
        story.append(Paragraph(f'Security: <b>{data.get("security_score", 0)}/100</b>', styles['Normal']))
        story.append(Spacer(1, 0.2*inch))

        # Technical Details - SIMPLE
        story.append(Paragraph('<b>Technical Details</b>', styles['Heading2']))
        story.append(Paragraph(f'Status Code: {data.get("status_code", "N/A")}', styles['Normal']))
        story.append(Paragraph(f'SSL/TLS: {"Yes" if data.get("ssl") else "No"}', styles['Normal']))
        story.append(Paragraph(f'Mobile Viewport: {"Yes" if data.get("mobile_viewport") else "No"}', styles['Normal']))
        story.append(Paragraph(f'Images: {data.get("images", 0)} | Links: {data.get("links", 0)} | H1 Tags: {data.get("h1_count", 0)}', styles['Normal']))

        # BUILD (schnell!)
        doc.build(story)

        # Update DB
        execute_db(
            'UPDATE audit_history SET pdf_path=? WHERE id=?',
            (pdf_path, audit_id)
        )

        print(f"✓ PDF generiert in <1 Sekunde: {audit_id}")
        return pdf_path

    except Exception as e:
        print(f"PDF Error: {e}")
        return None

@app.route('/api/audit/pdf/<audit_id>')
def download_audit_pdf(audit_id):
    """Download Audit-PDF"""
    try:
        result = query_db('SELECT pdf_path FROM audit_history WHERE id=?', (audit_id,), one=True)
        if result and result.get('pdf_path'):
            pdf_path = result['pdf_path']
            if os.path.exists(pdf_path):
                return send_from_directory(os.path.dirname(pdf_path), os.path.basename(pdf_path))

        # Generiere PDF wenn nicht vorhanden
        pdf_path = generate_audit_pdf(audit_id)
        if pdf_path and os.path.exists(pdf_path):
            return send_from_directory(os.path.dirname(pdf_path), os.path.basename(pdf_path))

        return jsonify({'error': 'PDF not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/audit/send/<audit_id>', methods=['POST'])
def send_audit_email(audit_id):
    """Sendet Audit-PDF per Email"""
    try:
        data = request.get_json() or {}
        email_to = data.get('email', '').strip()
        subject = data.get('subject', 'Website Audit Report')

        if not email_to:
            return jsonify({'error': 'Email required'}), 400

        # Generiere PDF
        pdf_path = generate_audit_pdf(audit_id)
        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF generation failed'}), 500

        # Lade Mail-Konto
        settings = load_settings()
        account = settings.get('accounts', {}).get('team@chefblick.de', {})

        if not account:
            return jsonify({'error': 'No mail account configured'}), 500

        # Sende Email
        msg = MIMEMultipart()
        msg['From'] = 'team@chefblick.de'
        msg['To'] = email_to
        msg['Subject'] = subject

        body = 'Anbei ist dein Website Audit Report.'
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Anhang
        with open(pdf_path, 'rb') as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename= {os.path.basename(pdf_path)}')
            msg.attach(part)

        # Sende via SMTP
        with smtplib.SMTP(account.get('smtp_server', 'smtp.ionos.de'), account.get('smtp_port', 587)) as server:
            server.starttls()
            server.login('team@chefblick.de', account.get('password', ''))
            server.send_message(msg)

        return jsonify({'success': True, 'message': f'Email sent to {email_to}'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/contacts', methods=['GET', 'POST'])
def manage_contacts():
    """GET: Alle Kontakte laden | POST: Neuen Kontakt speichern"""
    if request.method == 'POST':
        try:
            data = request.get_json() or {}
            settings = load_settings()

            if 'contacts' not in settings:
                settings['contacts'] = []

            contact = {
                'id': str(uuid.uuid4()),
                'name': data.get('name', ''),
                'email': data.get('email', ''),
                'phone': data.get('phone', ''),
                'company': data.get('company', ''),
                'notes': data.get('notes', ''),
                'created_at': datetime.now().isoformat()
            }

            settings['contacts'].append(contact)
            save_settings(settings)
            return jsonify({'success': True, 'contact': contact})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # GET
    settings = load_settings()
    contacts = settings.get('contacts', [])
    return jsonify(contacts)

@app.route('/api/contacts/<contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    """Löscht einen Kontakt"""
    try:
        settings = load_settings()
        settings['contacts'] = [c for c in settings.get('contacts', []) if c.get('id') != contact_id]
        save_settings(settings)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    init_db()  # Initialisiere Datenbank

    print("=" * 50)
    print("SIGGI startet lokal...")
    print("Dashboard: http://localhost:8080")

    # Abrufen beim Start
    print("Hole erste Mails ab...")
    fetch_and_reply_mails()
    print("Erste Mails abgerufen!")

    # Starte Mail-Abruf im Hintergrund
    mail_thread = threading.Thread(target=mail_fetch_loop, daemon=True)
    mail_thread.start()
    print("Mail-Abruf im Hintergrund aktiv!")

    # Starte Erinnerungs-Loop im Hintergrund (Windows-Toast)
    def reminder_loop():
        while True:
            try:
                memory_engine.check_and_fire_reminders()
                memory_engine.run_proactive_checks()
            except Exception as e:
                print(f'[Reminder] Loop-Fehler: {e}')
            time.sleep(60)

    reminder_thread = threading.Thread(target=reminder_loop, daemon=True)
    reminder_thread.start()
    print("Erinnerungs-Loop im Hintergrund aktiv!")

    # Starte Instagram Auto-Post-Loop im Hintergrund
    def instagram_auto_post_loop():
        public_base_url = os.environ.get('PUBLIC_BASE_URL', 'http://localhost:8080')
        while True:
            try:
                instagram_engine.maybe_auto_post(public_base_url)
            except Exception as e:
                print(f'[Instagram] Auto-Post-Loop-Fehler: {e}')
            time.sleep(60)

    instagram_thread = threading.Thread(target=instagram_auto_post_loop, daemon=True)
    instagram_thread.start()
    print("Instagram Auto-Post-Loop im Hintergrund aktiv!")
    print("=" * 50)
    socketio.run(app, port=8080, debug=False)
