# -*- coding: utf-8 -*-
"""
Google Analytics 4 Engine für SIGGI
Liest Traffic, Sessions, Nutzer und Conversions für chefblick.de
"""
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, 'google_service_account.json')

# GA4 Property ID — im Format '123456789' (ohne 'properties/')
# Zu finden in GA4: Admin → Property → Property-Details
GA4_PROPERTY_ID = ''  # wird aus settings.json geladen falls vorhanden


def _get_property_id():
    try:
        import json
        settings_path = os.path.join(BASE_DIR, 'settings.json')
        with open(settings_path, 'r', encoding='utf-8') as f:
            s = json.load(f)
        return s.get('ga4_property_id', GA4_PROPERTY_ID)
    except Exception:
        return GA4_PROPERTY_ID


def _get_client():
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return None
    try:
        from google.oauth2 import service_account
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=['https://www.googleapis.com/auth/analytics.readonly']
        )
        return BetaAnalyticsDataClient(credentials=creds)
    except Exception as e:
        print(f'[GA4] Verbindungsfehler: {e}')
        return None


def get_overview(days=28):
    """Sessions, Nutzer, Seitenaufrufe der letzten X Tage."""
    client = _get_client()
    property_id = _get_property_id()
    if not client or not property_id:
        return None
    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Metric
        )
        request = RunReportRequest(
            property=f'properties/{property_id}',
            date_ranges=[DateRange(start_date=f'{days}daysAgo', end_date='today')],
            metrics=[
                Metric(name='sessions'),
                Metric(name='totalUsers'),
                Metric(name='screenPageViews'),
                Metric(name='bounceRate'),
                Metric(name='averageSessionDuration'),
            ]
        )
        resp = client.run_report(request)
        if not resp.rows:
            return None
        row = resp.rows[0]
        vals = [mv.value for mv in row.metric_values]
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
    client = _get_client()
    property_id = _get_property_id()
    if not client or not property_id:
        return []
    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric, OrderBy
        )
        request = RunReportRequest(
            property=f'properties/{property_id}',
            date_ranges=[DateRange(start_date=f'{days}daysAgo', end_date='today')],
            dimensions=[Dimension(name='pagePath')],
            metrics=[Metric(name='screenPageViews')],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name='screenPageViews'), desc=True)],
            limit=limit
        )
        resp = client.run_report(request)
        return [
            {'page': r.dimension_values[0].value, 'views': int(r.metric_values[0].value)}
            for r in resp.rows
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
