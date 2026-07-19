# -*- coding: utf-8 -*-
"""STEAN Website Audit Engine - schnell, parallel, mit PDF-Report"""
import os, re, json, time, requests
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import audit_ai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT_RESULTS_PATH = os.path.join(BASE_DIR, 'audit_results')
LOGO_PATH = os.path.join(BASE_DIR, 'report_logo.png')
os.makedirs(AUDIT_RESULTS_PATH, exist_ok=True)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'}

CONTACT = {
    'name': 'ChefBlick',
    'contact_person': 'Stefan Mutter',
    'email': 'team@chefblick.de',
    'phone': '+49 155 65505025',
    'web': 'www.chefblick.de',
}

COOKIE_PATTERNS = [
    'cookiebot', 'borlabs', 'usercentrics', 'cookieconsent', 'cookie-consent',
    'cookie_consent', 'cookielaw', 'onetrust', 'complianz', 'iubenda',
    'klaro', 'termly', 'cookiefirst', 'cookie-banner', 'cookiebanner',
    'gdpr-cookie', 'cc-window', 'cookie-notice'
]


class AuditFinding:
    def __init__(self, signal, category, priority='MITTEL'):
        self.signal = signal
        self.category = category
        self.priority = priority
        self.description = ''
        self.status = None
        self.recommendation = ''
        self.legal_basis = ''  # Gesetzestext-Referenz - wird im PDF nur bei "Handlungsbedarf" angezeigt
        self.check_id = f"C{hash(signal) % 1000:03d}"

    def to_dict(self):
        return {
            'signal': self.signal,
            'category': self.category,
            'priority': self.priority,
            'description': self.description,
            'status': self.status,
            'recommendation': self.recommendation,
            'legal_basis': self.legal_basis,
            'check_id': self.check_id,
        }


def fetch_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        return r.text, r.status_code, r.url
    except Exception:
        return None, 0, url


def fetch_url_ok(url, timeout=8):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def fetch_pagespeed(url, api_key=None):
    """Google PageSpeed Insights - mit optionalem API-Key."""
    try:
        params = {'url': url, 'strategy': 'mobile', 'category': 'performance'}
        if api_key:
            params['key'] = api_key
        r = requests.get(
            'https://www.googleapis.com/pagespeedonline/v5/runPagespeed',
            params=params,
            timeout=25
        )
        if r.status_code != 200:
            return None
        data = r.json()
        score = data.get('lighthouseResult', {}).get('categories', {}).get('performance', {}).get('score')
        audits = data.get('lighthouseResult', {}).get('audits', {})
        lcp = audits.get('largest-contentful-paint', {}).get('displayValue', '')
        cls = audits.get('cumulative-layout-shift', {}).get('displayValue', '')
        fcp = audits.get('first-contentful-paint', {}).get('displayValue', '')
        return {
            'score': round(score * 100) if score is not None else None,
            'lcp': lcp, 'cls': cls, 'fcp': fcp
        }
    except Exception:
        return None


def analyze_indexing(html, url, domain, robots_ok, sitemap_ok):
    findings = []
    f1 = AuditFinding('Meta Robots Noindex', 'Indexierung', 'HOCH')
    has_noindex = bool(re.search(r'noindex', html, re.IGNORECASE))
    f1.status = not has_noindex
    f1.description = "Seite blockiert Google-Indexierung" if has_noindex else "Seite ist fuer Google indexierbar"
    f1.recommendation = "Meta-Robots-Tag auf 'index' setzen, damit Google die Seite listen kann." if has_noindex else ""
    findings.append(f1)

    f2 = AuditFinding('Robots.txt vorhanden', 'Indexierung', 'MITTEL')
    f2.status = robots_ok
    f2.description = "robots.txt gefunden" if robots_ok else "Keine robots.txt gefunden"
    f2.recommendation = "" if robots_ok else "robots.txt anlegen, um Crawlern klare Regeln zu geben."
    findings.append(f2)

    f3 = AuditFinding('XML Sitemap', 'Indexierung', 'MITTEL')
    f3.status = sitemap_ok
    f3.description = "Sitemap gefunden" if sitemap_ok else "Keine sitemap.xml gefunden"
    f3.recommendation = "" if sitemap_ok else "XML-Sitemap erstellen und bei Google Search Console einreichen."
    findings.append(f3)
    return findings


def analyze_technical(html, url):
    findings = []
    h = html.lower()

    f1 = AuditFinding('SSL/HTTPS', 'Technik', 'KRITISCH')
    f1.status = url.startswith('https://')
    f1.description = "Verbindung ist verschluesselt (HTTPS)" if f1.status else "Keine HTTPS-Verschluesselung"
    f1.recommendation = "" if f1.status else "SSL-Zertifikat einrichten - Pflicht fuer Sicherheit, Vertrauen und Google-Ranking."
    findings.append(f1)

    f2 = AuditFinding('Mobile Viewport Meta-Tag', 'Technik', 'HOCH')
    f2.status = bool(re.search(r'viewport', h))
    f2.description = "Viewport-Tag vorhanden" if f2.status else "Kein Viewport-Tag gefunden"
    f2.recommendation = "" if f2.status else "Viewport-Meta-Tag ergaenzen fuer korrekte mobile Darstellung."
    findings.append(f2)

    f3 = AuditFinding('Responsive Design', 'Technik', 'HOCH')
    f3.status = bool(re.search(r'@media|bootstrap|tailwind|flex|grid-template', h))
    f3.description = "Hinweise auf responsives Design gefunden" if f3.status else "Keine Hinweise auf responsives Design"
    f3.recommendation = "" if f3.status else "Seite fuer Smartphones und Tablets optimieren (Responsive Design)."
    findings.append(f3)
    return findings


def analyze_seo(html, url):
    findings = []

    f1 = AuditFinding('Meta Title Laenge', 'SEO', 'HOCH')
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip() if title_match else ''
    title_len = len(title)
    f1.status = 30 <= title_len <= 65
    f1.description = f"Title-Laenge: {title_len} Zeichen (ideal: 30-65)"
    f1.recommendation = "" if f1.status else "Seitentitel auf 30-65 Zeichen anpassen fuer bessere Klickrate in Google."
    findings.append(f1)

    f2 = AuditFinding('Meta Description Laenge', 'SEO', 'HOCH')
    desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    desc = desc_match.group(1) if desc_match else ''
    desc_len = len(desc)
    f2.status = 120 <= desc_len <= 160
    f2.description = f"Meta Description: {desc_len} Zeichen (ideal: 120-160)"
    f2.recommendation = "" if f2.status else "Meta-Description auf 120-160 Zeichen bringen - wichtig fuer Klickrate."
    findings.append(f2)

    f3 = AuditFinding('H1 Tag vorhanden', 'SEO', 'HOCH')
    h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE)
    f3.status = len(h1s) == 1
    f3.description = f"{len(h1s)} H1-Tag(s) gefunden (ideal: genau 1)"
    f3.recommendation = "" if f3.status else "Genau ein H1-Tag pro Seite verwenden - klare Hauptueberschrift fuer Google."
    findings.append(f3)

    f4 = AuditFinding('Canonical Link', 'SEO', 'MITTEL')
    f4.status = bool(re.search(r'rel=["\']canonical["\']', html, re.IGNORECASE))
    f4.description = "Canonical-Tag vorhanden" if f4.status else "Kein Canonical-Tag gefunden"
    f4.recommendation = "" if f4.status else "Canonical-Link ergaenzen, um doppelte Inhalte zu vermeiden."
    findings.append(f4)

    f5 = AuditFinding('Alt-Attribute bei Bildern', 'SEO', 'MITTEL')
    imgs = re.findall(r'<img[^>]*>', html, re.IGNORECASE)
    imgs_no_alt = [i for i in imgs if not re.search(r'alt=["\'][^"\']+["\']', i)]
    f5.status = len(imgs) == 0 or len(imgs_no_alt) / max(len(imgs), 1) < 0.2
    f5.description = f"{len(imgs) - len(imgs_no_alt)}/{len(imgs)} Bilder mit Alt-Text"
    f5.recommendation = "" if f5.status else "Alt-Texte fuer Bilder ergaenzen - wichtig fuer SEO und Barrierefreiheit."
    findings.append(f5)
    return findings


def analyze_content(html, url):
    findings = []
    f1 = AuditFinding('Mindestens 300 Woerter', 'Inhalte', 'MITTEL')
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    word_count = len(text.split())
    f1.status = word_count >= 300
    f1.description = f"Wort-Anzahl: {word_count} (Ziel: >= 300)"
    f1.recommendation = "" if f1.status else "Mehr hochwertigen Text ergaenzen - wichtig fuer SEO-Relevanz."
    findings.append(f1)

    f2 = AuditFinding('Interne Verlinkung', 'Inhalte', 'MITTEL')
    links = re.findall(r'href=["\']([^"\'#?]+)["\']', html, re.IGNORECASE)
    f2.status = len(links) >= 3
    f2.description = f"{len(links)} Links gefunden"
    f2.recommendation = "" if f2.status else "Mehr interne Verlinkung einbauen, um Nutzerfuehrung und SEO zu verbessern."
    findings.append(f2)
    return findings


SHOP_PATTERNS = [
    'warenkorb', 'in den warenkorb', 'zum warenkorb', 'checkout', 'zur kasse',
    'add-to-cart', 'add_to_cart', 'jetzt bestellen', 'jetzt kaufen', 'woocommerce',
    'shopware', 'shopify', 'magento', 'produkt-detail', 'preis inkl. mwst', 'inkl. mwst',
]

# Interne Links, die bevorzugt mitgecrawlt werden - erhoehen die Trefferquote fuer rechtliche
# Pflichtseiten (die oft nicht auf der Startseite selbst stehen, sondern nur verlinkt sind) und
# fuer die Einordnung des Sortiments (perishable vs. nicht-perishable, siehe PERISHABLE_HINTS).
CRAWL_PRIORITY_KEYWORDS = [
    'impressum', 'datenschutz', 'agb', 'widerruf', 'versand', 'lieferung', 'liefergebiet',
    'sortiment', 'produkte', 'shop', 'angebot', 'speisekarte', 'menu', 'menue', 'kategorie',
    'zahlung', 'kasse', 'warenkorb',
]

# Woerter, die auf tatsaechlich schnell verderbliche Ware/frisch zubereitete Speisen hindeuten -
# fuer diese greift die Ausnahme vom Widerrufsrecht nach Paragraph 312g Abs. 2 Nr. 2 BGB
# ("Waren, die schnell verderben koennen, wie frische Lebensmittel"). Verifiziert am 19.07.2026
# gegen den offiziellen Gesetzestext auf gesetze-im-internet.de.
PERISHABLE_HINTS = [
    'frisch zubereitet', 'frische lebensmittel', 'tagesgericht', 'gericht', 'speise', 'menue des tages',
    'mittagsmenue', 'pizza', 'sushi', 'doener', 'kebab', 'backwaren', 'baeckerei', 'konditorei',
    'catering', 'mahlzeit', 'essen bestellen', 'restaurant',
]

# Woerter, die auf NICHT schnell verderbliche Ware hindeuten (widerlegen eine vorschnelle
# Perishable-Einstufung, z.B. bei einem Getraenke-/Haushaltswaren-Lieferservice wie
# koller-lieferservice.de, wo die Ausnahme nicht greift).
NON_PERISHABLE_HINTS = [
    'getraenke', 'bier', 'mineralwasser', 'softdrink', 'limonade', 'saft', 'kasten',
    'haushaltsware', 'haushaltsprodukt', 'drogerie', 'elektronik', 'moebel', 'deko',
    'buecher', 'spielzeug', 'kleidung', 'schuhe', 'werkzeug',
]


def detect_online_shop(html):
    """Heuristik: erkennt, ob die Seite (vermutlich) ein Online-Shop mit Kaufabschluss ist -
    relevant fuer die Frage, ob ein Widerrufsbutton/-belehrung gesetzlich noetig ist."""
    h = html.lower()
    return any(p in h for p in SHOP_PATTERNS)


def crawl_site(base_html, base_url, domain, max_pages=8, timeout=8):
    """Folgt internen Links von der Startseite aus, um rechtliche Pflichtseiten (Impressum,
    Widerruf, AGB etc.) und das tatsaechliche Sortiment zu finden - diese stehen haeufig nicht
    auf der Startseite selbst. Liefert den kombinierten HTML-Text aller gecrawlten Seiten plus
    die Liste der besuchten URLs, damit im Report nachvollziehbar bleibt, was geprueft wurde."""
    scheme = urlparse(base_url).scheme
    raw_links = re.findall(r'href=["\']([^"\'#?]+)', base_html, re.IGNORECASE)

    candidates = []
    seen = {base_url}
    for link in raw_links:
        if link.startswith('mailto:') or link.startswith('tel:') or link.startswith('javascript:'):
            continue
        if link.startswith('http'):
            if urlparse(link).netloc != domain:
                continue
            full = link
        elif link.startswith('/'):
            full = f"{scheme}://{domain}{link}"
        else:
            continue
        full = full.split('#')[0]
        if full in seen:
            continue
        seen.add(full)
        score = sum(1 for kw in CRAWL_PRIORITY_KEYWORDS if kw in full.lower())
        if score > 0:
            candidates.append((score, full))

    candidates.sort(key=lambda x: -x[0])
    to_fetch = [url for _, url in candidates[:max_pages]]

    visited_pages = {base_url: base_html}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(fetch_page, u): u for u in to_fetch}
            for fut in as_completed(futures):
                u = futures[fut]
                try:
                    page_html, status, _ = fut.result()
                    if page_html and status == 200:
                        visited_pages[u] = page_html
                except Exception:
                    pass

    combined_html = '\n'.join(visited_pages.values())
    return combined_html, list(visited_pages.keys())


def classify_perishable(combined_html):
    """Grobe Einordnung, ob das erkennbare Sortiment ueberwiegend aus schnell verderblicher
    Ware/frisch zubereiteten Speisen besteht (Ausnahme Paragraph 312g Abs. 2 Nr. 2 BGB) oder
    nicht (z.B. Getraenke, Haushaltswaren - dort greift die Ausnahme NICHT). Bei Unklarheit
    (keine oder gemischte Signale) wird konservativ 'nicht verderblich' angenommen, damit der
    Report im Zweifel eher zu einem KRITISCH-Fund als zu einer falschen Entwarnung neigt."""
    h = combined_html.lower()
    perishable_hits = [kw for kw in PERISHABLE_HINTS if kw in h]
    non_perishable_hits = [kw for kw in NON_PERISHABLE_HINTS if kw in h]
    if perishable_hits and not non_perishable_hits:
        return True, perishable_hits, non_perishable_hits
    return False, perishable_hits, non_perishable_hits


def analyze_legal(html, url, combined_html=None, crawled_urls=None):
    """combined_html: HTML aller gecrawlten Seiten (Startseite + Unterseiten), falls vorhanden -
    macht die Pruefung deutlich zuverlaessiger, weil Impressum/Widerruf/AGB oft auf Unterseiten
    stehen. Faellt auf reines Startseiten-HTML zurueck, wenn kein Crawl durchgefuehrt wurde."""
    findings = []
    search_html = combined_html or html
    h = search_html.lower()
    crawled_urls = crawled_urls or [url]

    f1 = AuditFinding('Impressum vorhanden', 'Rechtlich', 'KRITISCH')
    f1.status = bool(re.search(r'impressum', h))
    f1.description = "Impressum verlinkt/gefunden" if f1.status else "Kein Impressum gefunden"
    f1.recommendation = "" if f1.status else "Impressum ergaenzen - in Deutschland gesetzlich Pflicht (Paragraph 5 TMG)."
    f1.legal_basis = "" if f1.status else (
        "Paragraph 5 Telemediengesetz (TMG): Diensteanbieter muessen auf ihrer Website leicht erkennbar, "
        "unmittelbar erreichbar und staendig verfuegbar u.a. Name, Anschrift und Kontaktmoeglichkeiten "
        "bereithalten. Verstoesse koennen als Wettbewerbsverstoss abgemahnt werden."
    )
    findings.append(f1)

    f2 = AuditFinding('Datenschutzerklaerung vorhanden', 'Rechtlich', 'KRITISCH')
    f2.status = bool(re.search(r'datenschutz|privacy.?policy', h))
    f2.description = "Datenschutzerklaerung gefunden" if f2.status else "Keine Datenschutzerklaerung gefunden"
    f2.recommendation = "" if f2.status else "Datenschutzerklaerung ergaenzen - DSGVO-Pflicht."
    f2.legal_basis = "" if f2.status else (
        "Art. 13 DSGVO (Datenschutz-Grundverordnung): Bei jeder Erhebung personenbezogener Daten (z.B. "
        "durch Kontaktformulare, Cookies, Tracking) muss der Nutzer transparent ueber Zweck, Umfang und "
        "Rechtsgrundlage der Datenverarbeitung informiert werden."
    )
    findings.append(f2)

    f3 = AuditFinding('Cookie-Banner vorhanden', 'Rechtlich', 'KRITISCH')
    f3.status = any(p in h for p in COOKIE_PATTERNS) or bool(re.search(r'cookie', h))
    f3.description = "Cookie-Hinweis/Banner gefunden" if f3.status else "Kein Cookie-Banner erkannt"
    f3.recommendation = "" if f3.status else "Cookie-Consent-Banner einbauen (z.B. Cookiebot, Borlabs) - Pflicht bei Cookies/Tracking laut DSGVO/TTDSG."
    f3.legal_basis = "" if f3.status else (
        "Paragraph 25 TTDSG (Telekommunikation-Telemedien-Datenschutz-Gesetz), i.V.m. Art. 6 DSGVO: "
        "Fuer nicht technisch notwendige Cookies (z.B. Tracking, Marketing) ist vorherige, aktive "
        "Einwilligung des Nutzers erforderlich - eine reine Information reicht nicht aus."
    )
    findings.append(f3)

    f4 = AuditFinding('SSL-Verschluesselung (rechtlich)', 'Rechtlich', 'KRITISCH')
    f4.status = url.startswith('https://')
    f4.description = "HTTPS aktiv" if f4.status else "Keine HTTPS-Verschluesselung"
    f4.recommendation = "" if f4.status else "SSL zwingend erforderlich fuer DSGVO-konforme Datenuebertragung."
    f4.legal_basis = "" if f4.status else (
        "Art. 32 DSGVO: Verantwortliche muessen geeignete technische Massnahmen treffen, um "
        "personenbezogene Daten bei der Uebertragung zu schuetzen - eine unverschluesselte Verbindung "
        "(HTTP statt HTTPS) gilt als Verstoss gegen diese Pflicht."
    )
    findings.append(f4)

    is_shop = detect_online_shop(search_html)
    f5 = AuditFinding('Widerrufsbutton / Widerrufsbelehrung', 'Rechtlich', 'KRITISCH' if is_shop else 'INFO')
    if is_shop:
        has_widerruf = bool(re.search(r'widerruf', h))
        is_perishable, per_hits, nonper_hits = classify_perishable(search_html)
        if has_widerruf:
            f5.status = True
            f5.description = f"Widerrufsbelehrung bzw. Widerrufsfunktion gefunden (geprueft ueber {len(crawled_urls)} Seite(n))."
            f5.recommendation = ""
        elif is_perishable:
            f5.status = True
            f5.priority = 'INFO'
            f5.description = (
                "Diese Seite hat Merkmale eines Online-Shops, aber es wurde keine Widerrufsbelehrung gefunden. "
                f"Da das Sortiment auf schnell verderbliche Ware/frisch zubereitete Speisen hindeutet ({', '.join(per_hits[:3])}), "
                "greift moeglicherweise die gesetzliche Ausnahme nach Paragraph 312g Abs. 2 Nr. 2 BGB "
                "('Waren, die schnell verderben koennen'). Diese Einschaetzung basiert auf einer automatischen "
                "Keyword-Analyse und ersetzt keine Rechtsberatung - bitte im Einzelfall pruefen lassen."
            )
            f5.recommendation = (
                "Rechtlich pruefen lassen, ob die Ausnahme fuer verderbliche Ware tatsaechlich auf das gesamte "
                "Sortiment zutrifft (z.B. nicht bei Getraenken oder verpackten Beilagen im selben Bestellvorgang)."
            )
        else:
            f5.status = False
            f5.description = (
                "Diese Seite hat Merkmale eines Online-Shops (Warenkorb/Kauf-Funktion), aber es wurde auf "
                f"{len(crawled_urls)} geprueften Seite(n) keine Widerrufsbelehrung bzw. keine Widerrufsfunktion gefunden. "
                + (f"Das Sortiment deutet auf nicht schnell verderbliche Ware hin ({', '.join(nonper_hits[:3])}), "
                   "die Ausnahme fuer verderbliche Ware greift daher voraussichtlich nicht." if nonper_hits else "")
            )
            f5.recommendation = (
                "Elektronische Widerrufsfunktion ergaenzen: Der Verbraucher muss einen Fernabsatzvertrag ueber "
                "eine klar erreichbare Funktion (Beschriftung z.B. 'Vertrag widerrufen') widerrufen koennen, mit "
                "Bestaetigungsschritt und sofortiger Empfangsbestaetigung auf dauerhaftem Datentraeger "
                "(Paragraph 356a BGB, in Verbindung mit dem Widerrufsrecht nach Paragraph 312g BGB)."
            )
            f5.legal_basis = (
                "Paragraph 312g BGB (Widerrufsrecht) i.V.m. Paragraph 356a BGB (Elektronische "
                "Widerrufsfunktion, verpflichtend seit 19.06.2026): Verbraucher haben bei online geschlossenen "
                "Fernabsatzvertraegen ein 14-taegiges Widerrufsrecht. Der Unternehmer muss auf der "
                "Online-Benutzeroberflaeche eine Widerrufsfunktion bereitstellen, mit der der Verbraucher eine "
                "Widerrufserklaerung abgeben und bestaetigen kann. Verifiziert am 19.07.2026 gegen den "
                "offiziellen Gesetzestext auf gesetze-im-internet.de."
            )
    else:
        f5.status = True
        f5.description = "Kein Online-Shop mit Kauffunktion erkannt - Widerrufsrecht daher hier nicht verpflichtend"
        f5.recommendation = ""
    findings.append(f5)
    return findings


ELEMENT_CHECKS = [
    ('has_ssl', 'SSL/HTTPS'),
    ('has_contact_form', 'Kontaktformular'),
    ('has_phone', 'Telefonnummer'),
    ('has_email', 'E-Mail-Adresse'),
    ('has_address', 'Adresse/Standort'),
    ('has_impressum', 'Impressum'),
    ('has_datenschutz', 'Datenschutz'),
    ('has_cookie_banner', 'Cookie-Banner'),
    ('has_google_maps', 'Google Maps'),
    ('has_social_links', 'Social Media Links'),
    ('has_reviews', 'Kundenbewertungen'),
    ('has_cta', 'Call-to-Action'),
    ('has_newsletter', 'Newsletter'),
    ('has_viewport', 'Mobile Viewport'),
    ('has_structured_data', 'Strukturierte Daten'),
]


def analyze_elements(html, url, combined_html=None):
    """Einfache An/Aus-Checkliste einzelner Website-Bausteine (fuer das Dashboard und den
    PDF-Report). Rein regelbasiert, unabhaengig von den Kategorie-Findings oben."""
    h = (combined_html or html).lower()
    return {
        'has_ssl': url.startswith('https://'),
        'has_contact_form': bool(re.search(r'<form', h)),
        'has_phone': bool(re.search(r'tel:\+?\d|(\+49[\s\-]?\d[\d\s\-/]{5,})|\b0\d{2,5}[\s/\-]\d{3,}', h)),
        'has_email': bool(re.search(r'mailto:|[\w.\-]+@[\w\-]+\.[a-z]{2,}', h)),
        'has_address': bool(re.search(r'\b\d{5}\b\s+[a-zäöüß]', h) or re.search(r'stra(ss|ß)e\s*\d', h)),
        'has_impressum': bool(re.search(r'impressum', h)),
        'has_datenschutz': bool(re.search(r'datenschutz|privacy.?policy', h)),
        'has_cookie_banner': any(p in h for p in COOKIE_PATTERNS) or bool(re.search(r'cookie', h)),
        'has_google_maps': bool(re.search(r'google\.[a-z.]+/maps|maps\.google', h)),
        'has_social_links': bool(re.search(r'facebook\.com/|instagram\.com/|linkedin\.com/|tiktok\.com/|(twitter|x)\.com/', h)),
        'has_reviews': bool(re.search(r'bewertung|testimonial|kundenstimme|trustpilot|google.?review|sternebewertung', h)),
        'has_cta': bool(re.search(r'jetzt anfragen|jetzt buchen|jetzt kaufen|termin vereinbaren|angebot anfordern|kontaktieren sie uns|jetzt bestellen', h)),
        'has_newsletter': bool(re.search(r'newsletter', h)),
        'has_viewport': bool(re.search(r'viewport', h)),
        'has_structured_data': bool(re.search(r'application/ld\+json|schema\.org', h)),
    }


def build_top_massnahmen(categories, limit=5):
    """Konsolidierte Top-Massnahmen aus allen offenen (status=False) Regel-Findings,
    priorisiert nach KRITISCH > HOCH > MITTEL."""
    order = {'KRITISCH': 0, 'HOCH': 1, 'MITTEL': 2, 'INFO': 3}
    aufwand_map = {'KRITISCH': 'mittel', 'HOCH': 'mittel', 'MITTEL': 'gering'}
    issues = []
    for items in categories.values():
        for f in items:
            if f.status is False and f.recommendation:
                issues.append({
                    'massnahme': f.signal,
                    'prioritaet': f.priority,
                    'begruendung': f.recommendation,
                    'aufwand': aufwand_map.get(f.priority, 'gering'),
                })
    issues.sort(key=lambda x: order.get(x['prioritaet'], 3))
    return issues[:limit]


def run_full_audit(url, progress_cb=None, pagespeed_api_key=None, anthropic_api_key=None):
    """Kompletter Audit - parallelisiert fuer Geschwindigkeit."""
    if not url.startswith('http'):
        url = 'https://' + url

    def report(pct):
        if progress_cb:
            try:
                progress_cb(pct)
            except Exception:
                pass

    report(5)
    t_start = time.time()
    html, status_code, final_url = fetch_page(url)

    result = {'url': url, 'timestamp': datetime.now().isoformat(), 'findings': {}, 'summary': {}}

    if not html:
        result['error'] = 'Seite nicht erreichbar'
        report(100)
        return result

    domain = urlparse(final_url).netloc
    report(15)

    # Parallel: robots.txt, sitemap.xml, interne Unterseiten (Impressum/Widerruf/Sortiment) crawlen
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_robots = ex.submit(fetch_url_ok, f"{urlparse(final_url).scheme}://{domain}/robots.txt")
        fut_sitemap = ex.submit(fetch_url_ok, f"{urlparse(final_url).scheme}://{domain}/sitemap.xml")
        fut_crawl = ex.submit(crawl_site, html, final_url, domain)

        robots_ok = fut_robots.result()
        report(40)
        sitemap_ok = fut_sitemap.result()
        report(55)
        combined_html, crawled_urls = fut_crawl.result()
        report(70)
    pagespeed_full = None

    result['crawled_pages'] = crawled_urls

    categories = {
        'Indexierung': analyze_indexing(html, final_url, domain, robots_ok, sitemap_ok),
        'Technik': analyze_technical(html, final_url),
        'SEO': analyze_seo(html, final_url),
        'Inhalte': analyze_content(html, final_url),
        'Rechtlich': analyze_legal(html, final_url, combined_html, crawled_urls),
    }

    # Performance-Kategorie aus PageSpeed
    perf_findings = []
    fp = AuditFinding('PageSpeed Performance-Score', 'Performance', 'HOCH')
    fp.status = None
    fp.description = "PageSpeed-Messung laeuft im Hintergrund nach (dauert ca. 30-90 Sekunden, erscheint automatisch im Dashboard)"
    fp.recommendation = ""
    perf_findings.append(fp)
    categories['Performance'] = perf_findings

    for cat_name, findings in categories.items():
        result['findings'][cat_name] = [f.to_dict() for f in findings]

    result['html_analysis'] = dict(result['findings'])
    result['html_analysis']['elements'] = analyze_elements(html, final_url, combined_html)

    total_findings = sum(len(f) for f in categories.values())
    all_flat = [f for cat in categories.values() for f in cat]
    issues = [f for f in all_flat if f.status is False]
    critical = sum(1 for f in issues if f.priority == 'KRITISCH')
    passed = sum(1 for f in all_flat if f.status is True)
    score = round(100 * passed / max(len([f for f in all_flat if f.status is not None]), 1))

    result['summary'] = {
        'total_findings': total_findings,
        'issues_count': len(issues),
        'critical_issues': critical,
        'score': score,
        'categories': list(categories.keys()),
        'duration_seconds': round(time.time() - t_start, 1),
    }
    result['pagespeed'] = pagespeed_full or {'success': False, 'mobile': {}, 'desktop': {}}
    report(92)

    ai_result = audit_ai.ai_score_website(html, final_url, result['findings'], anthropic_api_key)
    if not ai_result:
        ai_result = {
            'gesamtnote': 'C', 'gesamtpunkte': score,
            'gesamtbewertung': 'Automatische Bewertung basierend auf technischen Pruefungen (KI-Analyse nicht verfuegbar).',
            'ist_altbacken': None, 'altbacken_begruendung': '',
            'kategorien': {}
        }
    ai_result['top_massnahmen'] = build_top_massnahmen(categories)
    result['ai_result'] = ai_result

    report(100)
    return result
