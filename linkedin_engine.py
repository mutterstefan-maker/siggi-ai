# -*- coding: utf-8 -*-
"""
LinkedIn Engine für SIGGI - offizielles Posten via LinkedIn API.

Voraussetzung: LinkedIn Developer App mit Produkt "Share on LinkedIn"
(Community Management API) freigeschaltet, OAuth Access-Token mit Scope
w_member_social (siehe LINKEDIN_ACCESS_TOKEN in config/.env).

Kommentare lesen/beantworten laufen weiterhin über linkedin_scrape_engine.py
(Browser-Fallback), da die offizielle API das normalen OAuth-Apps nicht erlaubt.
Das eigentliche Posten läuft hier über die offizielle API (UGC Posts), da das
im Gegensatz zum Kommentieren regulär unterstützt und erlaubt ist.
"""
import os
import requests as req

API_BASE = 'https://api.linkedin.com/v2'
API_VERSION_HEADER = {'LinkedIn-Version': '202401', 'X-Restli-Protocol-Version': '2.0.0'}


def _access_token():
    return os.environ.get('LINKEDIN_ACCESS_TOKEN', '')


def _author_urn():
    """Person- oder Organisations-URN, unter dem gepostet wird.
    Fest über LINKEDIN_AUTHOR_URN konfigurierbar, sonst automatisch per /userinfo ermittelt."""
    fixed = os.environ.get('LINKEDIN_AUTHOR_URN', '')
    if fixed:
        return fixed
    return _fetch_own_urn()


def _headers():
    token = _access_token()
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        **API_VERSION_HEADER
    }


def is_configured():
    return bool(_access_token())


def _fetch_own_urn():
    """Ermittelt die eigene Person-URN über den OpenID-Connect /userinfo-Endpunkt."""
    try:
        r = req.get(f'{API_BASE}/userinfo', headers={'Authorization': f'Bearer {_access_token()}'}, timeout=15)
        if not r.ok:
            return ''
        sub = r.json().get('sub', '')
        return f'urn:li:person:{sub}' if sub else ''
    except Exception as e:
        print(f'[LinkedIn] Konnte eigene URN nicht ermitteln: {e}')
        return ''


def _register_image_upload(author_urn):
    """Schritt 1 von 2 für Bild-Posts: Upload-Slot bei LinkedIn registrieren."""
    payload = {
        'registerUploadRequest': {
            'recipes': ['urn:li:digitalmediaRecipe:feedshare-image'],
            'owner': author_urn,
            'serviceRelationships': [{
                'relationshipType': 'OWNER',
                'identifier': 'urn:li:userGeneratedContent'
            }]
        }
    }
    r = req.post(f'{API_BASE}/assets?action=registerUpload', headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        return None, f'Bild-Upload-Registrierung fehlgeschlagen: {r.text}'
    data = r.json()['value']
    upload_url = data['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
    asset_urn = data['asset']
    return {'upload_url': upload_url, 'asset_urn': asset_urn}, None


def _upload_image_bytes(upload_url, image_bytes):
    r = req.put(
        upload_url,
        headers={'Authorization': f'Bearer {_access_token()}'},
        data=image_bytes,
        timeout=60
    )
    if r.status_code not in (200, 201):
        return f'Bild-Upload fehlgeschlagen: {r.status_code} {r.text}'
    return None


def post_share(text, image_path=None):
    """Veröffentlicht einen Text-Post (optional mit Bild) auf LinkedIn.
    Gibt {'success': True, 'post_urn': ...} oder {'success': False, 'error': ...} zurück."""
    if not is_configured():
        return {'success': False, 'error': 'LinkedIn Access-Token fehlt (LINKEDIN_ACCESS_TOKEN in config/.env setzen).'}

    author_urn = _author_urn()
    if not author_urn:
        return {'success': False, 'error': 'Konnte Author-URN nicht ermitteln (LINKEDIN_AUTHOR_URN in config/.env setzen).'}

    media = []
    if image_path:
        if not os.path.exists(image_path):
            return {'success': False, 'error': f'Bilddatei nicht gefunden: {image_path}'}
        reg, err = _register_image_upload(author_urn)
        if err:
            return {'success': False, 'error': err}
        with open(image_path, 'rb') as f:
            upload_err = _upload_image_bytes(reg['upload_url'], f.read())
        if upload_err:
            return {'success': False, 'error': upload_err}
        media.append({
            'status': 'READY',
            'media': reg['asset_urn'],
        })

    share_content = {
        'shareCommentary': {'text': text},
        'shareMediaCategory': 'IMAGE' if media else 'NONE'
    }
    if media:
        share_content['media'] = media

    payload = {
        'author': author_urn,
        'lifecycleState': 'PUBLISHED',
        'specificContent': {
            'com.linkedin.ugc.ShareContent': share_content
        },
        'visibility': {
            'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'
        }
    }

    try:
        r = req.post(f'{API_BASE}/ugcPosts', headers=_headers(), json=payload, timeout=30)
        if r.status_code not in (200, 201):
            return {'success': False, 'error': f'LinkedIn-API-Fehler {r.status_code}: {r.text}'}
        post_urn = r.headers.get('x-restli-id', '')
        return {'success': True, 'post_urn': post_urn}
    except Exception as e:
        return {'success': False, 'error': str(e)}
