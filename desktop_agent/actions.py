# -*- coding: utf-8 -*-
"""
Aktions-Handler für den Siggi Desktop-Agent.
Jede Funktion nimmt ein params-dict entgegen und gibt {'ok': bool, 'data': ...} oder
{'ok': False, 'error': ...} zurück.
"""
import os
import base64
import subprocess
import time

import mss
import pyautogui


def _resolve_and_check(path, allowed_dirs):
    """Löst den Pfad auf und prüft, dass er innerhalb einer der erlaubten Ordner liegt."""
    abs_path = os.path.abspath(path)
    for allowed in allowed_dirs:
        allowed_abs = os.path.abspath(allowed)
        if abs_path == allowed_abs or abs_path.startswith(allowed_abs + os.sep):
            return abs_path
    raise PermissionError(f"Pfad '{abs_path}' liegt außerhalb der erlaubten Ordner.")


def screenshot(params, config):
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # Hauptmonitor
        img = sct.grab(monitor)
        png_bytes = mss.tools.to_png(img.rgb, img.size)
        b64 = base64.b64encode(png_bytes).decode('ascii')
    return {'ok': True, 'data': {'image_base64': b64, 'mime': 'image/png'}}


def list_dir(params, config):
    path = _resolve_and_check(params.get('path', ''), config['allowed_dirs'])
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        entries.append({'name': name, 'is_dir': os.path.isdir(full)})
    return {'ok': True, 'data': entries}


def read_file(params, config):
    path = _resolve_and_check(params.get('path', ''), config['allowed_dirs'])
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return {'ok': True, 'data': content}


def write_file(params, config):
    path = _resolve_and_check(params.get('path', ''), config['allowed_dirs'])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(params.get('content', ''))
    return {'ok': True, 'data': f'Gespeichert: {path}'}


def open_app(params, config):
    name = params.get('name', '')
    if not name:
        return {'ok': False, 'error': 'Kein Programmname angegeben.'}
    try:
        os.startfile(name)
    except OSError as e:
        return {'ok': False, 'error': f'Konnte "{name}" nicht starten: {e}'}
    return {'ok': True, 'data': f'Gestartet: {name}'}


def close_app(params, config):
    name = params.get('name', '')
    if not name:
        return {'ok': False, 'error': 'Kein Programmname angegeben.'}
    subprocess.run(['taskkill', '/IM', name, '/F'], capture_output=True)
    return {'ok': True, 'data': f'Beendet: {name}'}


def office_write(params, config):
    """Erstellt ein Word-Dokument über COM und speichert es unter save_path (muss whitelisted sein)."""
    save_path = _resolve_and_check(params.get('save_path', ''), config['allowed_dirs'])
    text = params.get('text', '')
    app_name = params.get('app', 'Word')

    if app_name.lower() != 'word':
        return {'ok': False, 'error': f"Office-App '{app_name}' wird aktuell nicht unterstützt (nur Word)."}

    import win32com.client
    word = win32com.client.Dispatch('Word.Application')
    word.Visible = True
    try:
        doc = word.Documents.Add()
        doc.Content.Text = text
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        doc.SaveAs(save_path)
    finally:
        pass  # Word bleibt offen (Visible=True), damit der Nutzer das Ergebnis sieht
    return {'ok': True, 'data': f'Dokument gespeichert: {save_path}'}


def click(params, config):
    x, y = params.get('x'), params.get('y')
    if x is None or y is None:
        return {'ok': False, 'error': 'x/y fehlt.'}
    pyautogui.click(x, y)
    return {'ok': True, 'data': f'Geklickt bei ({x}, {y})'}


def type_text(params, config):
    text = params.get('text', '')
    time.sleep(0.3)  # kurze Verzögerung, damit das Zielfenster fokussiert ist
    pyautogui.typewrite(text, interval=0.02)
    return {'ok': True, 'data': 'Text eingegeben.'}


HANDLERS = {
    'screenshot': screenshot,
    'list_dir': list_dir,
    'read_file': read_file,
    'write_file': write_file,
    'open_app': open_app,
    'close_app': close_app,
    'office_write': office_write,
    'click': click,
    'type_text': type_text,
}


def run_action(action, params, config):
    handler = HANDLERS.get(action)
    if not handler:
        return {'ok': False, 'error': f'Unbekannte Aktion: {action}'}
    try:
        return handler(params, config)
    except PermissionError as e:
        return {'ok': False, 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'error': f'Fehler bei {action}: {e}'}
