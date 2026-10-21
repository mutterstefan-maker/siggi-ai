"""Erfasst den KI-Verbrauch (Claude) aller Siggi-Teile und rechnet ihn in Euro um.

Haengt sich beim Import einmal in requests ein und protokolliert die `usage` jeder
erfolgreichen Antwort von api.anthropic.com/v1/messages - egal, welches Modul den Aufruf
macht (Chat, Mails, LinkedIn, Bilder, Kommentare, Audits ...). Der Verursacher wird aus dem
Aufruf-Stack bestimmt und einem Agenten der Agenten-Zentrale zugeordnet.

Wird von agents_engine importiert - dadurch ist das in jedem Prozess aktiv, der Agenten
meldet (Web-App, Mail-Dienst, Cron-Laeufe).
"""
import inspect
import json
import os
import sqlite3
from datetime import datetime, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'agents.db')

# US-$ pro Million Tokens: Eingabe, Ausgabe, Cache lesen, Cache schreiben 5 Min., Cache schreiben 1 Std.
# (Anthropic-Listenpreise; Cache: lesen 0,1x, schreiben 1,25x bzw. 2x des Eingabepreises)
PRICES = {
    'claude-fable-5': (10.0, 50.0, 1.0, 12.5, 20.0),
    'claude-opus-5-5': (4.0, 20.0, 0.4, 5.0, 8.0),
    'claude-opus-5': (5.0, 25.0, 0.5, 6.25, 10.0),
    'claude-opus-4': (5.0, 25.0, 0.5, 6.25, 10.0),
    'claude-sonnet-5': (2.0, 10.0, 0.2, 2.5, 4.0),
    'claude-sonnet-4': (3.0, 15.0, 0.3, 3.75, 6.0),
    'claude-haiku-4-5': (1.0, 5.0, 0.1, 1.25, 2.0),
}
DEFAULT_PRICE = PRICES['claude-sonnet-5']

# Verursacher: (Modul- oder Funktionsname im Aufruf-Stack, Agent-ID, Anzeigename) - Reihenfolge = Vorrang
# (Telegram laeuft durch den Chat, soll aber als Telegram zaehlen)
SOURCES = [
    ('telegram_engine', 'telegram', 'Telegram'),
    ('_suggest_instagram_reply', 'comments', 'Kommentare'),
    ('_suggest_linkedin_reply', 'comments', 'Kommentare'),
    ('followup_engine', 'followup', 'Nachfassen'),
    ('mail_engine', 'mail', 'Mails'),
    ('linkedin_pipeline_engine', 'linkedin', 'LinkedIn'),
    ('instagram_flyer_engine', 'bild', 'Bilder'),
    ('reels_engine', 'reels', 'Reels'),
    ('instagram_engine', 'instagram', 'Instagram'),
    ('self_improve_engine', 'improve', 'Selbstverbesserung'),
    ('health_check_engine', 'health', 'Health-Check'),
    ('audit_ai', 'audit', 'Website-Audits'),
    ('audit_engine', 'audit', 'Website-Audits'),
    ('jarvis_chat', 'chat', 'Chat (Dashboard)'),
]
FALLBACK_SOURCE = ('other', 'Sonstiges')


def _init():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute('''CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, provider TEXT, model TEXT, source TEXT,
        input_tokens INTEGER, cache_write_tokens INTEGER, cache_read_tokens INTEGER, output_tokens INTEGER,
        usd REAL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)')
    c.commit()
    c.close()


def _source():
    names = set()
    for frame in inspect.stack(context=0):
        names.add(os.path.splitext(os.path.basename(frame.filename))[0])
        names.add(frame.function)
    for marker, agent_id, _ in SOURCES:
        if marker in names:
            return agent_id
    return FALLBACK_SOURCE[0]


def _price(model):
    for prefix, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])):
        if (model or '').startswith(prefix):
            return p
    return DEFAULT_PRICE


def cost_usd(model, u):
    p_in, p_out, p_read, p_w5, p_w1h = _price(model)
    cc = u.get('cache_creation') or {}
    w1h = cc.get('ephemeral_1h_input_tokens', 0) or 0
    w5 = cc.get('ephemeral_5m_input_tokens')
    if w5 is None:  # aeltere Antworten ohne Aufschluesselung: alles als 5-Min.-Cache werten
        w5 = (u.get('cache_creation_input_tokens', 0) or 0) - w1h
    return ((u.get('input_tokens', 0) or 0) * p_in + (u.get('output_tokens', 0) or 0) * p_out
            + (u.get('cache_read_input_tokens', 0) or 0) * p_read + w5 * p_w5 + w1h * p_w1h) / 1e6


def record(model, u, source=None):
    try:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.execute('INSERT INTO api_usage (created_at, provider, model, source, input_tokens, cache_write_tokens, '
                  'cache_read_tokens, output_tokens, usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (datetime.now().isoformat(timespec='seconds'), 'anthropic', model, source or _source(),
                   u.get('input_tokens', 0) or 0, u.get('cache_creation_input_tokens', 0) or 0,
                   u.get('cache_read_input_tokens', 0) or 0, u.get('output_tokens', 0) or 0, cost_usd(model, u)))
        c.commit()
        c.close()
    except Exception as e:
        print(f'[Verbrauch] Konnte nicht protokollieren: {e}')


# ─── In requests einhaengen (einmal pro Prozess) ─────────────────────

def _install():
    if getattr(requests.sessions.Session.request, '_siggi_usage', False):
        return
    original = requests.sessions.Session.request

    def request(self, method, url, *args, **kwargs):
        resp = original(self, method, url, *args, **kwargs)
        try:
            if ('api.anthropic.com' in str(url) and '/v1/messages' in str(url) and 'count_tokens' not in str(url)
                    and not kwargs.get('stream') and resp.status_code == 200):
                data = resp.json()
                if data.get('usage'):
                    record(data.get('model', ''), data['usage'])
        except Exception:
            pass  # Mitschreiben darf nie den eigentlichen Aufruf stoeren
        return resp

    request._siggi_usage = True
    requests.sessions.Session.request = request


# ─── Auswertung ──────────────────────────────────────────────────────

def usd_to_eur_rate():
    """Tageskurs der EZB (einmal pro Tag geholt, in settings.json gemerkt)."""
    import re
    import settings_store
    today = datetime.now().strftime('%Y-%m-%d')
    cached = settings_store.load_or_empty().get('usd_eur_rate') or {}
    if cached.get('fetched') == today and cached.get('rate'):
        return cached['rate'], cached.get('date')
    try:
        xml = requests.get('https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml', timeout=10).text
        usd_per_eur = float(re.search(r"currency='USD' rate='([\d.]+)'", xml).group(1))
        day = re.search(r"time='([\d-]+)'", xml).group(1)
        rate = round(1 / usd_per_eur, 5)
        settings_store.update(lambda s: s.__setitem__('usd_eur_rate', {'rate': rate, 'date': day, 'fetched': today}))
        return rate, day
    except Exception as e:
        print(f'[Verbrauch] EZB-Kurs nicht abrufbar: {e}')
        return cached.get('rate'), cached.get('date')  # notfalls letzter bekannter Kurs (oder None)


def summary():
    """Verbrauch heute / 7 Tage / Monat, Hochrechnung, Aufteilung nach Verursacher - in Euro."""
    rate, rate_date = usd_to_eur_rate()
    now = datetime.now()
    day0 = now.strftime('%Y-%m-%d')
    week0 = (now - timedelta(days=6)).strftime('%Y-%m-%d')
    month0 = now.strftime('%Y-%m-01')
    c = sqlite3.connect(DB_PATH, timeout=10)
    try:
        def total(since):
            return c.execute('SELECT COALESCE(SUM(usd), 0), COUNT(*) FROM api_usage WHERE created_at >= ?', (since,)).fetchone()
        t_day, n_day = total(day0)
        t_week, _ = total(week0)
        t_month, n_month = total(month0)
        first = c.execute('SELECT MIN(created_at) FROM api_usage').fetchone()[0]
        by_source = c.execute('SELECT source, SUM(usd), COUNT(*) FROM api_usage WHERE created_at >= ? '
                              'GROUP BY source ORDER BY SUM(usd) DESC', (month0,)).fetchall()
    finally:
        c.close()
    # Hochrechnung: Durchschnitt pro Tag seit Erfassungsbeginn (innerhalb des Monats) x Tage im Monat.
    # Erst ab einem vollen erfassten Tag - eine Stunde auf einen Monat hochzurechnen ergibt Unsinn
    # (z.B. ein Testabend mit vielen Nachrichten -> "50 EUR/Monat").
    start = max(datetime.fromisoformat(first) if first else now, now.replace(day=1, hour=0, minute=0, second=0))
    days_tracked = (now - start).total_seconds() / 86400
    next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_in_month = (next_month - now.replace(day=1)).days
    projection = t_month / days_tracked * days_in_month if first and days_tracked >= 1 else None

    names = {a: n for _, a, n in SOURCES}
    names[FALLBACK_SOURCE[0]] = FALLBACK_SOURCE[1]
    eur = (lambda usd: round(usd * rate, 4)) if rate else (lambda usd: None)
    return {
        'currency': 'EUR' if rate else 'USD',
        'rate': rate, 'rate_date': rate_date,
        'today': eur(t_day) if rate else round(t_day, 4), 'today_calls': n_day,
        'week': eur(t_week) if rate else round(t_week, 4),
        'month': eur(t_month) if rate else round(t_month, 4), 'month_calls': n_month,
        'month_projection': None if projection is None else (eur(projection) if rate else round(projection, 4)),
        'projection_reliable': days_tracked >= 3,
        'since': first,
        'by_source': [{'id': s, 'name': names.get(s, s), 'amount': eur(u) if rate else round(u, 4), 'calls': n}
                      for s, u, n in by_source],
    }


_init()
_install()
