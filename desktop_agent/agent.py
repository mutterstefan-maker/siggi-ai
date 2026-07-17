# -*- coding: utf-8 -*-
"""
Siggi Desktop-Agent
Läuft dauerhaft im Hintergrund auf Stefans PC, verbindet sich ausgehend per Socket.IO
mit dem Siggi-Server und führt von dort angeforderte Desktop-Aktionen aus.

Start: pythonw agent.py   (siehe install.py für Autostart-Einrichtung)
"""
import os
import sys
import json
import logging
import threading

import socketio

import actions

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'agent_config.json')
LOG_PATH = os.path.join(BASE_DIR, 'agent.log')

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('siggi-desktop-agent')

sio = socketio.Client(reconnection=True, reconnection_delay=2, reconnection_delay_max=30)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        logger.error(f'Config fehlt: {CONFIG_PATH}. Kopiere agent_config.example.json und trage Token/Server ein.')
        sys.exit(1)
    with open(CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


config = load_config()
status = {'connected': False}


@sio.event(namespace='/desktop-agent')
def connect():
    status['connected'] = True
    logger.info('Verbunden mit Siggi-Server.')


@sio.event(namespace='/desktop-agent')
def connect_error(data):
    logger.error(f'Verbindung fehlgeschlagen: {data}')


@sio.event(namespace='/desktop-agent')
def disconnect():
    status['connected'] = False
    logger.info('Verbindung zum Siggi-Server getrennt.')


@sio.on('desktop:command', namespace='/desktop-agent')
def on_command(data):
    request_id = data.get('request_id')
    action = data.get('action')
    params = data.get('params', {}) or {}
    logger.info(f'Kommando empfangen: {action} {params} (id={request_id})')

    result = actions.run_action(action, params, config)
    result['request_id'] = request_id
    sio.emit('desktop:result', result, namespace='/desktop-agent')

    if result.get('ok'):
        logger.info(f'Aktion erfolgreich: {action}')
    else:
        logger.warning(f"Aktion fehlgeschlagen: {action} - {result.get('error')}")


def connect_loop():
    try:
        sio.connect(
            config['server_url'],
            namespaces=['/desktop-agent'],
            auth={'token': config['device_token']}
        )
        sio.wait()
    except Exception as e:
        logger.error(f'Verbindungsfehler: {e}')


def start():
    thread = threading.Thread(target=connect_loop, daemon=True)
    thread.start()
    return thread


if __name__ == '__main__':
    logger.info('Siggi Desktop-Agent startet...')
    try:
        import tray
        tray.run(status, sio, start)
    except ImportError:
        # Fallback ohne Tray-Icon (z.B. wenn pystray nicht installiert ist)
        start()
        threading.Event().wait()
