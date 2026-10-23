"""Shop-Agent: Shopware-6-Shop der Steler GmbH (graveda.de) - NUR lesen, gehoert zum Steler-Bereich.

Wofuer: Siggi schaut nach Artikel, Bestand und Bestellungen, wenn es im Steler-kChat darum geht, damit
Antwort-Entwuerfe stimmen. Trennung wie beim kChat-Agent: eigene Zugangsdatei, nie Chefblick/Privat.
- Zugang: Shopware-Integration (Admin → Einstellungen → System → Integrationen), OHNE Administrator-Recht.
  Zugangs-ID + Schluessel liegen in shopware_token.json (gitignored, chmod 600), nicht in settings.json.
- Im Code gibt es nur lesende Aufrufe: GET und POST /api/search/* (Shopware sucht per POST).
"""
import json
import os
import threading
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'shopware_token.json')  # {"url", "client_id", "client_secret"}

_lock = threading.Lock()
_access = {'token': None, 'bis': 0}


class NotConnected(Exception):
    pass


def _cfg():
    try:
        with open(TOKEN_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = None
    if not cfg or not cfg.get('client_id') or not cfg.get('client_secret'):
        raise NotConnected('Shop ist nicht verbunden')
    cfg['url'] = (cfg.get('url') or 'https://graveda.de').rstrip('/')
    return cfg


def status():
    try:
        cfg = _cfg()
        return {'connected': True, 'url': cfg['url']}
    except NotConnected:
        return {'connected': False}


def _bearer():
    with _lock:
        if _access['token'] and time.time() < _access['bis']:
            return _access['token']
        cfg = _cfg()
        r = requests.post(cfg['url'] + '/api/oauth/token', timeout=20, json={
            'grant_type': 'client_credentials', 'client_id': cfg['client_id'], 'client_secret': cfg['client_secret']})
        if r.status_code == 401:
            raise NotConnected('Shop lehnt die Zugangsdaten ab - Integration in Shopware prüfen')
        r.raise_for_status()
        d = r.json()
        _access.update(token=d['access_token'], bis=time.time() + int(d.get('expires_in', 600)) - 60)
        return _access['token']


def _search(entity, body):
    """Einziger Datenweg: Shopware-Suche (lesend)."""
    r = requests.post(f"{_cfg()['url']}/api/search/{entity}", json=body, timeout=30,
                      headers={'Authorization': 'Bearer ' + _bearer(), 'Accept': 'application/json'})
    if r.status_code == 403:
        try:
            fehlt = json.loads(r.json()['errors'][0]['detail']).get('missingPrivileges')
        except Exception:
            fehlt = None
        if isinstance(fehlt, dict):  # Shopware liefert je nach Abfrage Liste oder {"0": "order:read", ...}
            fehlt = list(fehlt.values())
        raise PermissionError(f"Der Siggi-Integration fehlt das Recht {', '.join(fehlt or [entity + ':read'])} "
                              f"(Shopware → Integrationen → Siggi → Rolle)")
    r.raise_for_status()
    return r.json().get('data') or []


def _preis(p):
    try:
        return round(float(p['price'][0]['gross']), 2)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def produkte(begriff, anzahl=10):
    """Artikel nach Artikelnummer oder Name: Bestand, verfuegbar (= Bestand minus offene Bestellungen), Preis."""
    begriff = str(begriff or '').strip()[:100]
    if not begriff:
        return []
    felder = ['productNumber', 'name', 'stock', 'availableStock', 'active', 'price', 'parentId', 'translated']
    body = {'limit': max(1, min(int(anzahl or 10), 25)),
            'filter': [{'type': 'equals', 'field': 'productNumber', 'value': begriff}],
            'includes': {'product': felder}}
    data = _search('product', body)
    if not data:  # Teil der Artikelnummer, z.B. "GP50" findet GP50-15-V2, GP50-5-7 ...
        body['filter'] = [{'type': 'contains', 'field': 'productNumber', 'value': begriff}]
        data = _search('product', body)
    if not data:
        body.pop('filter')
        body['term'] = begriff
        data = _search('product', body)
    out = []
    for p in data:
        name = p.get('name') or (p.get('translated') or {}).get('name') or ''
        out.append({'artikelnummer': p.get('productNumber'), 'name': name[:150],
                    'bestand': p.get('stock'), 'verfuegbar': p.get('availableStock'),
                    'aktiv': p.get('active'), 'preis_brutto': _preis(p), 'variante': bool(p.get('parentId'))})
    return out


def bestellung(nummer):
    """Bestellung nach Bestellnummer: Status, Zahlung, Versand, Positionen, Sendungsnummern."""
    nummer = str(nummer or '').strip()[:40]
    if not nummer:
        return None
    data = _search('order', {
        'limit': 1, 'filter': [{'type': 'equals', 'field': 'orderNumber', 'value': nummer}],
        'associations': {'lineItems': {}, 'deliveries': {'associations': {'stateMachineState': {}}},
                         'transactions': {'associations': {'stateMachineState': {}}},
                         'stateMachineState': {}, 'orderCustomer': {}}})
    if not data:
        return {'fehler': f'Keine Bestellung {nummer} im Shop'}
    o = data[0]

    def state(x):
        return ((x or {}).get('stateMachineState') or {}).get('name') or ((x or {}).get('stateMachineState') or {}).get('technicalName')

    kunde = o.get('orderCustomer') or {}
    lieferungen = o.get('deliveries') or []
    return {
        'bestellnummer': o.get('orderNumber'), 'datum': (o.get('orderDateTime') or '')[:16].replace('T', ' '),
        'status': state(o), 'zahlung': state((o.get('transactions') or [None])[-1]),
        'versand': [state(d) for d in lieferungen],
        'sendungsnummern': [t for d in lieferungen for t in (d.get('trackingCodes') or [])],
        'kunde': f"{kunde.get('firstName', '')} {kunde.get('lastName', '')}".strip(),  # nur Name, keine Adresse
        'summe_brutto': o.get('amountTotal'),
        'positionen': [{'artikelnummer': (li.get('payload') or {}).get('productNumber'), 'name': (li.get('label') or '')[:120],
                        'menge': li.get('quantity')} for li in (o.get('lineItems') or [])],
    }
