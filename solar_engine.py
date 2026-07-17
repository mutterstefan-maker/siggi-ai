# -*- coding: utf-8 -*-
"""
Solarman Cloud Engine für SIGGI
Liest Echtzeit-Daten vom Balkonkraftwerk via Solarman Global API
Docs: https://developers.solarmanpv.com/
"""
import os
import json
import hashlib
import requests as req
from datetime import datetime

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
BASE_URL = 'https://globalapi.solarmanpv.com'

# App-ID aus der offiziellen Solarman-App (öffentlich bekannt, genutzt von HA-Community)
_DEFAULT_APP_ID     = '2000000118'
_DEFAULT_APP_SECRET = 'Y95jqDa4lIeEVU59MDkFc26C4JIeSFaH'

_token_cache = {}


def _load_settings():
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _get_config():
    s = _load_settings()
    return {
        'app_id':     s.get('solar_app_id', '') or _DEFAULT_APP_ID,
        'app_secret': s.get('solar_app_secret', '') or _DEFAULT_APP_SECRET,
        'email':      s.get('solar_email', ''),
        'password':   s.get('solar_password', ''),
    }


def _get_token():
    import time
    cfg = _get_config()
    if not cfg['email'] or not cfg['password']:
        print('[Solar] Keine E-Mail/Passwort in settings.json (solar_email, solar_password)')
        return None
    if _token_cache.get('token') and time.time() < _token_cache.get('expires', 0) - 60:
        return _token_cache['token']
    try:
        pw_hash = hashlib.sha256(cfg['password'].encode()).hexdigest().lower()
        r = req.post(
            f'{BASE_URL}/account/v1.0/token',
            params={'appId': cfg['app_id'], 'language': 'de'},
            json={'appSecret': cfg['app_secret'], 'email': cfg['email'], 'password': pw_hash},
            timeout=15
        )
        data = r.json()
        if not r.ok or str(data.get('code', '')) not in ('', 'None', '0', '200'):
            print(f'[Solar] Auth-Fehler: {data}')
            return None
        token = data.get('access_token') or (data.get('data') or {}).get('access_token')
        _token_cache['token']   = token
        _token_cache['expires'] = time.time() + int(data.get('expires_in', 7200))
        return token
    except Exception as e:
        print(f'[Solar] Token-Fehler: {e}')
        return None


def _auth_headers():
    token = _get_token()
    if not token:
        return None
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


# ──────────────────────────────────────────────
# STATION (ANLAGE)
# ──────────────────────────────────────────────

def get_stations():
    """Gibt alle Anlagen des Accounts zurück."""
    h = _auth_headers()
    if not h:
        return []
    try:
        r = req.post(f'{BASE_URL}/station/v1.0/list', headers=h,
                     json={'page': 1, 'size': 10}, timeout=15)
        data = r.json()
        return data.get('stationList') or data.get('data', {}).get('stationList', [])
    except Exception as e:
        print(f'[Solar] Stations-Fehler: {e}')
        return []


def get_station_realtime(station_id):
    """Echtzeit-Daten einer Anlage."""
    h = _auth_headers()
    if not h:
        return None
    try:
        r = req.post(f'{BASE_URL}/station/v1.0/realTime', headers=h,
                     json={'stationId': station_id}, timeout=15)
        data = r.json()
        return data.get('data') or data if r.ok else None
    except Exception as e:
        print(f'[Solar] Realtime-Fehler: {e}')
        return None


def get_station_today(station_id):
    """Heutiger Ertrag einer Anlage."""
    h = _auth_headers()
    if not h:
        return None
    try:
        r = req.post(f'{BASE_URL}/station/v1.0/energy', headers=h,
                     json={'stationId': station_id,
                           'startTime': datetime.now().strftime('%Y-%m-%d'),
                           'endTime':   datetime.now().strftime('%Y-%m-%d'),
                           'timeType': 1},
                     timeout=15)
        return r.json().get('data') if r.ok else None
    except Exception as e:
        print(f'[Solar] Energy-Fehler: {e}')
        return None


# ──────────────────────────────────────────────
# DASHBOARD / API
# ──────────────────────────────────────────────

def get_device_info():
    """Kompakte Echtzeit-Übersicht der ersten Anlage für die /api/solar/status Route."""
    cfg = _get_config()
    if not all([cfg['app_id'], cfg['app_secret'], cfg['email']]):
        return None
    stations = get_stations()
    if not stations:
        return None
    st = stations[0]
    st_id = st.get('id') or st.get('stationId')
    name = st.get('name', 'Anlage')
    rt = get_station_realtime(st_id) if st_id else None
    if not rt:
        return {'name': name, 'connected': False}
    return {
        'name': name,
        'connected': True,
        'power_w': float(rt.get('generationPower') or 0),
        'total_kwh': float(rt.get('generationTotal') or 0),
        'last_update': rt.get('lastUpdateTime')
    }


# ──────────────────────────────────────────────
# SIGGI KONTEXT
# ──────────────────────────────────────────────

def get_solar_context():
    """Kompakte Solar-Zusammenfassung für SIGGIs System-Prompt."""
    cfg = _get_config()
    if not all([cfg['app_id'], cfg['app_secret'], cfg['email']]):
        return ''
    try:
        stations = get_stations()
        if not stations:
            return 'BALKONKRAFTWERK: Keine Anlage gefunden oder Verbindungsfehler.'
        lines = ['BALKONKRAFTWERK (Solarman):']
        for st in stations[:2]:
            st_id = st.get('id') or st.get('stationId')
            name  = st.get('name', 'Anlage')
            rt    = get_station_realtime(st_id) if st_id else None
            if rt:
                power_w   = float(rt.get('generationPower') or 0)
                total_kwh = float(rt.get('generationTotal') or 0)
                last_upd  = rt.get('lastUpdateTime')
                status    = 'aktiv ☀️' if power_w > 0 else 'inaktiv 🌙'
                upd_str   = ''
                if last_upd:
                    from datetime import datetime as _dt
                    upd_str = f' | Letzte Messung: {_dt.fromtimestamp(last_upd).strftime("%H:%M Uhr")}'
                lines.append(
                    f'  {name}: {status} | Aktuell: {power_w} W | '
                    f'Gesamt erzeugt: {total_kwh} kWh{upd_str}'
                )
            else:
                lines.append(f'  {name}: Keine Echtzeit-Daten')
        lines.append('  (Tagesertraege: im Free-Plan nicht per API abrufbar)')
        return '\n'.join(lines)
    except Exception as e:
        return f'BALKONKRAFTWERK: Fehler ({e})'
