"""Kunden-Websites: gemeinsame Liste fuer Website-Waechter (Wartung + Monatsbericht) und Check-in-Agent.

- Jede Kunden-Website wird vom Website-Waechter (n8n) alle 15 Min. mitgeprueft: agents_engine haengt
  die Kunden-URLs beim Start an die eigene Liste an, jeder Check landet in agents.db/site_checks.
- Taeglich (Wartung): Kontaktformular erreichbar? Domain-Ablauf (RDAP - bei .de gibt DENIC kein
  Ablaufdatum heraus)? Verschlechterungen gehen per Telegram an Stefan.
- Monatsbericht: am Monatsersten fuer jede Website mit 'bericht' ein PDF (Erreichbarkeit, Ausfaelle,
  Ladezeit, SSL, Domain, Formular). Die Mail an den Kunden ist IMMER ein Entwurf mit Freigabe-Knopf
  in Telegram - nichts geht ungefragt an Kunden raus.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests

import agents_engine as agents
import kunden_pdf

DB_PATH = '/opt/stean/mails.db'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, 'wartungsberichte')
UA = {'User-Agent': 'Mozilla/5.0 (compatible; Siggi-Website-Waechter/1.0; +https://chefblick.de)'}
MONATE = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober',
          'November', 'Dezember']
FIELDS = ('kunde', 'ansprechpartner', 'email', 'url', 'anrede', 'golive', 'bericht', 'formular_url', 'notizen')
FORM_HINTS = ('<form', 'wpcf7', 'contact-form', 'kontaktformular', 'typeform', 'jotform', 'formspree',
              'hsforms', 'cognitoforms', 'tally.so', 'forms.office.com', 'docs.google.com/forms')
CONTACT_LINK = re.compile(r'href=["\']([^"\'#]*(?:kontakt|contact|anfrage|termin)[^"\'#]*)["\']', re.IGNORECASE)


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_table():
    c = _conn()
    c.execute('''CREATE TABLE IF NOT EXISTS kunden_websites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kunde TEXT, ansprechpartner TEXT, email TEXT, url TEXT,
        anrede TEXT DEFAULT 'Sie', golive TEXT, bericht INTEGER DEFAULT 1,
        formular_url TEXT, notizen TEXT,
        form_status TEXT, form_checked_url TEXT, domain_expiry TEXT, domain_note TEXT, daily_at TEXT,
        last_report_month TEXT, last_report_pdf TEXT,
        checkin_30 TEXT, checkin_90 TEXT,
        created_at TEXT)''')
    c.commit()
    c.close()


def normalize_url(url):
    url = (url or '').strip()
    if not url:
        return ''
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    p = urlparse(url)
    return f'{p.scheme}://{p.netloc.lower()}{p.path.rstrip("/")}'


def host_of(url):
    h = (urlparse(normalize_url(url)).hostname or '').lower()
    return h[4:] if h.startswith('www.') else h


# ─── Liste ────────────────────────────────────────────────────────────

def liste():
    init_table()
    c = _conn()
    rows = [dict(r) for r in c.execute('SELECT * FROM kunden_websites ORDER BY kunde COLLATE NOCASE')]
    c.close()
    for r in rows:
        r['stats_30'] = stats(r['url'], datetime.now() - timedelta(days=30), datetime.now())
    return rows


def get(kid):
    init_table()
    c = _conn()
    r = c.execute('SELECT * FROM kunden_websites WHERE id=?', (kid,)).fetchone()
    c.close()
    return dict(r) if r else None


def urls():
    """Fuer den Website-Waechter: alle Kunden-URLs."""
    try:
        init_table()
        c = _conn()
        out = [r[0] for r in c.execute("SELECT url FROM kunden_websites WHERE url != ''")]
        c.close()
        return out
    except Exception as e:
        print(f'[Kunden] URLs nicht lesbar: {e}')
        return []


def _clean(data):
    out = {k: str(data.get(k) or '').strip()[:500] for k in FIELDS}
    out['url'] = normalize_url(out['url'])
    out['formular_url'] = normalize_url(out['formular_url']) if out['formular_url'] else ''
    out['anrede'] = 'Du' if out['anrede'].lower() == 'du' else 'Sie'
    out['bericht'] = 1 if data.get('bericht') in (True, 1, '1', 'true', 'on') else 0
    if out['golive']:
        try:
            out['golive'] = datetime.strptime(out['golive'][:10], '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            raise ValueError('Go-live-Datum bitte als Datum (JJJJ-MM-TT)')
    if not out['url']:
        raise ValueError('Website fehlt')
    if out['email'] and not re.fullmatch(r'[\w.+-]+@[\w.-]+\.\w+', out['email']):
        raise ValueError('E-Mail-Adresse sieht nicht richtig aus')
    if out['bericht'] and not out['email']:
        raise ValueError('Für den Monatsbericht braucht Siggi die E-Mail des Kunden')
    return out


def speichern(data, kid=None):
    init_table()
    d = _clean(data)
    c = _conn()
    if kid:
        old = c.execute('SELECT golive FROM kunden_websites WHERE id=?', (kid,)).fetchone()
        if not old:
            c.close()
            raise ValueError('Kunde nicht gefunden')
        c.execute(f"UPDATE kunden_websites SET {', '.join(k + '=?' for k in FIELDS)} WHERE id=?",
                  (*[d[k] for k in FIELDS], kid))
        if old['golive'] != d['golive']:  # neues Go-live -> Check-ins neu
            c.execute('UPDATE kunden_websites SET checkin_30=NULL, checkin_90=NULL WHERE id=?', (kid,))
    else:
        c.execute(f"INSERT INTO kunden_websites ({', '.join(FIELDS)}, created_at) VALUES ({', '.join('?' * len(FIELDS))}, ?)",
                  (*[d[k] for k in FIELDS], datetime.now().isoformat(timespec='seconds')))
        kid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.commit()
    c.close()
    return kid


def loeschen(kid):
    init_table()
    c = _conn()
    c.execute('DELETE FROM kunden_websites WHERE id=?', (kid,))
    c.commit()
    c.close()


def _set(kid, **fields):
    c = _conn()
    c.execute(f"UPDATE kunden_websites SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), kid))
    c.commit()
    c.close()


# ─── Auswertung der Waechter-Checks ───────────────────────────────────

def stats(url, von, bis):
    """Erreichbarkeit/Ausfaelle/Ladezeit aus den Checks des Website-Waechters (alle 15 Min.).
    Ein Ausfall = mind. 2 Fehlschlaege hintereinander (wie der Alarm des Waechters)."""
    host = host_of(url)
    try:
        c = sqlite3.connect(agents.DB_PATH, timeout=10)
        rows = c.execute('SELECT ok, ms, ssl_days, checked_at FROM site_checks WHERE host=? AND checked_at>=? AND checked_at<? '
                         'ORDER BY checked_at', (host, von.isoformat(), bis.isoformat())).fetchall()
        c.close()
    except sqlite3.Error:
        rows = []
    if not rows:
        return {'checks': 0}
    ok = sum(1 for r in rows if r[0])
    times = [r[1] for r in rows if r[0] and r[1]]
    outages, run = [], []
    for r in rows + [(1, None, None, None)]:
        if not r[0]:
            run.append(r[3])
        else:
            if len(run) >= 2:
                outages.append({'von': run[0], 'bis': r[3] or run[-1], 'checks': len(run)})
            run = []
    return {
        'checks': len(rows), 'uptime': round(100 * ok / len(rows), 2),
        'avg_ms': int(sum(times) / len(times)) if times else None, 'max_ms': max(times) if times else None,
        'outages': outages, 'down_minutes': sum(o['checks'] for o in outages) * 15,
        'ssl_days': next((r[2] for r in reversed(rows) if r[2] is not None), None),
        'since': rows[0][3],
    }


# ─── Taegliche Wartung: Kontaktformular + Domain ──────────────────────

def _domain_expiry(url):
    """Ablaufdatum der Domain per RDAP (kostenlos). DENIC (.de) gibt keins heraus."""
    labels = host_of(url).split('.')
    domain = '.'.join(labels[-2:])
    if domain.endswith('.de'):
        return None, 'Domain: .de-Domains haben kein öffentliches Ablaufdatum, sie laufen mit dem Hosting-Vertrag weiter'
    try:
        r = requests.get(f'https://rdap.org/domain/{domain}', timeout=20, headers=UA)
        if r.status_code != 200:
            return None, f'Registry antwortet nicht ({r.status_code})'
        for ev in r.json().get('events', []):
            if ev.get('eventAction') == 'expiration':
                return ev['eventDate'][:10], None
        return None, 'Registry nennt kein Ablaufdatum'
    except Exception as e:
        return None, f'Abfrage fehlgeschlagen: {str(e)[:80]}'


def _check_form(site):
    """Kontaktseite erreichbar und Formular drauf? Abgeschickt wird nichts (sonst Spam beim Kunden)."""
    try:
        if site.get('formular_url'):
            candidates = [site['formular_url']]
        else:
            home = requests.get(site['url'], timeout=20, headers=UA)
            if home.status_code >= 400:
                return 'seite_fehler', site['url']
            if any(h in home.text.lower() for h in FORM_HINTS):
                return 'ok', home.url
            candidates = []
            for href in CONTACT_LINK.findall(home.text):
                full = urljoin(home.url, href)
                if host_of(full) == host_of(site['url']) and full not in candidates:
                    candidates.append(full)
            if not candidates:
                return 'nicht_gefunden', None
        for cand in candidates[:3]:
            r = requests.get(cand, timeout=20, headers=UA)
            if r.status_code >= 400:
                if cand == candidates[-1] or site.get('formular_url'):
                    return 'seite_fehler', cand
                continue
            if any(h in r.text.lower() for h in FORM_HINTS):
                return 'ok', r.url
        return 'nicht_gefunden', candidates[0]
    except Exception:
        return 'nicht_erreichbar', site.get('formular_url') or site['url']


FORM_LABEL = {'ok': 'Kontaktformular gefunden und erreichbar', 'nicht_gefunden': 'Kein Kontaktformular gefunden',
              'seite_fehler': 'Kontaktseite meldet einen Fehler', 'nicht_erreichbar': 'Kontaktseite nicht erreichbar'}


def daily_checks(only_id=None):
    """only_id: nur diese Website (gleich nach dem Eintragen, damit die Karte sofort Werte zeigt)."""
    init_table()
    c = _conn()
    sites = [dict(r) for r in c.execute('SELECT * FROM kunden_websites' + (' WHERE id=?' if only_id else ''),
                                        (only_id,) if only_id else ())]
    c.close()
    if not sites:
        return
    warnings = []
    for s in sites:
        form, form_url = _check_form(s)
        expiry, note = _domain_expiry(s['url'])
        if form != 'ok' and s.get('form_status') == 'ok':
            warnings.append(f"{s['kunde'] or s['url']}: {FORM_LABEL[form]} ({form_url or s['url']}) – gestern war es noch da")
        if expiry:
            days = (datetime.strptime(expiry, '%Y-%m-%d') - datetime.now()).days
            # einmal beim ersten Mal unter 30 Tagen, dann an festen Tagen erinnern
            if days in (30, 14, 7, 3, 1) or (days <= 30 and s.get('domain_expiry') != expiry):
                warnings.append(f"{s['kunde'] or s['url']}: Domain läuft in {days} Tagen ab ({expiry})")
        _set(s['id'], form_status=form, form_checked_url=form_url, domain_expiry=expiry, domain_note=note,
             daily_at=datetime.now().isoformat(timespec='seconds'))
    for w in warnings:
        agents.event('watch', w, 'warn')
    if warnings:
        agents.notify_stefan('⚠️ Website-Wächter (Kunden-Websites)\n\n' + '\n'.join(f'• {w}' for w in warnings),
                             mail_subject='Siggi: Kunden-Website braucht Aufmerksamkeit')
    elif not only_id:
        agents.event('watch', f'Tägliche Wartung: {len(sites)} Kunden-Website(s) geprüft – Formulare und Domains OK')


# ─── Monatsbericht ────────────────────────────────────────────────────

def _month_range(month):
    start = datetime.strptime(month + '-01', '%Y-%m-%d')
    end = (start + timedelta(days=32)).replace(day=1)
    return start, min(end, datetime.now())


def _de(n, digits=0):
    return f'{n:,.{digits}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def bericht_daten(site, month):
    start, end = _month_range(month)
    st = stats(site['url'], start, end)
    return {
        'kunde': site['kunde'] or host_of(site['url']), 'url': site['url'],
        'monat': f"{MONATE[start.month - 1]} {start.year}", 'month': month, 'stats': st,
        'form_status': site.get('form_status'), 'form_label': FORM_LABEL.get(site.get('form_status') or '', 'Noch nicht geprüft'),
        'domain_expiry': site.get('domain_expiry'), 'domain_note': site.get('domain_note'),
        'erstellt': datetime.now().strftime('%d.%m.%Y'),
    }


def _mail_text(site, d):
    st = d['stats']
    du = site['anrede'] == 'Du'
    name = (site.get('ansprechpartner') or '').strip()
    gruss = (f'Hallo {name},' if name else 'Hallo,') if du else (f'Guten Tag {name},' if name else 'Guten Tag,')
    if not st.get('checks'):
        kern = 'die Überwachung ist eingerichtet, die ersten Messwerte siehst du im nächsten Bericht.' if du else \
            'die Überwachung ist eingerichtet, die ersten Messwerte sehen Sie im nächsten Bericht.'
    else:
        n = len(st['outages'])
        kern = (f"{'deine' if du else 'Ihre'} Website war im {d['monat'].split()[0]} zu {_de(st['uptime'], 2)} % erreichbar"
                + (f" – {n} kurze{'r' if n == 1 else ''} Ausfall{'' if n == 1 else 'e'} wurde{'' if n == 1 else 'n'} erkannt und "
                   f"behoben." if n else ' – ohne einen einzigen Ausfall.'))
    bitte = 'Melde dich gerne, wenn du Fragen hast oder etwas an der Seite ändern möchtest.' if du else \
        'Melden Sie sich gerne, wenn Sie Fragen haben oder etwas an der Seite ändern möchten.'
    anbei = 'anbei bekommst du' if du else 'anbei erhalten Sie'
    return (f"{gruss}\n\n{anbei} den Wartungsbericht für {host_of(site['url'])} für {d['monat']}.\n\n"
            f"Kurz zusammengefasst: {kern}\n\n{bitte}")


def vormonat():
    return (datetime.now().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')


def bericht_erstellen(kid, month=None, create_draft_fn=None):
    """PDF bauen und (mit create_draft_fn) als Mail-Entwurf an den Kunden zur Freigabe legen.
    month: 'YYYY-MM', Standard = Vormonat."""
    site = get(kid)
    if not site:
        raise ValueError('Kunde nicht gefunden')
    month = month or vormonat()
    d = bericht_daten(site, month)
    os.makedirs(REPORT_DIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', host_of(site['url']))
    path = os.path.join(REPORT_DIR, f'Wartungsbericht_{slug}_{month}.pdf')
    kunden_pdf.wartungsbericht(d, path)
    _set(kid, last_report_pdf=path)
    draft_id = None
    if create_draft_fn and site.get('email'):
        draft_id = create_draft_fn(site['email'], f"Ihr Wartungsbericht {d['monat']} – {host_of(site['url'])}"
                                   if site['anrede'] != 'Du' else f"Dein Wartungsbericht {d['monat']} – {host_of(site['url'])}",
                                   _mail_text(site, d), attachments=[path])
        _set(kid, last_report_month=month)
        agents.event('watch', f"Wartungsbericht {d['monat']} für {d['kunde']} wartet auf deine Freigabe", 'success')
    return {'pdf': path, 'draft_id': draft_id, 'month': month}


def monthly_reports(create_draft_fn):
    month = vormonat()
    init_table()
    c = _conn()
    ids = [r[0] for r in c.execute("SELECT id FROM kunden_websites WHERE bericht=1 AND email != '' "
                                    "AND COALESCE(last_report_month, '') != ?", (month,))]
    c.close()
    done = 0
    for kid in ids:
        try:
            bericht_erstellen(kid, month, create_draft_fn)
            done += 1
        except Exception as e:
            agents.event('watch', f'Wartungsbericht für Kunde #{kid} fehlgeschlagen: {e}', 'error')
    return done
