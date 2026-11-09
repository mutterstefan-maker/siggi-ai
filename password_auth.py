"""Login gegen bestehende Passwort-Hashes, ohne voreingestellte Zugangsdaten."""
import json
from pathlib import Path
import secrets
from werkzeug.security import check_password_hash, generate_password_hash

USERS_FILE = Path(__file__).with_name('users.json')
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


def authenticate_user(username, password):
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 1024:
        return False
    try:
        users = json.loads(USERS_FILE.read_text(encoding='utf-8'))
        stored = users.get(username)
        valid = check_password_hash(stored or _DUMMY_HASH, password)
        return bool(stored and valid)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
