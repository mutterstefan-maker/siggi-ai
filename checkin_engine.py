"""Check-in-Agent: meldet sich 30 und 90 Tage nach dem Go-live einer Kunden-Website beim Kunden.

"Laeuft alles? Kommen Anfragen ueber die Seite?" plus EIN passender Vorschlag fuer den naechsten
Schritt (lokale SEO, Google-Profil, Wartungsbericht, Software ...). Grundlage ist die Liste der
Kunden-Websites (kunden_engine, gepflegt im Website-Waechter). Claude Haiku schreibt den Text
(Bruchteil eines Cents), der Entwurf kommt per Telegram mit Freigabe-Knopf - nie direkt verschickt.
"""
import json
import os
import re
from datetime import datetime, timedelta

import requests

import agents_engine as agents
import kunden_engine
import settings_store
import siggi_time

AGENT_ID = 'checkin'
# Stufe -> (ab Tag, bis Tag): wer erst spaet eingetragen wird, bekommt keine veraltete Stufe mehr
STUFEN = {'30': (30, 60), '90': (90, 150)}
MAX_PER_RUN = 5
VORSCHLAEGE = (
    "- Google-Unternehmensprofil und lokale SEO (im Paket Business Basic, 990 EUR + 18 EUR/Monat) - bringt "
    "Anfragen aus der Umgebung\n"
    "- Upgrade von Starter auf Business Basic: kompletter Auftritt mit mehreren Seiten, 3 Updates im Monat\n"
    "- Monatlicher Wartungsbericht: Erreichbarkeit, SSL, Kontaktformular - Siggi prueft rund um die Uhr\n"
    "- Software nach Mass (nach Aufwand): Terminbuchung, Buchungssystem, CRM, Kalkulation, Dashboards\n"
    "- Regelmaessige Beitraege fuer Instagram/Facebook\n"
    "NICHT anbieten: Online-Shops (macht ChefBlick nicht)."
)


def faellige():
    """[(site, stufe, tage_seit_golive)] fuer heute."""
    out = []
    today = datetime.now().date()
    for s in kunden_engine.liste():
        if not s.get('golive') or not s.get('email'):
            continue
        days = (today - datetime.strptime(s['golive'], '%Y-%m-%d').date()).days
        for stufe, (ab, bis) in STUFEN.items():
            if ab <= days < bis and not s.get(f'checkin_{stufe}'):
                out.append((s, stufe, days))
    return out


def naechste(limit=6):
    """Fuer die Agenten-Karte: die naechsten anstehenden Check-ins."""
    out = []
    today = datetime.now().date()
    for s in kunden_engine.liste():
        if not s.get('golive') or not s.get('email'):
            continue
        golive = datetime.strptime(s['golive'], '%Y-%m-%d').date()
        for stufe, (ab, bis) in STUFEN.items():
            if s.get(f'checkin_{stufe}') or (today - golive).days >= bis:
                continue
            out.append({'kunde': s['kunde'] or kunden_engine.host_of(s['url']), 'stufe': stufe,
                        'datum': max(golive + timedelta(days=ab), today).isoformat()})
            break
    return sorted(out, key=lambda x: x['datum'])[:limit]


def _schreiben(site, stufe, days, api_key):
    st = site.get('stats_30') or {}
    lage = (f"Die Website war in den letzten 30 Tagen zu {st['uptime']} % erreichbar, {len(st['outages'])} Ausfall/Ausfaelle."
            if st.get('checks') else 'Keine Messwerte zur Erreichbarkeit.')
    prompt = (
        f"{siggi_time.now_line()}\n\n"
        "Du schreibst fuer Stefan Mutter (ChefBlick, Webdesign/Hosting/Software aus Haag an der Amper) eine kurze, "
        f"persoenliche Mail an einen Kunden, dessen neue Website vor {days} Tagen online gegangen ist "
        f"({'erster Check-in nach einem Monat' if stufe == '30' else 'zweiter Check-in nach drei Monaten'}).\n\n"
        f"KUNDE: {site['kunde'] or '-'}\nANSPRECHPARTNER: {site.get('ansprechpartner') or '-'}\n"
        f"WEBSITE: {site['url']}\nANREDE: {site['anrede']}\nNOTIZEN VON STEFAN: {site.get('notizen') or '-'}\n"
        f"MONATSBERICHT AKTIV: {'ja' if site.get('bericht') else 'nein'}\nLAGE: {lage}\n\n"
        "Inhalt: ehrlich nachfragen, ob alles laeuft und ob ueber die Seite schon Anfragen kommen; anbieten, Kleinig"
        "keiten anzupassen. Dann GENAU EINEN passenden, unaufdringlichen Vorschlag fuer den naechsten Schritt aus "
        f"dieser Liste (der zu Kunde und Notizen passt; Wartungsbericht nur, wenn er noch nicht aktiv ist):\n{VORSCHLAEGE}\n\n"
        f"Regeln: Deutsch, {'Du-Form' if site['anrede'] == 'Du' else 'Sie-Form'}, 4-7 Saetze, warm und direkt wie ein "
        "Handwerker-Chef, kein Marketing-Sprech, keine Links, keine Preise ausser beim Vorschlag kurz 'ab'. Anrede mit "
        "Namen, wenn bekannt. KEINE Grussformel und KEINE Unterschrift (wird automatisch angehaengt).\n"
        'Antworte NUR mit JSON: {"betreff": "...", "text": "..."}'
    )
    r = requests.post('https://api.anthropic.com/v1/messages',
                      headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': 'claude-haiku-4-5-20251001', 'max_tokens': 800,
                            'messages': [{'role': 'user', 'content': prompt}]}, timeout=45).json()
    text = ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text')
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        raise RuntimeError(f'Keine verwertbare Antwort: {text[:200] or r}')
    return json.loads(m.group(0))


def run(create_draft_fn):
    plan = lambda *st: [{'label': l, 'state': x} for l, x in zip(
        ['Kunden mit Go-live vor 30/90 Tagen suchen', 'Persönliche Mail schreiben', 'Entwürfe zur Freigabe schicken'], st)]
    if not agents.start(AGENT_ID, 'Schaue, welche Kunden ein Check-in bekommen …', plan=plan('active', 'open', 'open')):
        return []
    try:
        due = faellige()[:MAX_PER_RUN]
        if not due:
            nxt = naechste(1)
            agents.done(AGENT_ID, 'Heute ist kein Check-in fällig', plan=plan('done', 'done', 'done'), quiet=True,
                        bubble=(f"Nächster Check-in: {nxt[0]['kunde']} am "
                                f"{datetime.fromisoformat(nxt[0]['datum']).strftime('%d.%m.')}") if nxt
                        else 'Trag im Website-Wächter Kunden mit Go-live-Datum ein')
            return []
        settings = settings_store.load_or_empty()
        api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
        created = []
        for i, (site, stufe, days) in enumerate(due):
            name = site['kunde'] or kunden_engine.host_of(site['url'])
            agents.step(AGENT_ID, f'Schreibe {name} ({days} Tage online) …', plan=plan('done', 'active', 'open'),
                        progress=i / len(due))
            try:
                res = _schreiben(site, stufe, days, api_key)
            except Exception as e:
                agents.event(AGENT_ID, f'{name}: {e}', 'warn')
                continue
            draft_id = create_draft_fn(site['email'], res.get('betreff') or 'Wie läuft die neue Website?',
                                       (res.get('text') or '').strip())
            kunden_engine._set(site['id'], **{f'checkin_{stufe}': f"{datetime.now():%Y-%m-%d} Entwurf #{draft_id}"})
            created.append(draft_id)
            agents.event(AGENT_ID, f'Check-in nach {stufe} Tagen an {name} wartet auf deine Freigabe', 'success')
        agents.done(AGENT_ID, f'{len(created)} Check-in-Mail(s) zur Freigabe', plan=plan('done', 'done', 'done'))
        return created
    except Exception as e:
        agents.fail(AGENT_ID, e)
        raise
