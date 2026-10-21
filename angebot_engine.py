"""Angebots-Agent: macht aus einer Projektanfrage per Mail sofort einen fertigen Angebotsentwurf.

Ablauf je neuer Mail (nach dem Abruf durch den Mail-Agenten, der die Anfrage weiterhin selbst kurz
beantwortet):
1. Stichwort-Vorfilter (kostenlos), dann Claude Haiku: echte Projektanfrage? Wer, was, welche Website?
2. Hat der Kunde schon eine Website: Website-Analyse (audit_engine) - Kundenversion als PDF dazu.
3. Claude Sonnet waehlt das passende Paket und schreibt Einleitung + Mailtext. Die PREISE kommen
   ausschliesslich aus der Paketliste (Einstellungen) - das Modell nennt nur die Paket-Nummer, damit
   nie ein erfundener Preis im Angebot steht. Preise sind brutto inkl. 19 % MwSt.
4. Angebots-PDF + Analyse-PDF als Mail-Entwurf -> Telegram mit Freigabe-Knopf. Nichts geht ungefragt
   raus. Nach dem Versand fasst der Nachfass-Agent nach, wenn der Kunde sich nicht meldet.
Online-Shop-Anfragen bekommen kein Angebot (macht ChefBlick nicht).
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

import agents_engine as agents
import kunden_pdf
import settings_store
import siggi_time
from followup_engine import OWN_DOMAINS, SKIP_PATTERNS

AGENT_ID = 'angebot'
DB_PATH = '/opt/stean/mails.db'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE_DIR, 'angebote')
MAX_AGE_DAYS = 3
SAME_ADDR_DAYS = 14
DEFAULT_PAKETE = [
    {'name': 'Starter / Visitenkarte', 'einmalig': 299.99, 'monatlich': 8,
     'leistungen': 'Eine Seite, mobiloptimiert, Kontaktweg, Google Maps, monatliche Updates, Hosting & SSL inklusive'},
    {'name': 'Business Basic', 'einmalig': 990, 'monatlich': 18,
     'leistungen': 'Kompletter Auftritt, Google-Unternehmensprofil, lokale SEO, DSGVO, Domain & Mail inklusive, '
                   '3 Updates pro Monat'},
    {'name': 'Enterprise & Software', 'einmalig': None, 'monatlich': 35,
     'leistungen': 'Web-Apps, Dashboards, CRM, API-Anbindung, unbegrenzte Pflege & VIP-Support'},
    {'name': 'ImmoPro (Immobilienverwaltung)', 'einmalig': 79, 'monatlich': 0,
     'leistungen': '14 Tage kostenlos testen, danach Einmalkauf, kein Abo'},
    {'name': 'ChefBlick Catering Software', 'einmalig': 149, 'monatlich': 0,
     'leistungen': 'Für Catering, Foodtruck und Partyservice. 14 Tage kostenlos, danach Einmalkauf, kein Abo'},
]
DEFAULT_CONFIG = {'pakete': DEFAULT_PAKETE, 'analyse': True, 'gueltig_tage': 30}
KEYWORDS = ('website', 'webseite', 'web-seite', 'homepage', 'internetseite', 'internetauftritt', 'internetpräsenz',
            'online-auftritt', 'onlineauftritt', 'relaunch', 'angebot', 'kostenvoranschlag', 'was kostet', 'preis',
            'kosten', 'software', 'programm', 'app ', 'buchungssystem', 'crm', 'seo', 'google', 'neue seite',
            'immopro', 'catering')
FREEMAIL = ('gmail.', 'googlemail.', 'gmx.', 'web.de', 't-online.', 'hotmail.', 'outlook.', 'live.', 'yahoo.',
            'icloud.', 'me.com', 'aol.', 'freenet.', 'mail.de', 'posteo.', 'proton', 'arcor.', '1und1.', 'online.de')


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_table():
    c = _conn()
    c.execute('''CREATE TABLE IF NOT EXISTS angebote (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mail_id INTEGER, addr TEXT, nummer TEXT, kunde TEXT, paket TEXT, summe_einmalig REAL, summe_monatlich REAL,
        pdf TEXT, analyse_pdf TEXT, draft_id INTEGER, status TEXT, grund TEXT, created_at TEXT)''')
    c.commit()
    c.close()


# ─── Einstellungen ────────────────────────────────────────────────────

def config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.update(settings_store.load_or_empty().get('angebot_settings') or {})
    return cfg


def _preis(text):
    """'990' / '299,99' / '1.290,00 €' -> Zahl, 'nach Aufwand' oder leer -> None (kein Festpreis)."""
    t = str(text or '').lower().replace('€', '').replace('eur', '').replace(' ', '')
    if t in ('', '-', 'nachaufwand', 'aufwand', 'aufanfrage'):
        return None
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    return round(float(t), 2)


def pakete_aus_text(text):
    """Eine Zeile pro Paket: Name | einmalig | monatlich | Leistungen  ('nach Aufwand' = ohne Festpreis)."""
    out = []
    for line in str(text or '').splitlines():
        parts = [p.strip() for p in line.split('|')]
        if not parts[0]:
            continue
        if len(parts) < 4:
            raise ValueError(f'Zeile „{line[:40]}“: bitte Name | einmalig | monatlich | Leistungen')
        try:
            out.append({'name': parts[0][:80], 'einmalig': _preis(parts[1]), 'monatlich': _preis(parts[2]) or 0,
                        'leistungen': ' | '.join(parts[3:])[:400]})
        except ValueError:
            raise ValueError(f'Preis in „{line[:40]}“ nicht lesbar (z.B. 990 oder 299,99 oder nach Aufwand)')
    if not out:
        raise ValueError('Mindestens ein Paket nötig')
    return out


def save_config(data):
    cfg = {'pakete': pakete_aus_text(data['pakete']) if 'pakete' in data else config()['pakete'],
           'analyse': bool(data.get('analyse', True)),
           'gueltig_tage': max(7, min(90, int(data.get('gueltig_tage') or 30)))}
    settings_store.update(lambda s: s.__setitem__('angebot_settings', cfg))
    return cfg


def letzte(limit=8):
    init_table()
    c = _conn()
    rows = [dict(r) for r in c.execute("SELECT nummer, kunde, addr, paket, summe_einmalig, summe_monatlich, status, "
                                       "grund, created_at FROM angebote ORDER BY id DESC LIMIT ?", (limit,))]
    c.close()
    return rows


# ─── KI ───────────────────────────────────────────────────────────────

def _claude(model, prompt, max_tokens, api_key):
    body = {'model': model, 'max_tokens': max_tokens, 'messages': [{'role': 'user', 'content': prompt}]}
    if 'sonnet' in model:
        body['thinking'] = {'type': 'disabled'}  # sonst frisst das Denken das Token-Budget
    r = requests.post('https://api.anthropic.com/v1/messages',
                      headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json=body, timeout=90).json()
    if r.get('stop_reason') == 'max_tokens':
        raise RuntimeError('Antwort abgeschnitten')
    text = ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        raise RuntimeError(f'Keine verwertbare Antwort: {text[:200] or r}')
    return json.loads(m.group(0))


def _pruefen(mail, api_key):
    prompt = (
        f"{siggi_time.now_line()}\n\n"
        "ChefBlick (Stefan Mutter) macht Websites fuer kleine Betriebe, Hosting und Software nach Mass - KEINE "
        "Online-Shops. Ist diese Mail eine echte Projektanfrage eines (moeglichen) Kunden, fuer die ein Angebot "
        "sinnvoll ist (neue Website, Relaunch, Software, Preisanfrage zu einem konkreten Vorhaben)? NEIN bei "
        "Werbung, Rechnungen, Lieferanten, Bewerbungen, Support-Fragen bestehender Kunden ohne neues Projekt, "
        "Terminbestaetigungen, reinen Rueckrufbitten ohne Inhalt.\n\n"
        f"VON: {mail['from_addr']}\nBETREFF: {mail['subject']}\n\n{(mail['body'] or '')[:3000]}\n\n"
        'Antworte NUR mit JSON: {"anfrage": true/false, "grund": "kurz", "online_shop": true/false, '
        '"name": "Vor- und Nachname oder leer", "firma": "oder leer", "anrede": "Sie|Du (wie der Kunde schreibt)", '
        '"branche": "", "bedarf": "1 Satz, was der Kunde will", "website": "bestehende Website-Adresse oder leer"}'
    )
    return _claude('claude-haiku-4-5-20251001', prompt, 500, api_key)


def _schreiben(mail, info, pakete, analyse, api_key):
    liste = '\n'.join(
        f"{i + 1}. {p['name']}: {'nach Aufwand' if p['einmalig'] is None else str(p['einmalig']) + ' EUR einmalig'}"
        f" + {p['monatlich']} EUR/Monat - {p['leistungen']}" for i, p in enumerate(pakete))
    ana = ''
    if analyse:
        z = analyse.get('zusammenfassung') or {}
        ana = (f"\nANALYSE DER BISHERIGEN WEBSITE {analyse.get('url')}: Note {analyse.get('gesamtnote', '?')}, "
               f"{analyse.get('gesamtpunkte', '?')}/100 Punkte. {analyse.get('gesamtbewertung', '')}\n"
               f"Schwaechen: {'; '.join(z.get('groesste_schwaechen') or [])}\n"
               f"Dringend: {'; '.join(z.get('dringendste_massnahmen') or [])}\n")
    du = info.get('anrede') == 'Du'
    prompt = (
        f"{siggi_time.now_line()}\n\n"
        "Du bist Stefan Mutter von ChefBlick (Webdesign, Hosting, Software aus Haag an der Amper, Oberbayern - "
        "direkt mit dem Chef, keine Agentur-Buerokratie). Erstelle zu dieser Anfrage ein Angebot.\n\n"
        f"ANFRAGE VON {mail['from_addr']} (Betreff: {mail['subject']}):\n{(mail['body'] or '')[:3000]}\n\n"
        f"ERKANNT: Name {info.get('name') or '-'}, Firma {info.get('firma') or '-'}, Branche {info.get('branche') or '-'}, "
        f"Bedarf: {info.get('bedarf') or '-'}\n{ana}\n"
        f"PAKETE (nur diese gibt es, Preise sind fest und brutto):\n{liste}\n\n"
        "Waehle das EINE Paket, das am besten passt (im Zweifel das kleinere, das den Bedarf erfuellt). Nenne im Text "
        "KEINE Preise und KEINE Zahlen zu Preisen - die stehen im Angebots-PDF. Optional bis zu 2 Zusatzleistungen, "
        "die der Kunde ausdruecklich wuenscht und die nicht im Paket stecken (ohne Preis, 'nach Absprache').\n"
        f"Texte auf Deutsch in der {'Du' if du else 'Sie'}-Form, konkret auf den Kunden bezogen, freundlich, kurz, "
        "kein Marketing-Sprech." + (" Wenn eine Analyse vorliegt, im Mailtext 1 Satz dazu, was dort auffiel, und dass "
                                     "sie als PDF anbei ist." if analyse else '') + "\n"
        "- einleitung: 2-4 Saetze fuer das Angebots-PDF (Bezug auf die Anfrage, was der Kunde bekommt).\n"
        "- paket_beschreibung: 1-2 Saetze, was das Paket konkret fuer DIESEN Kunden bedeutet.\n"
        "- mail_text: 4-7 Saetze Begleitmail mit Namen in der Anrede, Angebot anbei, naechster Schritt (kurzes "
        "Telefonat oder Rueckmeldung). KEINE Grussformel und KEINE Unterschrift (wird angehaengt), keine Links.\n"
        'Antworte NUR mit JSON: {"paket": Nummer, "betreff": "Angebot ... (kurz)", "titel": "Angebot fuer ...", '
        '"einleitung": "...", "paket_beschreibung": "...", "zusatz": [{"titel": "...", "beschreibung": "..."}], '
        '"mail_text": "..."}'
    )
    return _claude('claude-sonnet-5', prompt, 1500, api_key)


# ─── Ablauf ───────────────────────────────────────────────────────────

def _addr(text):
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', text or '')
    return m.group(0).lower() if m else None


def _website(info, addr):
    """Bestehende Website: aus der Mail, sonst die Firmendomain des Absenders (nicht bei Freemail)."""
    cand = (info.get('website') or '').strip()
    if not cand and addr:
        dom = addr.split('@', 1)[1]
        if not any(f in dom for f in FREEMAIL):
            cand = dom
    if not cand:
        return None
    url = cand if cand.startswith(('http://', 'https://')) else 'https://' + cand
    host = urlparse(url).hostname or ''
    if not host or host.endswith(OWN_DOMAINS):
        return None
    return url if agents.check_site(url).get('ok') else None


def _nummer(c):
    year = datetime.now().year
    n = c.execute('SELECT COUNT(*) FROM angebote WHERE nummer LIKE ?', (f'A-{year}-%',)).fetchone()[0]
    return f'A-{year}-{n + 1:03d}'


def _merken(c, mail, addr, status, grund, **extra):
    cols = ['mail_id', 'addr', 'status', 'grund', 'created_at', *extra]
    c.execute(f"INSERT INTO angebote ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
              (mail['id'], addr, status, str(grund)[:300], datetime.now().isoformat(timespec='seconds'), *extra.values()))
    c.commit()


def _bearbeiten(mail, cfg, api_key, create_draft_fn, audit_fn):
    addr = _addr(mail['from_addr'])
    c = _conn()
    try:
        since = (datetime.now() - timedelta(days=SAME_ADDR_DAYS)).isoformat()
        if c.execute("SELECT 1 FROM angebote WHERE addr=? AND status='entwurf' AND created_at>=?", (addr, since)).fetchone():
            return None
        info = _pruefen(mail, api_key)
        if not info.get('anfrage') or info.get('online_shop'):
            _merken(c, mail, addr, 'keine_anfrage' if not info.get('anfrage') else 'online_shop', info.get('grund', ''))
            return None
        kunde = info.get('firma') or info.get('name') or addr
        agents.step(AGENT_ID, f'Anfrage von {kunde} – bereite Angebot vor …',
                    plan=PLAN('done', 'active', 'open', 'open'))

        analyse, analyse_pdf = None, None
        url = _website(info, addr) if cfg.get('analyse') else None
        if url and audit_fn:
            agents.step(AGENT_ID, f'Analysiere die bisherige Website {url} …', plan=PLAN('done', 'active', 'open', 'open'))
            try:
                analyse_pdf, analyse = audit_fn(url)
                if analyse is not None:
                    analyse['url'] = url
            except Exception as e:
                agents.event(AGENT_ID, f'Website-Analyse {url} fehlgeschlagen: {e}', 'warn')

        agents.step(AGENT_ID, f'Schreibe das Angebot für {kunde} …', plan=PLAN('done', 'done', 'active', 'open'))
        pakete = cfg['pakete']
        res = _schreiben(mail, info, pakete, analyse, api_key)
        idx = int(res.get('paket') or 0) - 1
        if not 0 <= idx < len(pakete):
            raise RuntimeError(f"Ungültige Paket-Nummer {res.get('paket')}")
        p = pakete[idx]
        positionen = [{'titel': p['name'],
                       'beschreibung': ' '.join(x for x in (res.get('paket_beschreibung'), 'Enthalten: ' + p['leistungen']) if x),
                       'einmalig': p['einmalig'] if p['einmalig'] is not None else 'nach Aufwand',
                       'monatlich': p['monatlich'] or None}]
        for z in (res.get('zusatz') or [])[:2]:
            if z.get('titel'):
                positionen.append({'titel': str(z['titel'])[:80], 'beschreibung': str(z.get('beschreibung', ''))[:300],
                                   'einmalig': 'nach Absprache', 'monatlich': None})
        heute = datetime.now()
        nummer = _nummer(c)
        a = {'nummer': nummer, 'datum': heute.strftime('%d.%m.%Y'),
             'gueltig_bis': (heute + timedelta(days=cfg.get('gueltig_tage', 30))).strftime('%d.%m.%Y'),
             'kunde': {'name': info.get('name'), 'firma': info.get('firma'), 'email': addr},
             'betreff': res.get('titel') or 'Ihr Angebot', 'einleitung': res.get('einleitung', ''),
             'positionen': positionen, 'analyse_angehaengt': bool(analyse_pdf)}
        os.makedirs(PDF_DIR, exist_ok=True)
        slug = re.sub(r'[^A-Za-z0-9]+', '_', kunde)[:40].strip('_') or 'Kunde'
        pdf = os.path.join(PDF_DIR, f'Angebot_{nummer}_{slug}.pdf')
        kunden_pdf.angebot(a, pdf)

        agents.step(AGENT_ID, 'Lege dir das Angebot zur Freigabe vor …', plan=PLAN('done', 'done', 'done', 'active'))
        files = [pdf] + ([analyse_pdf] if analyse_pdf and os.path.isfile(analyse_pdf) else [])
        subject = res.get('betreff') or f'Ihr Angebot von ChefBlick ({nummer})'
        draft_id = create_draft_fn(addr, subject, (res.get('mail_text') or '').strip(), attachments=files)
        e = p['einmalig'] or 0
        _merken(c, mail, addr, 'entwurf', info.get('bedarf', ''), nummer=nummer, kunde=kunde, paket=p['name'],
                summe_einmalig=e, summe_monatlich=p['monatlich'], pdf=pdf, analyse_pdf=analyse_pdf, draft_id=draft_id)
        agents.event(AGENT_ID, f"Angebot {nummer} für {kunde} ({p['name']}) wartet auf deine Freigabe", 'success')
        return draft_id
    finally:
        c.close()


PLAN = lambda *st: [{'label': l, 'state': x} for l, x in zip(
    ['Neue Mails auf Projektanfragen prüfen', 'Bisherige Website analysieren', 'Paket wählen und Angebot schreiben',
     'Angebot zur Freigabe schicken'], st)]


def run(create_draft_fn, audit_fn=None):
    """Alle paar Minuten: neue Mails seit dem letzten Lauf. Beim allerersten Lauf wird nur der Stand gemerkt
    (alte Mails bekommen kein Angebot)."""
    init_table()
    c = _conn()
    max_id = c.execute('SELECT COALESCE(MAX(id), 0) FROM mails').fetchone()[0]
    c.close()
    last = settings_store.load_or_empty().get('angebot_last_mail_id')
    if last is None:
        settings_store.update(lambda s: s.__setitem__('angebot_last_mail_id', max_id))
        return []
    if max_id <= last or agents.is_paused(AGENT_ID):  # pausiert: Stand bleibt, Anfragen werden danach nachgeholt
        return []
    settings = settings_store.load_or_empty()
    own = {a.lower() for a in (settings.get('accounts') or {})}
    oldest = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    c = _conn()
    mails = [dict(r) for r in c.execute(
        'SELECT id, from_addr, subject, body, created_at FROM mails WHERE id>? AND id<=? AND is_spam=0 AND is_auto_reply=0 '
        "AND category != 'spam' AND COALESCE(deleted, 0)=0 ORDER BY id", (last, max_id))]
    c.close()
    todo = []
    for m in mails:
        a = _addr(m['from_addr'])
        text = f"{m['subject']} {m['body']}".lower()
        if (a and a not in own and not a.endswith(OWN_DOMAINS) and not any(p in a for p in SKIP_PATTERNS)
                and (m['created_at'] or '') >= oldest and any(k in text for k in KEYWORDS)):
            todo.append(m)
    settings_store.update(lambda s: s.__setitem__('angebot_last_mail_id', max_id))
    if not todo:
        return []
    if not agents.start(AGENT_ID, f'Prüfe {len(todo)} neue Mail(s) auf Projektanfragen …',
                        plan=PLAN('active', 'open', 'open', 'open')):
        return []
    cfg = config()
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    created = []
    try:
        for m in todo:
            try:
                d = _bearbeiten(m, cfg, api_key, create_draft_fn, audit_fn)
                if d:
                    created.append(d)
            except Exception as e:
                agents.event(AGENT_ID, f"Mail „{(m['subject'] or '')[:60]}“: {e}", 'error')
        agents.done(AGENT_ID, f'{len(created)} Angebot(e) zur Freigabe' if created else 'Keine neue Projektanfrage',
                    plan=PLAN('done', 'done', 'done', 'done'), quiet=not created)
        return created
    except Exception as e:
        agents.fail(AGENT_ID, e)
        raise


def test_angebot(mail_id, create_draft_fn, audit_fn=None):
    """Fuer 'Jetzt starten' mit einer bestimmten Mail (Dashboard): ignoriert Vorfilter und Stand."""
    init_table()
    c = _conn()
    m = c.execute('SELECT id, from_addr, subject, body, created_at FROM mails WHERE id=?', (mail_id,)).fetchone()
    c.close()
    if not m:
        raise ValueError('Mail nicht gefunden')
    settings = settings_store.load_or_empty()
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    if not agents.start(AGENT_ID, 'Erstelle Angebot zu einer ausgewählten Mail …', plan=PLAN('active', 'open', 'open', 'open')):
        raise ValueError('Angebots-Agent ist pausiert')
    try:
        d = _bearbeiten(dict(m), config(), api_key, create_draft_fn, audit_fn)
        agents.done(AGENT_ID, 'Angebot zur Freigabe' if d else 'Keine Projektanfrage erkannt', plan=PLAN('done', 'done', 'done', 'done'))
        return d
    except Exception as e:
        agents.fail(AGENT_ID, e)
        raise
