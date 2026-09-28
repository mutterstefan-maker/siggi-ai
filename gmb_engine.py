# -*- coding: utf-8 -*-
"""
Google Business Profile Engine für SIGGI
Nutzt OAuth2 (Stefan Mutters Google-Konto) statt Service Account.
Einmalig-Setup: /api/gmb/auth aufrufen im Browser.
"""
import os
import json
import time
import requests as req
from datetime import datetime, timedelta
import settings_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

GMB_SCOPE    = 'https://www.googleapis.com/auth/business.manage'
ACCOUNT_API  = 'https://mybusinessaccountmanagement.googleapis.com/v1'
INFO_API     = 'https://mybusinessbusinessinformation.googleapis.com/v1'
V4_API       = 'https://mybusiness.googleapis.com/v4'
PERF_API     = 'https://businessprofileperformance.googleapis.com/v1'
TOKEN_URL    = 'https://oauth2.googleapis.com/token'

_cache = {}


# ──────────────────────────────────────────────
# AUTH / TOKEN
# ──────────────────────────────────────────────

def _load_settings():
    return settings_store.load()

def _save_settings(data):
    return settings_store.save(data)

def get_oauth_credentials():
    s = _load_settings()
    return s.get('gmb_client_id', ''), s.get('gmb_client_secret', '')

def get_stored_token():
    return _load_settings().get('gmb_token', {})

def save_token(token_data):
    s = _load_settings()
    s['gmb_token'] = token_data
    _save_settings(s)

def is_connected():
    return bool(get_stored_token().get('refresh_token'))

def _get_access_token():
    """Gibt gültiges Access Token zurück, refresht bei Bedarf."""
    token = get_stored_token()
    if not token.get('refresh_token'):
        return None
    client_id, client_secret = get_oauth_credentials()
    if not client_id or not client_secret:
        print('[GMB] Keine Client-ID/Secret in settings.json (gmb_client_id, gmb_client_secret)')
        return None
    # Noch gültig?
    if token.get('access_token') and time.time() < token.get('expires_at', 0) - 60:
        return token['access_token']
    # Refresh
    resp = req.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': token['refresh_token'],
        'grant_type': 'refresh_token'
    })
    if not resp.ok:
        print(f'[GMB] Token-Refresh fehlgeschlagen: {resp.text[:200]}')
        return None
    new = resp.json()
    token['access_token'] = new['access_token']
    token['expires_at'] = time.time() + new.get('expires_in', 3600)
    save_token(token)
    return token['access_token']

def _headers():
    at = _get_access_token()
    if not at:
        return None
    return {'Authorization': f'Bearer {at}', 'Content-Type': 'application/json'}


# ──────────────────────────────────────────────
# ACCOUNT / LOCATION LOOKUP
# ──────────────────────────────────────────────

def _save_account_to_settings(account_name):
    """Persistiert den Account-Namen in settings.json um API-Calls zu sparen."""
    try:
        settings_store.update(lambda s: s.__setitem__('gmb_account_name', account_name))
    except Exception as e:
        print(f'[GMB] Account-Speichern fehlgeschlagen: {e}')


def _get_account():
    # 1. In-Memory-Cache
    if 'account' in _cache:
        return _cache['account']
    # 2. Gespeicherter Wert in settings.json (kein API-Call nötig)
    try:
        s = _load_settings()
        saved = s.get('gmb_account_name')
        if saved:
            _cache['account'] = saved
            return saved
    except Exception:
        pass
    # 3. API-Call (nur wenn noch nicht gespeichert)
    h = _headers()
    if not h:
        return None
    r = req.get(f'{ACCOUNT_API}/accounts', headers=h)
    if not r.ok:
        print(f'[GMB] Accounts-Fehler: {r.status_code} {r.text[:200]}')
        return None
    accounts = r.json().get('accounts', [])
    if not accounts:
        return None
    _cache['account'] = accounts[0]['name']
    _save_account_to_settings(_cache['account'])  # für nächsten Neustart
    return _cache['account']

def _get_location():
    if 'location' in _cache:
        return _cache['location']
    account = _get_account()
    if not account:
        return None
    h = _headers()
    if not h:
        return None
    r = req.get(f'{INFO_API}/{account}/locations',
                headers=h, params={'readMask': 'name,title'})
    if not r.ok:
        print(f'[GMB] Locations-Fehler: {r.status_code} {r.text[:200]}')
        return None
    locs = r.json().get('locations', [])
    if not locs:
        return None
    loc_name = locs[0]['name']           # z. B. 'locations/987654321'
    loc_id   = loc_name.split('/')[-1]
    acct_id  = account.split('/')[-1]
    _cache['location']    = f'accounts/{acct_id}/locations/{loc_id}'
    _cache['location_id'] = loc_id
    return _cache['location']


# ──────────────────────────────────────────────
# BEWERTUNGEN
# ──────────────────────────────────────────────

STAR_NUM = {'ONE':1,'TWO':2,'THREE':3,'FOUR':4,'FIVE':5}
STAR_STR = {'ONE':'★','TWO':'★★','THREE':'★★★','FOUR':'★★★★','FIVE':'★★★★★'}

def get_reviews(limit=10):
    location = _get_location()
    h = _headers()
    if not location or not h:
        return []
    try:
        r = req.get(f'{V4_API}/{location}/reviews', headers=h,
                    params={'pageSize': limit, 'orderBy': 'updateTime desc'})
        if not r.ok:
            print(f'[GMB] Reviews-Fehler: {r.status_code} {r.text[:200]}')
            return []
        return [{
            'name':     rv.get('name', ''),
            'reviewer': rv.get('reviewer', {}).get('displayName', 'Anonym'),
            'rating':   rv.get('starRating', ''),
            'comment':  rv.get('comment', ''),
            'time':     rv.get('createTime', ''),
            'reply':    rv.get('reviewReply', {}).get('comment') if rv.get('reviewReply') else None
        } for rv in r.json().get('reviews', [])]
    except Exception as e:
        print(f'[GMB] Reviews-Fehler: {e}')
        return []

def reply_to_review(review_name, reply_text):
    """review_name: z. B. 'accounts/123/locations/456/reviews/789'"""
    h = _headers()
    if not h:
        return False, 'Keine Auth'
    r = req.put(f'{V4_API}/{review_name}/reply',
                headers=h, json={'comment': reply_text})
    if r.ok:
        return True, 'Antwort gepostet'
    return False, f'{r.status_code}: {r.text[:200]}'


# ──────────────────────────────────────────────
# BEITRÄGE (LOCAL POSTS)
# ──────────────────────────────────────────────

TOPIC_TYPES = {'news':'STANDARD','angebot':'OFFER','event':'EVENT','standard':'STANDARD'}

def create_post(text, topic='news', call_to_action=None, offer_details=None):
    location = _get_location()
    h = _headers()
    if not location or not h:
        return False, 'Keine Verbindung'
    try:
        tt = TOPIC_TYPES.get(topic.lower(), 'STANDARD')
        body = {'languageCode': 'de', 'summary': text, 'topicType': tt}
        if call_to_action:
            body['callToAction'] = {
                'actionType': call_to_action.get('action_type', 'LEARN_MORE'),
                'url': call_to_action.get('url', 'https://www.chefblick.de')
            }
        if tt == 'OFFER' and offer_details:
            body['offer'] = offer_details
        r = req.post(f'{V4_API}/{location}/localPosts', headers=h, json=body)
        if r.ok:
            return True, r.json().get('name', 'Post erstellt')
        return False, f'{r.status_code}: {r.text[:300]}'
    except Exception as e:
        return False, str(e)

def get_posts(limit=5):
    location = _get_location()
    h = _headers()
    if not location or not h:
        return []
    try:
        r = req.get(f'{V4_API}/{location}/localPosts', headers=h,
                    params={'pageSize': limit})
        if not r.ok:
            return []
        return [{'summary': p.get('summary',''), 'state': p.get('state',''),
                 'createTime': p.get('createTime','')} for p in r.json().get('localPosts',[])]
    except Exception as e:
        print(f'[GMB] Posts-Fehler: {e}')
        return []


# ──────────────────────────────────────────────
# GESCHÄFTSINFO
# ──────────────────────────────────────────────

def get_business_info():
    if 'location_id' not in _cache:
        _get_location()
    loc_id = _cache.get('location_id')
    h = _headers()
    if not loc_id or not h:
        return None
    try:
        r = req.get(f'{INFO_API}/locations/{loc_id}', headers=h,
                    params={'readMask': 'name,title,storefrontAddress,regularHours,phoneNumbers,websiteUri,profile'})
        if not r.ok:
            print(f'[GMB] Info-Fehler: {r.status_code} {r.text[:200]}')
            return None
        return r.json()
    except Exception as e:
        print(f'[GMB] Info-Fehler: {e}')
        return None

def update_hours(periods):
    if 'location_id' not in _cache:
        _get_location()
    loc_id = _cache.get('location_id')
    h = _headers()
    if not loc_id or not h:
        return False, 'Keine Verbindung'
    try:
        r = req.patch(f'{INFO_API}/locations/{loc_id}', headers=h,
                      params={'updateMask': 'regularHours'},
                      json={'regularHours': {'periods': periods}})
        if r.ok:
            return True, 'Öffnungszeiten aktualisiert'
        return False, f'{r.status_code}: {r.text[:200]}'
    except Exception as e:
        return False, str(e)

def update_description(description):
    if 'location_id' not in _cache:
        _get_location()
    loc_id = _cache.get('location_id')
    h = _headers()
    if not loc_id or not h:
        return False, 'Keine Verbindung'
    try:
        r = req.patch(f'{INFO_API}/locations/{loc_id}', headers=h,
                      params={'updateMask': 'profile.description'},
                      json={'profile': {'description': description}})
        if r.ok:
            return True, 'Beschreibung aktualisiert'
        return False, f'{r.status_code}: {r.text[:200]}'
    except Exception as e:
        return False, str(e)


# ──────────────────────────────────────────────
# PERFORMANCE / INSIGHTS
# ──────────────────────────────────────────────

DAILY_METRICS = [
    'BUSINESS_IMPRESSIONS_DESKTOP_MAPS',
    'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH',
    'BUSINESS_IMPRESSIONS_MOBILE_MAPS',
    'BUSINESS_IMPRESSIONS_MOBILE_SEARCH',
    'BUSINESS_DIRECTION_REQUESTS',
    'CALL_CLICKS',
    'WEBSITE_CLICKS',
]

def get_insights(days=28):
    if 'location_id' not in _cache:
        _get_location()
    loc_id = _cache.get('location_id')
    h = _headers()
    if not loc_id or not h:
        return None
    try:
        end   = datetime.utcnow().date()
        start = end - timedelta(days=days)
        totals = {}
        for metric in DAILY_METRICS:
            r = req.get(
                f'{PERF_API}/locations/{loc_id}:getDailyMetricsTimeSeries',
                headers=h,
                params={
                    'dailyMetric': metric,
                    'dailyRange.startDate.year':  start.year,
                    'dailyRange.startDate.month': start.month,
                    'dailyRange.startDate.day':   start.day,
                    'dailyRange.endDate.year':    end.year,
                    'dailyRange.endDate.month':   end.month,
                    'dailyRange.endDate.day':     end.day,
                }
            )
            if r.ok:
                series = r.json().get('timeSeries', {}).get('datedValues', [])
                totals[metric] = sum(int(v.get('value', 0)) for v in series if v.get('value'))
        return totals
    except Exception as e:
        print(f'[GMB] Insights-Fehler: {e}')
        return None


# ──────────────────────────────────────────────
# SIGGI KONTEXT
# ──────────────────────────────────────────────

def get_gmb_context():
    if not is_connected():
        return 'GOOGLE UNTERNEHMENSPROFIL: Nicht verbunden (einmalig /api/gmb/auth aufrufen).'
    try:
        lines = ['GOOGLE UNTERNEHMENSPROFIL (chefblick.de): ✅ Verbunden']
        try:
            reviews = get_reviews(5)
            if reviews:
                stars = [STAR_NUM[r['rating']] for r in reviews if r['rating'] in STAR_NUM]
                avg   = round(sum(stars)/len(stars), 1) if stars else '?'
                unanswered = [r for r in reviews if not r['reply']]
                lines.append(f'  Bewertungen: Ø {avg}/5 (letzte {len(reviews)})')
                if unanswered:
                    lines.append(f'  ⚠ {len(unanswered)} unbeantwortete Bewertung(en):')
                    for rv in unanswered[:2]:
                        stars_str = STAR_STR.get(rv["rating"], rv["rating"])
                        comment   = (rv["comment"] or "(kein Text)")[:80]
                        lines.append(f'    - {rv["reviewer"]}: {stars_str} — "{comment}"')
                        lines.append(f'      review_name: {rv["name"]}')
        except Exception as re:
            if '429' in str(re):
                lines.append('  (Bewertungen: API-Limit erreicht, nächste Abfrage in Kürze)')
            else:
                lines.append(f'  (Bewertungen: {re})')
        try:
            insights = get_insights(28)
            if insights:
                total_imp  = sum(v for k,v in insights.items() if 'IMPRESSIONS' in k)
                calls      = insights.get('CALL_CLICKS', 0)
                web        = insights.get('WEBSITE_CLICKS', 0)
                directions = insights.get('BUSINESS_DIRECTION_REQUESTS', 0)
                lines.append(f'  Performance (28 Tage): {total_imp} Impressionen | {calls} Anrufe | {web} Website-Klicks | {directions} Routen')
        except Exception as ie:
            if '429' in str(ie):
                lines.append('  (Insights: API-Limit erreicht)')
        return '\n'.join(lines)
    except Exception as e:
        if '429' in str(e):
            return 'GOOGLE UNTERNEHMENSPROFIL: ✅ Verbunden (API-Limit kurz überschritten, Daten folgen)'
        return f'GOOGLE UNTERNEHMENSPROFIL: Verbindungsfehler ({e})'
