# -*- coding: utf-8 -*-
"""
Desktop-Agent Engine für SIGGI
Verwaltet die Socket.IO-Verbindung zu einem lokalen Companion-Agent (auf Stefans PC),
der Desktop-Aktionen ausführt: Dateien lesen/schreiben, Screenshots, Programme öffnen,
Office-Automatisierung, Maus/Tastatur-Steuerung.

Kommunikationsmodell:
  - Der lokale Agent baut eine ausgehende Socket.IO-Verbindung zum Server auf.
  - Der Server schickt Requests ("desktop:command") und wartet per Event auf die Antwort
    ("desktop:result"), korreliert über eine request_id.
  - Riskante Aktionen (Datei schreiben, Programm starten, Maus/Tastatur) werden nicht
    sofort ausgeführt, sondern als "pending action" gespeichert und müssen im Chat/UI
    bestätigt werden, bevor sie an den Agent geschickt werden.
"""
import os
import json
import time
import uuid
import hashlib
import secrets
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

RISKY_ACTIONS = {'write_file', 'open_app', 'office_write', 'click', 'type_text', 'close_app'}
SAFE_ACTIONS = {'screenshot', 'read_file', 'list_dir'}
ALL_ACTIONS = RISKY_ACTIONS | SAFE_ACTIONS

COMMAND_TIMEOUT = 30  # Sekunden, wie lange auf eine Antwort vom Agent gewartet wird
PENDING_ACTION_TTL = 600  # Sekunden, wie lange eine unbestätigte Aktion gültig bleibt

# ─── State ──────────────────────────────────────────────────────────
_socketio = None  # wird von app.py per init() gesetzt
_connected_sid = None  # aktuelle Socket.IO Session-ID des verbundenen Agents
_lock = threading.Lock()

_pending_requests = {}   # request_id -> {'event': threading.Event, 'result': ...}
_pending_actions = {}    # action_id -> {'action', 'params', 'created', 'description'}


def _load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def init(socketio):
    """Registriert die Socket.IO-Handler. Wird einmal beim App-Start aus app.py aufgerufen."""
    global _socketio
    _socketio = socketio

    @socketio.on('connect', namespace='/desktop-agent')
    def _on_connect(auth):
        token = (auth or {}).get('token', '')
        if not token or not _verify_token(token):
            return False  # Verbindung ablehnen
        global _connected_sid
        from flask import request as flask_request
        with _lock:
            _connected_sid = flask_request.sid
        print('[Desktop] Agent verbunden.')

    @socketio.on('disconnect', namespace='/desktop-agent')
    def _on_disconnect():
        global _connected_sid
        from flask import request as flask_request
        with _lock:
            if _connected_sid == flask_request.sid:
                _connected_sid = None
        print('[Desktop] Agent getrennt.')

    @socketio.on('desktop:result', namespace='/desktop-agent')
    def _on_result(data):
        request_id = data.get('request_id')
        entry = _pending_requests.get(request_id)
        if entry:
            entry['result'] = data
            entry['event'].set()


# ─── Pairing / Token ────────────────────────────────────────────────

def generate_pairing_token():
    """Erzeugt einen neuen Device-Token, speichert dessen Hash in settings.json
    und gibt den Klartext-Token einmalig zurück (wie bei API-Keys üblich)."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    settings = _load_settings()
    settings['desktop_agent_token_hash'] = token_hash
    _save_settings(settings)
    return token


def revoke_pairing_token():
    settings = _load_settings()
    settings.pop('desktop_agent_token_hash', None)
    _save_settings(settings)


def _verify_token(token):
    settings = _load_settings()
    stored_hash = settings.get('desktop_agent_token_hash')
    if not stored_hash or not token:
        return False
    return hashlib.sha256(token.encode()).hexdigest() == stored_hash


def is_paired():
    return bool(_load_settings().get('desktop_agent_token_hash'))


def is_agent_connected():
    with _lock:
        return _connected_sid is not None


# ─── Command-Ausführung ─────────────────────────────────────────────

def _send_command(action, params):
    """Schickt ein Kommando an den verbundenen Agent und wartet auf die Antwort."""
    if not is_agent_connected():
        return {'ok': False, 'error': 'Kein Desktop-Agent verbunden.'}

    request_id = str(uuid.uuid4())
    event = threading.Event()
    _pending_requests[request_id] = {'event': event, 'result': None}

    _socketio.emit(
        'desktop:command',
        {'request_id': request_id, 'action': action, 'params': params},
        namespace='/desktop-agent',
        to=_connected_sid
    )

    got_result = event.wait(COMMAND_TIMEOUT)
    entry = _pending_requests.pop(request_id, None)
    if not got_result or not entry or entry['result'] is None:
        return {'ok': False, 'error': 'Zeitüberschreitung - keine Antwort vom Desktop-Agent.'}
    return entry['result']


def execute_action(action, params):
    """Führt eine unkritische Aktion (SAFE_ACTIONS) sofort aus."""
    if action not in ALL_ACTIONS:
        return {'ok': False, 'error': f'Unbekannte Aktion: {action}'}
    return _send_command(action, params)


def describe_action(action, params):
    """Menschlich lesbare Beschreibung für die Bestätigungskarte im Chat."""
    if action == 'write_file':
        return f"Datei schreiben: {params.get('path')}"
    if action == 'open_app':
        return f"Programm öffnen: {params.get('name')}"
    if action == 'office_write':
        return f"{params.get('app', 'Office')}-Dokument erstellen und speichern unter: {params.get('save_path')}"
    if action == 'click':
        return f"Mausklick bei ({params.get('x')}, {params.get('y')})"
    if action == 'type_text':
        return 'Text per Tastatur eingeben'
    if action == 'close_app':
        return f"Programm schließen: {params.get('name')}"
    return f"Aktion: {action}"


def create_pending_action(action, params):
    """Speichert eine riskante Aktion zur Bestätigung statt sie sofort auszuführen."""
    if action not in RISKY_ACTIONS:
        return None
    action_id = str(uuid.uuid4())
    _pending_actions[action_id] = {
        'action': action,
        'params': params,
        'created': time.time(),
        'description': describe_action(action, params)
    }
    return action_id


def get_pending_action(action_id):
    entry = _pending_actions.get(action_id)
    if not entry:
        return None
    if time.time() - entry['created'] > PENDING_ACTION_TTL:
        _pending_actions.pop(action_id, None)
        return None
    return entry


def confirm_pending_action(action_id):
    entry = _pending_actions.pop(action_id, None)
    if not entry:
        return {'ok': False, 'error': 'Aktion nicht gefunden oder abgelaufen.'}
    return _send_command(entry['action'], entry['params'])


def cancel_pending_action(action_id):
    entry = _pending_actions.pop(action_id, None)
    return {'ok': bool(entry)}
