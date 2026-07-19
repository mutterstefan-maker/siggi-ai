# -*- coding: utf-8 -*-
"""
Google Analytics 4 Engine für SIGGI
Liest Traffic, Sessions, Nutzer und Conversions für chefblick.de über die
GA4 Data API (reines REST, kein google-analytics-data SDK nötig).
"""
import os
import json
import urllib.request
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, 'siggi-dashboard-ac0baeaaaef6.json')
GA4_API_URL = 'https://analyticsdata.googleapis.com/v1beta/properties/{}:runReport'

GA4_PROPERTY_ID = ''  # wird aus settings.json geladen falls vorhanden


def _get_property_id():
    try:
        settings_path = os.path.join(BASE_DIR, 'settings.json')
        with open(settings_path, 'r', encoding='utf-8') as f:
            s = json.load(f)
        return s.get('ga4_property_id', GA4_PROPERTY_ID)
    except Exception:
        return GA4_PROPERTY_ID


def _get_access_token():
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return None
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=['https://www.googleapis.com/auth/analytics.readonly']
        )
        creds.refresh(Request())
        return creds.token
    except Exception as e:
        print(f'[GA4] Verbindungsfehler: {e}')
        return None


def _run_report(property_id, body):
    token = _get_access_token()
    if not token:
        return None
    req = urllib.request.Request(
        GA4_API_URL.format(property_id),
        data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_overview(days=28):
    """Sessions, Nutzer, Seitenaufrufe der letzten X Tage."""
    property_id = _get_property_id()
    if not property_id:
        return None
    try:
        body = {
            'dateRanges': [{'startDate': f'{days}daysAgo', 'endDate': 'today'}],
            'metrics': [
                {'name': 'sessions'},
                {'name': 'totalUsers'},
                {'name': 'screenPageViews'},
                {'name': 'bounceRate'},
                {'name': 'averageSessionDuration'},
            ]
        }
        resp = _run_report(property_id, body)
        if not resp or not resp.get('rows'):
            return None
        vals = [mv['value'] for mv in resp['rows'][0]['metricValues']]
        return {
            'sessions': int(float(vals[0])),
            'users': int(float(vals[1])),
            'pageviews': int(float(vals[2])),
            'bounce_rate': round(float(vals[3]) * 100, 1),
            'avg_duration': int(float(vals[4])),
            'days': days
        }
    except Exception as e:
        print(f'[GA4] Fehler bei Übersicht: {e}')
        return None


def get_top_pages(days=28, limit=5):
    """Top-Seiten nach Aufrufen."""
    property_id = _get_property_id()
    if not property_id:
        return []
    try:
        body = {
            'dateRanges': [{'startDate': f'{days}daysAgo', 'endDate': 'today'}],
            'dimensions': [{'name': 'pagePath'}],
            'metrics': [{'name': 'screenPageViews'}],
            'orderBys': [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}],
            'limit': limit
        }
        resp = _run_report(property_id, body)
        if not resp:
            return []
        return [
            {'page': r['dimensionValues'][0]['value'], 'views': int(r['metricValues'][0]['value'])}
            for r in resp.get('rows', [])
        ]
    except Exception as e:
        print(f'[GA4] Fehler bei Top-Seiten: {e}')
        return []


def get_ga4_context():
    """Für SIGGIs System-Prompt: kompakte GA4-Zusammenfassung."""
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return ''
    property_id = _get_property_id()
    if not property_id:
        return 'GOOGLE ANALYTICS: Property-ID fehlt (ga4_property_id in settings.json eintragen).'
    try:
        overview = get_overview(28)
        pages = get_top_pages(28, 3)
        if not overview:
            return 'GOOGLE ANALYTICS: Keine Daten verfügbar.'
        dur_min = overview['avg_duration'] // 60
        dur_sec = overview['avg_duration'] % 60
        lines = [
            f'GOOGLE ANALYTICS GA4 (letzte 28 Tage chefblick.de):',
            f'  Sessions: {overview["sessions"]} | Nutzer: {overview["users"]} | Seitenaufrufe: {overview["pageviews"]}',
            f'  Absprungrate: {overview["bounce_rate"]}% | Ø Sitzungsdauer: {dur_min}m {dur_sec}s'
        ]
        if pages:
            pg_str = ', '.join(f'{p["page"]} ({p["views"]}x)' for p in pages)
            lines.append(f'  Top-Seiten: {pg_str}')
        return '\n'.join(lines)
    except Exception as e:
        return f'GOOGLE ANALYTICS: Fehler ({e})'
