"""Zeitlich begrenzte, dateigebundene Abrufrechte fuer Meta; keine oeffentlichen Pools."""
import hashlib
import hmac
import os
import time
from urllib.parse import quote, urlencode

MEDIA_TTL = 24 * 60 * 60
MEDIA_PREFIXES = ('/api/instagram/media/', '/api/instagram/reels/media/')


def _signature(path, expires):
    secret = os.environ.get('FLASK_SECRET')
    if not secret:
        raise RuntimeError('FLASK_SECRET fehlt: Medienfreigabe gesperrt')
    return hmac.new(secret.encode(), f'media-v1\n{path}\n{expires}'.encode(), hashlib.sha256).hexdigest()


def signed_media_url(base_url, path):
    if not path.startswith(MEDIA_PREFIXES):
        raise ValueError('Nur Medienpfade duerfen freigegeben werden')
    expires = int(time.time()) + MEDIA_TTL
    query = urlencode({'expires': expires, 'signature': _signature(path, expires)})
    return f"{base_url.rstrip('/')}{quote(path, safe='/')}?{query}"


def valid_media_access(path, args):
    try:
        raw_expires = args.get('expires', '')
        expires = int(raw_expires)
        now = int(time.time())
        if not path.startswith(MEDIA_PREFIXES) or not now < expires <= now + MEDIA_TTL:
            return False
        return hmac.compare_digest(_signature(path, expires), args.get('signature', ''))
    except (ValueError, TypeError, RuntimeError):
        return False
