# -*- coding: utf-8 -*-
"""
LinkedIn Engine für SIGGI
Postet über "Share on LinkedIn" (OpenID Connect) auf Stefans persönlichem Profil.
Hinweis: Ohne die (nicht freigegebene) Community Management API ist ein Posten als
offizielle ChefBlick-Unternehmensseite nicht möglich - nur als Person Stefan Mutter.
"""
import os
import json
import time
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'linkedin_token.json')
POSTS_LOG_PATH = os.path.join(BASE_DIR, 'linkedin_posts.json')

AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'
UGC_POSTS_URL = 'https://api.linkedin.com/v2/ugcPosts'
SOCIAL_ACTIONS_URL = 'https://api.linkedin.com/v2/socialActions'
SCOPES = 'openid profile email w_member_social'


def _load_settings():
    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _config():
    s = _load_settings()
    return {
        'client_id': s.get('linkedin_client_id', ''),
        'client_secret': s.get('linkedin_client_secret', ''),
        'redirect_uri': s.get('linkedin_redirect_uri', ''),
    }


def is_configured():
    cfg = _config()
    return bool(cfg['client_id'] and cfg['client_secret'] and cfg['redirect_uri'])


def get_authorize_url(state='siggi'):
    cfg = _config()
    params = (
        f"response_type=code&client_id={cfg['client_id']}"
        f"&redirect_uri={requests.utils.quote(cfg['redirect_uri'], safe='')}"
        f"&scope={requests.utils.quote(SCOPES)}"
        f"&state={state}"
    )
    return f"{AUTH_URL}?{params}"


def exchange_code_for_token(code):
    cfg = _config()
    resp = requests.post(
        TOKEN_URL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': cfg['redirect_uri'],
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=15
    )
    data = resp.json()
    if 'access_token' not in data:
        raise Exception(f"Token-Austausch fehlgeschlagen: {data}")

    data['obtained_at'] = time.time()
    with open(TOKEN_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return data


def _load_token():
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def is_connected():
    token = _load_token()
    if not token:
        return False
    expires_in = token.get('expires_in', 0)
    obtained_at = token.get('obtained_at', 0)
    return time.time() < obtained_at + expires_in - 60


def _get_access_token():
    token = _load_token()
    if not token or not is_connected():
        return None
    return token.get('access_token')


def _get_person_urn(access_token):
    resp = requests.get(
        USERINFO_URL,
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10
    )
    data = resp.json()
    sub = data.get('sub')
    if not sub:
        raise Exception(f"Konnte Profil-ID nicht ermitteln: {data}")
    return f'urn:li:person:{sub}'


def _log_post(post_urn, text):
    posts = []
    if os.path.exists(POSTS_LOG_PATH):
        try:
            with open(POSTS_LOG_PATH, 'r', encoding='utf-8') as f:
                posts = json.load(f)
        except Exception:
            posts = []
    posts.append({'urn': post_urn, 'text': text, 'created_at': time.time()})
    posts = posts[-20:]  # nur die letzten 20 behalten
    with open(POSTS_LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)


def get_last_post_urn():
    if not os.path.exists(POSTS_LOG_PATH):
        return None
    try:
        with open(POSTS_LOG_PATH, 'r', encoding='utf-8') as f:
            posts = json.load(f)
        return posts[-1]['urn'] if posts else None
    except Exception:
        return None


def get_recent_posts(limit=5):
    if not os.path.exists(POSTS_LOG_PATH):
        return []
    try:
        with open(POSTS_LOG_PATH, 'r', encoding='utf-8') as f:
            posts = json.load(f)
        return posts[-limit:]
    except Exception:
        return []


def post_share(text):
    """Veröffentlicht einen Text-Beitrag auf Stefans LinkedIn-Profil."""
    access_token = _get_access_token()
    if not access_token:
        return 'LinkedIn ist nicht verbunden - bitte einmalig über /api/linkedin/auth anmelden.'

    try:
        person_urn = _get_person_urn(access_token)
        body = {
            'author': person_urn,
            'lifecycleState': 'PUBLISHED',
            'specificContent': {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': text},
                    'shareMediaCategory': 'NONE'
                }
            },
            'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'}
        }
        resp = requests.post(
            UGC_POSTS_URL,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-Restli-Protocol-Version': '2.0.0'
            },
            json=body,
            timeout=15
        )
        if resp.status_code in (200, 201):
            post_urn = resp.headers.get('x-restli-id') or resp.json().get('id', '')
            if post_urn:
                _log_post(post_urn, text)
            return 'Beitrag auf LinkedIn veröffentlicht.'
        return f'LinkedIn-Fehler ({resp.status_code}): {resp.text[:200]}'
    except Exception as e:
        return f'LinkedIn-Fehler: {e}'


def fetch_comments_raw(post_urn):
    """Interne Rohfassung: gibt eine Liste von Kommentaren zurück oder wirft eine Exception."""
    access_token = _get_access_token()
    if not access_token:
        raise Exception('LinkedIn ist nicht verbunden')

    encoded_urn = requests.utils.quote(post_urn, safe='')
    resp = requests.get(
        f'{SOCIAL_ACTIONS_URL}/{encoded_urn}/comments',
        headers={
            'Authorization': f'Bearer {access_token}',
            'X-Restli-Protocol-Version': '2.0.0'
        },
        timeout=15
    )
    if resp.status_code != 200:
        raise Exception(f'{resp.status_code}: {resp.text[:200]}')
    elements = resp.json().get('elements', [])
    comments = []
    for el in elements:
        actor = el.get('actor', 'Unbekannt')
        text = (el.get('message') or {}).get('text', '')
        comments.append({'id': el.get('$URN') or el.get('id'), 'actor': actor, 'text': text})
    return comments


def get_comments(post_urn=None):
    """Liest Kommentare zu einem Beitrag (Standard: der zuletzt von Siggi geteilte Beitrag) - für das Chat-Tool."""
    post_urn = post_urn or get_last_post_urn()
    if not post_urn:
        return 'Kein Beitrag bekannt, zu dem Kommentare gelesen werden können.'
    try:
        comments = fetch_comments_raw(post_urn)
        if not comments:
            return 'Noch keine Kommentare vorhanden.'
        return json.dumps(comments, ensure_ascii=False)
    except Exception as e:
        return f'LinkedIn-Fehler: {e}'


def reply_to_comment(text, post_urn=None):
    """Antwortet als Kommentar auf einen Beitrag (Standard: der zuletzt von Siggi geteilte Beitrag)."""
    access_token = _get_access_token()
    if not access_token:
        return 'LinkedIn ist nicht verbunden - bitte einmalig über /api/linkedin/auth anmelden.'

    post_urn = post_urn or get_last_post_urn()
    if not post_urn:
        return 'Kein Beitrag bekannt, auf den geantwortet werden kann.'

    try:
        person_urn = _get_person_urn(access_token)
        encoded_urn = requests.utils.quote(post_urn, safe='')
        resp = requests.post(
            f'{SOCIAL_ACTIONS_URL}/{encoded_urn}/comments',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-Restli-Protocol-Version': '2.0.0'
            },
            json={'actor': person_urn, 'object': post_urn, 'message': {'text': text}},
            timeout=15
        )
        if resp.status_code in (200, 201):
            return 'Kommentar veröffentlicht.'
        return f'LinkedIn-Fehler beim Kommentieren ({resp.status_code}): {resp.text[:200]}'
    except Exception as e:
        return f'LinkedIn-Fehler: {e}'
