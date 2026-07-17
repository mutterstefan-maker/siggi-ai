# -*- coding: utf-8 -*-
"""
Google Search Console Engine für SIGGI
Liest Keywords, Klicks, Impressionen, CTR und Position für chefblick.de
"""
import os
import json
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, 'google_service_account.json')
SITE_URL = 'sc-domain:chefblick.de'  # oder 'https://www.chefblick.de/'


def _get_service():
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=['https://www.googleapis.com/auth/webmasters.readonly']
        )
        return build('searchconsole', 'v1', credentials=creds)
    except Exception as e:
        print(f'[GSC] Verbindungsfehler: {e}')
        return None


def get_top_keywords(days=28, limit=10):
    """Gibt die Top-Keywords der letzten X Tage zurück."""
    service = _get_service()
    if not service:
        return []
    try:
        end = datetime.now().date()
        start = end - timedelta(days=days)
        resp = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={
                'startDate': str(start),
                'endDate': str(end),
                'dimensions': ['query'],
                'rowLimit': limit,
                'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}]
            }
        ).execute()
        rows = resp.get('rows', [])
        return [
            {
                'keyword': r['keys'][0],
                'clicks': r.get('clicks', 0),
                'impressions': r.get('impressions', 0),
                'ctr': round(r.get('ctr', 0) * 100, 1),
                'position': round(r.get('position', 0), 1)
            }
            for r in rows
        ]
    except Exception as e:
        print(f'[GSC] Fehler bei Keywords: {e}')
        return []


def get_overview(days=28):
    """Gibt Gesamt-Klicks, Impressionen, CTR und Position zurück."""
    service = _get_service()
    if not service:
        return None
    try:
        end = datetime.now().date()
        start = end - timedelta(days=days)
        resp = service.searchanalytics().query(
            siteUrl=SITE_URL,
            body={
                'startDate': str(start),
                'endDate': str(end),
                'dimensions': ['date'],
                'rowLimit': 1000
            }
        ).execute()
        rows = resp.get('rows', [])
        if not rows:
            return None
        total_clicks = sum(r.get('clicks', 0) for r in rows)
        total_imp = sum(r.get('impressions', 0) for r in rows)
        avg_ctr = (total_clicks / total_imp * 100) if total_imp else 0
        avg_pos = sum(r.get('position', 0) for r in rows) / len(rows) if rows else 0
        return {
            'clicks': int(total_clicks),
            'impressions': int(total_imp),
            'ctr': round(avg_ctr, 1),
            'position': round(avg_pos, 1),
            'days': days
        }
    except Exception as e:
        print(f'[GSC] Fehler bei Übersicht: {e}')
        return None


def get_gsc_context():
    """Für SIGGIs System-Prompt: kompakte Search Console Zusammenfassung."""
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return ''
    try:
        overview = get_overview(28)
        keywords = get_top_keywords(28, 5)
        if not overview:
            return 'SEARCH CONSOLE: Keine Daten verfügbar.'
        lines = [f'GOOGLE SEARCH CONSOLE (letzte 28 Tage chefblick.de):',
                 f'  Klicks: {overview["clicks"]} | Impressionen: {overview["impressions"]} | CTR: {overview["ctr"]}% | Ø Position: {overview["position"]}']
        if keywords:
            kw_str = ', '.join(f'{k["keyword"]} ({k["clicks"]} Klicks)' for k in keywords[:5])
            lines.append(f'  Top-Keywords: {kw_str}')
        return '\n'.join(lines)
    except Exception as e:
        return f'SEARCH CONSOLE: Fehler ({e})'
