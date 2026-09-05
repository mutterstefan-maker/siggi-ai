"""Taeglicher Selbsttest fuer Siggi/STEAN.

Prueft die wichtigsten Bausteine (Settings-Datei, Anthropic/OpenAI-Keys,
Instagram/LinkedIn-Verbindung, Mail-Konten, systemd-Services) und schickt
eine Alarm-Mail an Stefan, sobald etwas kaputt ist - genau die Art von
Ausfall (korrupte settings.json, abgelaufener Instagram-Token), die sonst
erst auffiel wenn Stefan es zufaellig bemerkte.

WICHTIG: die Alarm-Mail wird bewusst NICHT ueber settings.json verschickt,
sondern direkt ueber die SMTP-Zugangsdaten aus config/.env - sonst wuerde
ausgerechnet der Fall "settings.json ist kaputt" auch die Alarmierung
lahmlegen.
"""
import json
import os
import smtplib
import sqlite3
import subprocess
import imaplib
from datetime import datetime
from email.message import EmailMessage

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
DB_PATH = os.path.join(BASE_DIR, 'mails.db')

load_dotenv(os.path.join(BASE_DIR, 'config', '.env'))

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def init_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS health_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        ok INTEGER,
        results TEXT
    )''')
    conn.commit()
    conn.close()


@check('settings.json')
def _c_settings(settings):
    if settings is None:
        return False, 'settings.json ist kein gueltiges JSON mehr (Datei korrupt!). Dringend pruefen.'
    return True, 'OK'


@check('Anthropic API (Chat, Mails, LinkedIn-Texte)')
def _c_anthropic(settings):
    key = os.environ.get('ANTHROPIC_API_KEY') or (settings or {}).get('anthropic_api_key', '')
    if not key:
        return False, 'Kein Anthropic-Key hinterlegt'
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 5, 'messages': [{'role': 'user', 'content': 'hi'}]},
            timeout=20,
        )
        if r.status_code == 200:
            return True, 'OK'
        return False, f'API-Fehler {r.status_code}: {r.text[:150]}'
    except Exception as e:
        return False, f'Verbindung fehlgeschlagen: {e}'


@check('OpenAI API (Bildgenerierung)')
def _c_openai(settings):
    key = (settings or {}).get('openai_api_key', '')
    if not key:
        return False, 'Kein OpenAI-Key hinterlegt'
    try:
        # bewusst ein winziger ECHTER Billing-Call (nicht /v1/models - das
        # funktioniert auch bei 0 Guthaben, weil reines Auflisten nichts kostet
        # und daher ein leeres Konto nicht erkennen wuerde)
        r = requests.post(
            'https://api.openai.com/v1/embeddings',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={'model': 'text-embedding-3-small', 'input': 'health-check'},
            timeout=20,
        )
        if r.status_code == 200:
            return True, 'OK'
        if 'insufficient_quota' in r.text or 'credit_balance_exhausted' in r.text:
            return False, 'OpenAI-Guthaben aufgebraucht - Bildgenerierung betroffen, alles andere laeuft weiter'
        return False, f'API-Fehler {r.status_code}: {r.text[:150]}'
    except Exception as e:
        return False, f'Verbindung fehlgeschlagen: {e}'


@check('Instagram-Verbindung')
def _c_instagram(settings):
    ig = (settings or {}).get('instagram_settings', {})
    token = ig.get('access_token', '')
    ig_id = ig.get('ig_user_id', '')
    if not token or not ig_id:
        return False, 'Access-Token oder Business-Account-ID fehlt in den Einstellungen'
    try:
        r = requests.get(
            f'https://graph.facebook.com/v19.0/{ig_id}',
            params={'fields': 'username', 'access_token': token}, timeout=20,
        )
        if r.status_code == 200:
            return True, 'OK'
        return False, f'Token ungueltig/abgelaufen: {r.text[:150]}'
    except Exception as e:
        return False, f'Verbindung fehlgeschlagen: {e}'


@check('LinkedIn-Verbindung')
def _c_linkedin(settings):
    try:
        import linkedin_engine
        if linkedin_engine.is_connected():
            return True, 'OK'
        return False, 'Nicht verbunden oder Token abgelaufen'
    except Exception as e:
        return False, f'Check fehlgeschlagen: {e}'


@check('Mail-Konten (IMAP-Login)')
def _c_mail(settings):
    accounts = (settings or {}).get('accounts', {})
    if not accounts:
        return False, 'Keine Mail-Konten in den Einstellungen konfiguriert'
    problems = []
    for addr, cfg in accounts.items():
        if not cfg.get('active', True):
            continue
        try:
            m = imaplib.IMAP4_SSL(cfg.get('imap_server', ''))
            m.login(addr, cfg.get('password', ''))
            m.logout()
        except Exception as e:
            problems.append(f'{addr}: {e}')
    if problems:
        return False, '; '.join(problems)
    return True, 'OK'


def _service_active(unit):
    try:
        out = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=10)
        status = out.stdout.strip()
        return status == 'active', status
    except Exception as e:
        return False, str(e)


@check('stean.service (Web-App)')
def _c_stean_service(settings):
    ok, status = _service_active('stean.service')
    return ok, ('OK' if ok else f'Service-Status: {status}')


@check('stean-mail-loop.service (Mail-Abruf/Auto-Antwort)')
def _c_mailloop_service(settings):
    ok, status = _service_active('stean-mail-loop.service')
    return ok, ('OK' if ok else f'Service-Status: {status}')


def _send_alert_mail(failed):
    host = os.environ.get('SMTP_HOST')
    port = int(os.environ.get('SMTP_PORT', 587))
    user = os.environ.get('SMTP_USER')
    pw = os.environ.get('SMTP_PASSWORD')
    to_addr = os.environ.get('MAIL_USER_2') or user
    if not (host and user and pw):
        print('Alarm-Mail nicht gesendet: SMTP-Zugangsdaten fehlen in .env')
        return

    lines = '\n'.join(f"- {name}: {detail}" for name, detail in failed)
    body = (
        f"Der taegliche Siggi-Health-Check hat {len(failed)} Problem(e) gefunden:\n\n{lines}\n\n"
        f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        "Details im Dashboard unter 'Health-Check'."
    )
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = f'SIGGI ALARM: {len(failed)} Problem(e) beim Health-Check'
    msg['From'] = user
    msg['To'] = to_addr
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, pw)
            server.send_message(msg)
        print('Alarm-Mail gesendet an', to_addr)
    except Exception as e:
        print('FEHLER beim Senden der Alarm-Mail:', e)


def run_health_check(send_alert=True):
    init_table()
    settings = load_settings()
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn(settings)
        except Exception as e:
            ok, detail = False, f'Check selbst abgestuerzt: {e}'
        results.append({'name': name, 'ok': ok, 'detail': detail})
        print(('OK  ' if ok else 'FAIL') + f' - {name}: {detail}')

    all_ok = all(r['ok'] for r in results)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO health_checks (ok, results) VALUES (?, ?)', (int(all_ok), json.dumps(results, ensure_ascii=False)))
    conn.commit()
    conn.close()

    failed = [(r['name'], r['detail']) for r in results if not r['ok']]
    if failed and send_alert:
        _send_alert_mail(failed)

    return {'ok': all_ok, 'results': results}


def get_latest():
    init_table()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, created_at, ok, results FROM health_checks ORDER BY created_at DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {'id': row[0], 'created_at': row[1], 'ok': bool(row[2]), 'results': json.loads(row[3])}


def get_history(limit=20):
    init_table()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, created_at, ok, results FROM health_checks ORDER BY created_at DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'created_at': r[1], 'ok': bool(r[2]), 'results': json.loads(r[3])} for r in rows]


if __name__ == '__main__':
    run_health_check()
