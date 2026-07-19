# -*- coding: utf-8 -*-
"""KI-gestuetzte Gesamtbewertung + PageSpeed (mobile+desktop) fuer den Website-Audit"""
import json, re, requests

CATS = ['design', 'seo', 'technik', 'mobile', 'inhalte', 'rechtliches']


def fetch_pagespeed_full(url, strategy, api_key=None):
    try:
        params = {
            'url': url, 'strategy': strategy,
            'category': ['performance', 'seo', 'accessibility', 'best-practices']
        }
        if api_key:
            params['key'] = api_key
        r = requests.get('https://www.googleapis.com/pagespeedonline/v5/runPagespeed',
                         params=params, timeout=150)
        if r.status_code != 200:
            return None
        data = r.json()
        cats = data.get('lighthouseResult', {}).get('categories', {})
        audits = data.get('lighthouseResult', {}).get('audits', {})

        def sc(key):
            v = cats.get(key, {}).get('score')
            return round(v * 100) if v is not None else None

        return {
            'performance': sc('performance'),
            'seo': sc('seo'),
            'accessibility': sc('accessibility'),
            'best_practices': sc('best-practices'),
            'lcp': audits.get('largest-contentful-paint', {}).get('displayValue', ''),
            'cls': audits.get('cumulative-layout-shift', {}).get('displayValue', ''),
        }
    except Exception:
        return None


def fetch_pagespeed_both(url, api_key=None):
    mobile = fetch_pagespeed_full(url, 'mobile', api_key)
    desktop = fetch_pagespeed_full(url, 'desktop', api_key)
    return {
        'success': bool(mobile or desktop),
        'mobile': mobile or {},
        'desktop': desktop or {},
    }


def _extract_json(text):
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'```$', '', text).strip()
    return json.loads(text)


def ai_score_website(html, url, rule_findings, api_key):
    """Nutzt Claude fuer eine ganzheitliche, menschlich formulierte Bewertung."""
    if not api_key:
        return None

    # Kompaktes Signal-Set aus den Regel-Checks fuer den Prompt
    findings_summary = []
    for cat, items in rule_findings.items():
        for f in items:
            mark = 'OK' if f['status'] is True else ('FEHLT' if f['status'] is False else 'UNBEKANNT')
            findings_summary.append(f"[{cat}] {f['signal']}: {mark} - {f['description']}")

    text_only = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text_only = re.sub(r'<[^>]+>', ' ', text_only)
    text_only = re.sub(r'\s+', ' ', text_only).strip()[:2500]

    structural_hints = []
    h = html.lower()
    if re.search(r'<table[^>]*>.*<table', h, re.DOTALL):
        structural_hints.append("Verschachtelte Tabellen gefunden (typisch fuer alte Layouts)")
    if '<font' in h or '<marquee' in h or '<blink' in h:
        structural_hints.append("Veraltete HTML-Tags gefunden (font/marquee/blink)")
    if re.search(r'@media|grid-template|flex-direction|display:\s*grid|display:\s*flex', h):
        structural_hints.append("Moderne CSS-Techniken gefunden (Flexbox/Grid/Media-Queries)")
    if re.search(r'copyright.{0,20}(19|20)\d{2}', text_only.lower()):
        structural_hints.append("Copyright-Jahr im Footer gefunden")

    prompt = f"""Du bist ein erfahrener Webdesign-Berater der Agentur ChefBlick. Du bewertest die Website {url} fuer einen potenziellen Kunden, um daraus ein Verkaufsgespraech / Kostenvoranschlag vorzubereiten.

TECHNISCHE PRUEFERGEBNISSE (automatisiert):
{chr(10).join(findings_summary)}

STRUKTUR-HINWEISE:
{chr(10).join(structural_hints) if structural_hints else 'Keine besonderen Auffaelligkeiten'}

TEXTINHALT DER SEITE (Auszug):
{text_only}

AUFGABE:
Bewerte die Website ganzheitlich wie ein Profi, der einem Kunden ehrlich aber verkaufsorientiert erklaert, warum sich eine neue Website lohnt. Antworte NUR mit JSON in exakt diesem Format (keine Markdown-Codebloecke):

{{
  "gesamtnote": "A|B|C|D|F",
  "gesamtpunkte": 0-100,
  "gesamtbewertung": "2-3 Saetze Gesamteinschaetzung, menschlich und konkret formuliert",
  "ist_altbacken": true/false,
  "altbacken_begruendung": "1-2 Saetze warum die Seite modern oder veraltet wirkt",
  "kategorien": {{
    "design": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}},
    "seo": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}},
    "technik": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}},
    "mobile": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}},
    "inhalte": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}},
    "rechtliches": {{"note":"A-F","punkte":0-100,"bewertung":"1 Satz","probleme":["..."],"empfehlungen":["..."]}}
  }},
  "zusammenfassung": {{
    "gesamteindruck": "2-3 Saetze Gesamteindruck der Website, konkret auf diese Seite bezogen",
    "groesste_staerken": ["konkrete Staerke 1", "konkrete Staerke 2"],
    "groesste_schwaechen": ["konkrete Schwaeche 1", "konkrete Schwaeche 2"],
    "dringendste_massnahmen": ["dringendste Massnahme 1", "dringendste Massnahme 2"],
    "quick_wins": ["schnell umsetzbare Verbesserung mit grossem Effekt 1", "..."],
    "langfristige_optimierungen": ["strategische, laengerfristige Verbesserung 1", "..."]
  }},
  "textqualitaet": {{
    "auffaelligkeiten_gefunden": true/false,
    "beispiele": ["konkretes Beispiel fuer Tippfehler/Grammatikfehler/doppeltes Wort aus dem Text, falls vorhanden"],
    "lesbarkeit": "gut|mittel|schwer",
    "bewertung": "1-2 Saetze zu Rechtschreibung, Grammatik und Lesbarkeit des Seitentexts"
  }}
}}

Jede Kategorie braucht 1-3 konkrete Probleme (falls vorhanden) und 1-3 konkrete Empfehlungen. Nutze die technischen Pruefergebnisse als Grundlage fuer seo/technik/mobile/rechtliches. Fuer design/inhalte nutze Textinhalt und Struktur-Hinweise. Sei ehrlich - wenn etwas gut ist, sag das auch. Fuer "zusammenfassung": leite alles direkt aus den technischen Pruefergebnissen und dem Textinhalt oben ab, keine Standardfloskeln - wenn etwas nicht zutrifft (z.B. keine dringenden Massnahmen), sag das ehrlich statt etwas zu erfinden. Fuer "textqualitaet": lies den Textinhalt oben aufmerksam durch und suche aktiv nach Tippfehlern, doppelten Woertern, unvollstaendigen Saetzen oder schwer verstaendlichen Formulierungen."""

    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            timeout=40
        )
        result = response.json()
        if 'content' not in result:
            return None
        return _extract_json(result['content'][0]['text'])
    except Exception:
        return None
