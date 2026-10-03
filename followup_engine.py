"""Nachfass-Agent: findet Kunden, denen zuletzt WIR geschrieben haben und die sich seitdem nicht
gemeldet haben (z.B. nach einem Angebot), und legt eine kurze Nachfass-Mail als Entwurf an.

- Grundlage: was Siggi verschickt hat (sent_mails: Auto-Antworten + Mails, die Siggi in Stefans
  Namen geschrieben hat) gegen den Posteingang (mails). Stefans eigene Outlook-Mails kennt Siggi
  nicht (Gesendet-Ordner auf dem Server sind leer).
- Nachgefasst wird nach FOLLOWUP_DAYS ohne Antwort, hoechstens einmal pro letzter Mail, und nur
  bis MAX_AGE_DAYS - danach ist das Thema kalt.
- Claude (Haiku, guenstig) entscheidet, ob Nachfassen ueberhaupt passt (nicht nach "Danke, erledigt")
  und schreibt den Text. Es wird NIE direkt verschickt: der Entwurf landet in den Mail-Entwuerfen und
  kommt per Telegram mit Freigabe-Knopf.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import requests

import agents_engine as agents
import settings_store
import siggi_time

DB_PATH = '/opt/stean/mails.db'
DEFAULT_CONFIG = {'days': 7, 'max_age_days': 45, 'max_per_run': 5}
SKIP_PATTERNS = ('noreply', 'no-reply', 'donotreply', 'mailer-daemon', 'postmaster', 'notification', 'newsletter',
                 'bounce', 'info@google', '@google.com', '@facebookmail', '@linkedin.com', '@instagram.com')
OWN_DOMAINS = ('chefblick.de', 'stean.info')


def _addr(text):
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', text or '')
    return m.group(0).lower() if m else None


def _init():
    c = sqlite3.connect(DB_PATH)
    c.execute('''CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        addr TEXT, last_out_at TEXT, decision TEXT, reason TEXT, draft_id INTEGER, created_at TEXT)''')
    c.commit()
    c.close()


def sync_sent_folders(days=60):
    """Stefans selbst verschickte Mails (Outlook) aus den Gesendet-Ordnern der Postfaecher holen - nur so
    sieht Siggi Angebote, die Stefan selbst schreibt. Setzt voraus, dass Outlook Gesendetes auf dem
    Server ablegt. Doppelte werden ueber die Message-ID erkannt."""
    import email
    import imaplib
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime
    c = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in c.execute('PRAGMA table_info(sent_mails)')]
    if 'message_id' not in cols:
        c.execute('ALTER TABLE sent_mails ADD COLUMN message_id TEXT')
    known = {r[0] for r in c.execute('SELECT message_id FROM sent_mails WHERE message_id IS NOT NULL')}
    since = (datetime.now() - timedelta(days=days)).strftime('%d-%b-%Y')
    added = 0
    for acc, cfg in (settings_store.load_or_empty().get('accounts') or {}).items():
        if not cfg.get('active'):
            continue
        try:
            imap = imaplib.IMAP4_SSL(cfg.get('imap_server', 'imap.ionos.de'), 993)
            imap.login(acc, cfg.get('password', ''))
            folders = [f.decode(errors='replace') for f in imap.list()[1]]
            sent = next((f.split(' "/" ')[-1] for f in folders if any(k in f.lower() for k in ('gesendet', 'sent'))), None)
            if not sent:
                imap.logout()
                continue
            imap.select(sent, readonly=True)
            for num in imap.search(None, 'SINCE', since)[1][0].split():
                raw = imap.fetch(num, '(RFC822)')[1][0][1]
                msg = email.message_from_bytes(raw)
                mid = (msg.get('Message-ID') or '').strip() or f'{acc}:{num.decode()}'
                if mid in known:
                    continue
                body = ''
                for part in (msg.walk() if msg.is_multipart() else [msg]):
                    if part.get_content_type() == 'text/plain' and not part.get('Content-Disposition', '').startswith('attachment'):
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                        break
                try:
                    sent_at = parsedate_to_datetime(msg.get('Date')).astimezone().replace(tzinfo=None).isoformat()
                except Exception:
                    sent_at = datetime.now().isoformat()
                c.execute('INSERT INTO sent_mails (account, to_addr, subject, body, sent_at, message_id) VALUES (?,?,?,?,?,?)',
                          (acc, str(make_header(decode_header(msg.get('To', '')))), str(make_header(decode_header(msg.get('Subject', '')))),
                           body[:20000], sent_at, mid))
                known.add(mid)
                added += 1
            imap.logout()
        except Exception as e:
            print(f'[Nachfassen] Gesendet-Ordner {acc}: {e}')
    c.commit()
    c.close()
    return added


def config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(settings_store.load_or_empty().get('followup_settings') or {})
    return cfg


def save_config(data):
    days = max(2, min(60, int(data.get('days', DEFAULT_CONFIG['days']))))
    settings_store.update(lambda s: s.setdefault('followup_settings', {}).__setitem__('days', days))
    return config()


def find_candidates(cfg=None):
    """Kontakte, bei denen unsere letzte Mail FOLLOWUP_DAYS..MAX_AGE_DAYS alt ist und danach nichts kam."""
    cfg = cfg or config()
    _init()
    settings = settings_store.load_or_empty()
    own = {a.lower() for a in (settings.get('accounts') or {})} | {'mutter.stefan@hotmail.com'}
    now = datetime.now()
    newest = (now - timedelta(days=cfg['days'])).isoformat()
    oldest = (now - timedelta(days=cfg['max_age_days'])).isoformat()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        last_out = {}
        for r in c.execute('SELECT to_addr, subject, body, sent_at FROM sent_mails WHERE sent_at >= ? ORDER BY sent_at', (oldest,)):
            a = _addr(r['to_addr'])
            if a:
                last_out[a] = dict(r)
        out = []
        for a, mail in last_out.items():
            if a in own or a.endswith(OWN_DOMAINS) or any(p in a for p in SKIP_PATTERNS):
                continue
            if mail['sent_at'] > newest:
                continue  # noch zu frisch
            last_in = c.execute("SELECT subject, body, created_at FROM mails WHERE from_addr LIKE ? AND is_spam=0 "
                                "ORDER BY created_at DESC LIMIT 1", (f'%{a}%',)).fetchone()
            if last_in and last_in['created_at'] >= mail['sent_at']:
                continue  # Kunde hat danach geantwortet
            if c.execute('SELECT 1 FROM followups WHERE addr=? AND last_out_at=?', (a, mail['sent_at'])).fetchone():
                continue  # fuer diese Mail schon entschieden
            if c.execute("SELECT 1 FROM mail_drafts WHERE status='pending' AND lower(to_addr) LIKE ?", (f'%{a}%',)).fetchone():
                continue  # es wartet schon ein Entwurf an diesen Kunden
            out.append({'addr': a, 'out': mail, 'in': dict(last_in) if last_in else None,
                        'days': (now - datetime.fromisoformat(mail['sent_at'])).days})
        return sorted(out, key=lambda x: x['out']['sent_at'])
    finally:
        c.close()


def _draft_with_claude(cand, api_key):
    out, last_in = cand['out'], cand['in']
    prompt = (
        f"{siggi_time.now_line()}\n\n"
        "Du hilfst Stefan Mutter (Agentur ChefBlick: Websites, Online-Shops, Digitalisierung). Er hat einem "
        f"Kontakt vor {cand['days']} Tagen geschrieben und keine Antwort bekommen.\n\n"
        f"UNSERE LETZTE MAIL (Betreff: {out['subject']}):\n{(out['body'] or '')[:2500]}\n\n"
        + (f"DIE LETZTE MAIL DES KONTAKTS DAVOR (Betreff: {last_in['subject']}):\n{(last_in['body'] or '')[:1200]}\n\n" if last_in else '')
        + "Entscheide, ob eine kurze Nachfass-Mail sinnvoll ist: JA bei offenem Angebot, offener Frage, ausstehender "
        "Entscheidung oder Terminabsprache. NEIN bei erledigten Vorgaengen, reinen Bestaetigungen, Danke-Mails, "
        "Absagen, Spam/Newsletter oder wenn nichts offen ist.\n"
        "Wenn JA: schreibe die Mail auf Deutsch, freundlich und kurz (3-5 Saetze), in derselben Anrede (Du/Sie) wie "
        "unsere letzte Mail, mit konkretem Bezug auf das Thema und einer einfachen naechsten Frage. KEINE Gruss"
        "formel und KEINE Unterschrift (wird automatisch angehaengt), keine Links.\n"
        'Antworte NUR mit JSON: {"nachfassen": true/false, "grund": "kurz", "betreff": "...", "text": "..."}'
    )
    r = requests.post('https://api.anthropic.com/v1/messages',
                      headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 700,
                            'messages': [{'role': 'user', 'content': prompt}]}, timeout=45).json()
    text = ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        raise RuntimeError(f'Keine verwertbare Antwort: {text[:200] or r}')
    return json.loads(m.group(0))


def run(create_draft_fn):
    """Taeglicher Lauf. create_draft_fn(to, betreff, text) legt den Mail-Entwurf an (app.create_mail_draft -
    der schickt ihn per Telegram mit Freigabe-Knopf)."""
    cfg = config()
    plan = lambda *st: [{'label': l, 'state': x} for l, x in zip(
        [f"Kunden ohne Antwort seit {cfg['days']}+ Tagen suchen", 'Prüfen, ob Nachfassen passt', 'Entwürfe zur Freigabe schicken'], st)]
    if not agents.start('followup', 'Suche Kunden, die sich nicht zurückgemeldet haben …', plan=plan('active', 'open', 'open')):
        return []
    try:
        new_sent = sync_sent_folders()
        if new_sent:
            agents.event('followup', f'{new_sent} selbst verschickte Mail(s) aus den Gesendet-Ordnern übernommen')
        cands = find_candidates(cfg)[:cfg['max_per_run']]
        if not cands:
            agents.done('followup', 'Niemand wartet auf ein Nachfassen', plan=plan('done', 'done', 'done'),
                        bubble=f"Alle Kunden haben geantwortet – nächster Check morgen", quiet=True)
            return []
        settings = settings_store.load_or_empty()
        api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
        created = []
        c = sqlite3.connect(DB_PATH)
        for i, cand in enumerate(cands):
            agents.step('followup', f"Prüfe {cand['addr']} ({cand['days']} Tage ohne Antwort) …",
                        plan=plan('done', 'active', 'open'), progress=i / len(cands))
            try:
                res = _draft_with_claude(cand, api_key)
            except Exception as e:
                agents.event('followup', f"{cand['addr']}: {e}", 'warn')
                continue
            draft_id = None
            if res.get('nachfassen') and res.get('text'):
                subject = res.get('betreff') or f"Re: {cand['out']['subject']}"
                draft_id = create_draft_fn(cand['addr'], subject, res['text'].strip())
                created.append(draft_id)
                agents.event('followup', f"Nachfass-Entwurf an {cand['addr']}: {subject}", 'success')
            c.execute('INSERT INTO followups (addr, last_out_at, decision, reason, draft_id, created_at) VALUES (?,?,?,?,?,?)',
                      (cand['addr'], cand['out']['sent_at'], 'draft' if draft_id else 'skip', str(res.get('grund', ''))[:300],
                       draft_id, datetime.now().isoformat(timespec='seconds')))
            c.commit()
        c.close()
        agents.done('followup', f"{len(created)} Nachfass-Entwurf/Entwürfe zur Freigabe ({len(cands) - len(created)} nicht nötig)",
                    plan=plan('done', 'done', 'done'))
        return created
    except Exception as e:
        agents.fail('followup', e)
        raise
