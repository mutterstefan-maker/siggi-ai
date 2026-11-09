"""Zugriffsschutz isoliert testen, ohne die produktiven Hintergrundjobs zu starten."""
import ast
import os
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask, jsonify, request, session, redirect
from media_access import signed_media_url, valid_media_access, MEDIA_TTL
import password_auth
from werkzeug.security import generate_password_hash


def guarded_app():
    app = Flask(__name__, static_folder=None)
    app.secret_key = 'test-session-key-only'
    names = {'require_login', 'block_cross_site_writes', 'private_response_headers'}
    tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
    selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    exec(compile(selected, 'app.py', 'exec'), {'app': app, 'request': request, 'session': session,
         'jsonify': jsonify, 'redirect': redirect, 'valid_media_access': valid_media_access,
         'TRUSTED_ORIGINS': {'https://stean.info', 'https://www.stean.info'}})
    def content(**kw):
        return 'PRIVATE'
    for path, endpoint in [('/', 'index'), ('/api/settings', 'settings'), ('/login', 'login'),
                           ('/api/gpt/openapi.json', 'schema'),
                           ('/api/instagram/media/<path:filename>', 'instagram_media'),
                           ('/api/instagram/reels/media/<path:filename>', 'reels_media_route')]:
        app.add_url_rule(path, endpoint, content, methods=['GET', 'POST'])
    return app


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'FLASK_SECRET': 'test-key-not-a-real-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = guarded_app()
        self.client = self.app.test_client()

    def test_anonymous_and_forged_cookie_are_blocked(self):
        self.client.set_cookie('session', 'forged.logged_in.true')
        self.assertEqual(self.client.get('/').status_code, 302)
        for path in ['/api/settings', '/api/gpt/openapi.json', '/api/instagram/media/a.jpg', '/api/instagram/reels/media/a.mp4']:
            for method in ['get', 'head', 'post']:
                self.assertEqual(getattr(self.client, method)(path).status_code, 401, (path, method))

    def test_signed_media_is_bound_to_path_and_expiry(self):
        path = '/api/instagram/reels/media/test video.mp4'
        with patch('media_access.time.time', return_value=100):
            url = signed_media_url('https://stean.info', path)
            parts = urlsplit(url)
            route = parts.path + '?' + parts.query
            self.assertEqual(self.client.get(route).status_code, 200)
            self.assertEqual(self.client.get(route.replace('test%20video', 'other')).status_code, 401)
            self.assertEqual(self.client.post(route).status_code, 401)
            self.assertEqual(self.client.get(route + 'x').status_code, 401)
        with patch('media_access.time.time', return_value=100 + MEDIA_TTL):
            self.assertEqual(self.client.get(route).status_code, 401)

    def test_authenticated_preview_and_cache_headers(self):
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
        for path in ['/', '/api/settings', '/api/instagram/reels/media/a.mp4']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.post('/api/settings', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/settings', headers={'Origin': 'https://stean.info'}).status_code, 200)

    def test_missing_signing_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(valid_media_access('/api/instagram/media/a.jpg', {'expires': '1', 'signature': 'x'}))
            with self.assertRaises(RuntimeError):
                signed_media_url('https://stean.info', '/api/instagram/media/a.jpg')


class PasswordTests(unittest.TestCase):
    def test_only_valid_stored_hash_authenticates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'users.json'
            with patch.object(password_auth, 'USERS_FILE', path):
                self.assertFalse(password_auth.authenticate_user('test', 'correct-test-pass'))
                path.write_text(json.dumps({'test': generate_password_hash('correct-test-pass')}))
                self.assertTrue(password_auth.authenticate_user('test', 'correct-test-pass'))
                self.assertFalse(password_auth.authenticate_user('test', 'wrong'))
                self.assertFalse(password_auth.authenticate_user('unknown', 'correct-test-pass'))
                self.assertFalse(password_auth.authenticate_user([], {}))
                path.write_text('invalid json')
                self.assertFalse(password_auth.authenticate_user('test', 'correct-test-pass'))


if __name__ == '__main__':
    unittest.main()
