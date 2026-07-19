# -*- coding: utf-8 -*-
"""STEAN Website Audit Engine - schnell, parallel, mit PDF-Report"""
import os, re, json, time, requests
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import audit_ai
try:
    import audit_playwright
    PLAYWRIGHT_MODULE_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_MODULE_AVAILABLE = False

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
    def __init__(self, signal, category, priority='MITTEL', was_geprueft='', warum_wichtig=''):
        self.signal = signal
        self.category = category
        self.priority = priority
        self.was_geprueft = was_geprueft  # kurze Erklaerung, was genau geprueft wurde
        self.warum_wichtig = warum_wichtig  # verstaendliche Erklaerung fuer Unternehmer, warum das relevant ist
        self.description = ''  # das konkrete Ergebnis dieser Pruefung
        self.status = None
        self.recommendation = ''
        self.legal_basis = ''  # Gesetzestext-Referenz - wird im PDF nur bei "Handlungsbedarf" angezeigt
        self.check_id = f"C{hash(signal) % 1000:03d}"

    def to_dict(self):
        return {
            'signal': self.signal,
            'category': self.category,
            'priority': self.priority,
            'was_geprueft': self.was_geprueft,
            'warum_wichtig': self.warum_wichtig,
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
    f1 = AuditFinding('Meta Robots Noindex', 'Indexierung', 'HOCH',
                       was_geprueft="Ob ein Meta-Tag Google aktiv anweist, die Seite NICHT zu listen.",
                       warum_wichtig="Ist dieser Schalter versehentlich aktiv, taucht die Seite in Google gar nicht erst auf - selbst die beste Website bringt dann keine neuen Kunden.")
    has_noindex = bool(re.search(r'noindex', html, re.IGNORECASE))
    f1.status = not has_noindex
    f1.description = "Seite blockiert Google-Indexierung" if has_noindex else "Seite ist fuer Google indexierbar"
    f1.recommendation = "Meta-Robots-Tag auf 'index' setzen, damit Google die Seite listen kann." if has_noindex else ""
    findings.append(f1)

    f2 = AuditFinding('Robots.txt vorhanden', 'Indexierung', 'MITTEL',
                       was_geprueft="Ob unter /robots.txt eine Datei existiert, die Suchmaschinen-Crawlern Regeln vorgibt.",
                       warum_wichtig="Ohne robots.txt wissen Suchmaschinen nicht, welche Bereiche gecrawlt werden sollen - meist unproblematisch, aber bei groesseren Seiten hilfreich zur Steuerung.")
    f2.status = robots_ok
    f2.description = "robots.txt gefunden" if robots_ok else "Keine robots.txt gefunden"
    f2.recommendation = "" if robots_ok else "robots.txt anlegen, um Crawlern klare Regeln zu geben."
    findings.append(f2)

    f3 = AuditFinding('XML Sitemap', 'Indexierung', 'MITTEL',
                       was_geprueft="Ob unter /sitemap.xml eine Liste aller wichtigen Unterseiten vorhanden ist.",
                       warum_wichtig="Eine Sitemap hilft Google, alle Seiten schnell zu finden und zu indexieren - besonders wichtig bei neuen oder groesseren Websites.")
    f3.status = sitemap_ok
    f3.description = "Sitemap gefunden" if sitemap_ok else "Keine sitemap.xml gefunden"
    f3.recommendation = "" if sitemap_ok else "XML-Sitemap erstellen und bei Google Search Console einreichen."
    findings.append(f3)
    return findings


def check_www_redirect(url, domain):
    """Prueft, ob www- und non-www-Variante der Domain konsistent auf eine Version umleiten -
    fehlt das, kann Google beide Varianten als getrennte Seiten werten (Duplicate Content)."""
    try:
        scheme = urlparse(url).scheme
        bare = domain[4:] if domain.startswith('www.') else domain
        alt = f"www.{bare}" if not domain.startswith('www.') else bare
        r = requests.get(f"{scheme}://{alt}", headers=HEADERS, timeout=8, allow_redirects=True)
        final_domain = urlparse(r.url).netloc
        return final_domain == domain or final_domain == alt
    except Exception:
        return None


def check_https_redirect(domain):
    """Prueft, ob ein Aufruf per HTTP automatisch auf HTTPS umgeleitet wird."""
    try:
        r = requests.get(f"http://{domain}", headers=HEADERS, timeout=8, allow_redirects=True)
        return r.url.startswith('https://')
    except Exception:
        return None


def analyze_technical(html, url, domain=None):
    findings = []
    h = html.lower()

    f1 = AuditFinding('SSL/HTTPS', 'Technik', 'KRITISCH',
                       was_geprueft="Ob die Website verschluesselt (https://) statt unverschluesselt (http://) ausgeliefert wird.",
                       warum_wichtig="Ohne SSL zeigen Browser eine Warnung ('Nicht sicher') an, was Besucher abschreckt. Zusaetzlich ist HTTPS ein Google-Rankingfaktor und fuer DSGVO-konforme Datenuebertragung Pflicht.")
    f1.status = url.startswith('https://')
    f1.description = "Verbindung ist verschluesselt (HTTPS)" if f1.status else "Keine HTTPS-Verschluesselung"
    f1.recommendation = "" if f1.status else "SSL-Zertifikat einrichten - Pflicht fuer Sicherheit, Vertrauen und Google-Ranking."
    findings.append(f1)

    if domain:
        f1b = AuditFinding('HTTPS-Weiterleitung', 'Technik', 'HOCH',
                            was_geprueft="Ob ein Aufruf ueber http:// automatisch auf die sichere https://-Version umgeleitet wird.",
                            warum_wichtig="Ohne automatische Weiterleitung koennen Besucher (z.B. ueber alte Links oder Lesezeichen) auf der unverschluesselten Version landen, ohne es zu merken.")
        redirect_ok = check_https_redirect(domain)
        f1b.status = redirect_ok
        f1b.description = ("HTTP wird automatisch auf HTTPS umgeleitet" if redirect_ok else
                            "Keine automatische Weiterleitung von HTTP auf HTTPS erkannt" if redirect_ok is False else
                            "Konnte nicht geprueft werden")
        f1b.recommendation = "" if redirect_ok else "301-Weiterleitung von HTTP auf HTTPS im Webserver einrichten."
        findings.append(f1b)

        f1c = AuditFinding('www / non-www Weiterleitung', 'Technik', 'MITTEL',
                            was_geprueft="Ob die Domain mit und ohne 'www.' konsistent auf eine einzige Version umleitet.",
                            warum_wichtig="Ohne einheitliche Weiterleitung koennte Google beide Varianten als zwei getrennte, aehnliche Seiten werten (Duplicate Content), was das Ranking verschlechtern kann.")
        www_ok = check_www_redirect(url, domain)
        f1c.status = www_ok
        f1c.description = ("www- und non-www-Version leiten konsistent auf eine Version um" if www_ok else
                            "www- und non-www-Version scheinen nicht konsistent umzuleiten" if www_ok is False else
                            "Konnte nicht geprueft werden")
        f1c.recommendation = "" if www_ok else "301-Weiterleitung einrichten, damit nur eine Domain-Variante (mit oder ohne www) erreichbar ist."
        findings.append(f1c)

    f2 = AuditFinding('Mobile Viewport Meta-Tag', 'Technik', 'HOCH',
                       was_geprueft="Ob ein Viewport-Meta-Tag im Quellcode vorhanden ist, das dem Browser die richtige Skalierung fuer mobile Geraete vorgibt.",
                       warum_wichtig="Ohne diesen Tag wird die Seite auf Smartphones oft winzig klein oder verzerrt dargestellt - ein Grossteil der Besucher kommt heute ueber mobile Geraete.")
    f2.status = bool(re.search(r'viewport', h))
    f2.description = "Viewport-Tag vorhanden" if f2.status else "Kein Viewport-Tag gefunden"
    f2.recommendation = "" if f2.status else "Viewport-Meta-Tag ergaenzen fuer korrekte mobile Darstellung."
    findings.append(f2)

    f3 = AuditFinding('Responsive Design', 'Technik', 'HOCH',
                       was_geprueft="Ob technische Hinweise auf ein responsives (sich an die Bildschirmgroesse anpassendes) Layout vorhanden sind.",
                       warum_wichtig="Eine Seite, die sich nicht an verschiedene Bildschirmgroessen anpasst, ist auf Smartphones und Tablets oft schwer nutzbar und schreckt Besucher ab.")
    f3.status = bool(re.search(r'@media|bootstrap|tailwind|flex|grid-template', h))
    f3.description = "Hinweise auf responsives Design gefunden" if f3.status else "Keine Hinweise auf responsives Design"
    f3.recommendation = "" if f3.status else "Seite fuer Smartphones und Tablets optimieren (Responsive Design)."
    findings.append(f3)

    f4 = AuditFinding('Mixed Content', 'Technik', 'HOCH',
                       was_geprueft="Ob eine per HTTPS geladene Seite intern noch unverschluesselte (http://) Ressourcen wie Bilder oder Skripte einbindet.",
                       warum_wichtig="Mixed Content lassen Browser als unsicher markieren, auch wenn die Seite selbst per HTTPS laeuft - untergraebt das Vertrauen und kann Inhalte blockieren.")
    mixed = bool(url.startswith('https://') and re.search(r'src=["\']http://|href=["\']http://', html, re.IGNORECASE))
    f4.status = not mixed
    f4.description = "Unverschluesselte Ressourcen (Mixed Content) gefunden" if mixed else "Keine unverschluesselten Ressourcen gefunden"
    f4.recommendation = "" if not mixed else "Alle eingebundenen Ressourcen (Bilder, Skripte) auf https:// umstellen."
    findings.append(f4)

    f5 = AuditFinding('Lazy Loading bei Bildern', 'Technik', 'MITTEL',
                       was_geprueft="Ob Bilder mit loading=\"lazy\" erst beim Scrollen nachgeladen werden, statt alle sofort beim Seitenaufruf.",
                       warum_wichtig="Lazy Loading verkuerzt die Ladezeit spuerbar, besonders auf bilderlastigen Seiten und bei mobilen Verbindungen.")
    imgs_all = re.findall(r'<img[^>]*>', html, re.IGNORECASE)
    lazy_imgs = [i for i in imgs_all if 'loading="lazy"' in i.lower() or "loading='lazy'" in i.lower()]
    f5.status = len(imgs_all) == 0 or len(lazy_imgs) / max(len(imgs_all), 1) >= 0.5
    f5.description = f"{len(lazy_imgs)}/{len(imgs_all)} Bilder nutzen Lazy Loading"
    f5.recommendation = "" if f5.status else "loading=\"lazy\" bei Bildern unterhalb des sichtbaren Bereichs ergaenzen."
    findings.append(f5)

    f6 = AuditFinding('Moderne Bildformate (WebP/AVIF)', 'Technik', 'MITTEL',
                       was_geprueft="Ob moderne, komprimierte Bildformate (WebP/AVIF) statt nur klassischer JPG/PNG verwendet werden.",
                       warum_wichtig="WebP/AVIF sind bei gleicher Qualitaet deutlich kleiner als JPG/PNG und verkuerzen dadurch die Ladezeit spuerbar.")
    has_modern_fmt = bool(re.search(r'\.webp|\.avif|image/webp|image/avif', h))
    f6.status = has_modern_fmt
    f6.description = "Moderne Bildformate (WebP/AVIF) im Einsatz" if has_modern_fmt else "Keine modernen Bildformate (WebP/AVIF) erkannt"
    f6.recommendation = "" if has_modern_fmt else "Bilder zusaetzlich oder anstatt JPG/PNG als WebP/AVIF ausliefern - kleinere Dateien, schnellere Ladezeit."
    findings.append(f6)
    return findings


def analyze_seo(html, url):
    findings = []

    f1 = AuditFinding('Meta Title Laenge', 'SEO', 'HOCH',
                       was_geprueft="Die Laenge des Seitentitels (<title>-Tag), der in den Google-Suchergebnissen als Ueberschrift erscheint.",
                       warum_wichtig="Ein zu kurzer Title nutzt die Google-Anzeige nicht aus, ein zu langer wird abgeschnitten - beides senkt die Klickrate in den Suchergebnissen.")
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip() if title_match else ''
    title_len = len(title)
    f1.status = 30 <= title_len <= 65
    f1.description = f"Title-Laenge: {title_len} Zeichen (ideal: 30-65)"
    f1.recommendation = "" if f1.status else "Seitentitel auf 30-65 Zeichen anpassen fuer bessere Klickrate in Google."
    findings.append(f1)

    f2 = AuditFinding('Meta Description Laenge', 'SEO', 'HOCH',
                       was_geprueft="Die Laenge der Meta-Description, die als Vorschautext unter dem Titel in Google erscheint.",
                       warum_wichtig="Eine gute Meta-Description wirkt wie eine kleine Werbeanzeige - sie entscheidet oft mit, ob jemand auf das Suchergebnis klickt.")
    desc_match = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    desc = desc_match.group(1) if desc_match else ''
    desc_len = len(desc)
    f2.status = 120 <= desc_len <= 160
    f2.description = f"Meta Description: {desc_len} Zeichen (ideal: 120-160)"
    f2.recommendation = "" if f2.status else "Meta-Description auf 120-160 Zeichen bringen - wichtig fuer Klickrate."
    findings.append(f2)

    f3 = AuditFinding('H1 Tag vorhanden', 'SEO', 'HOCH',
                       was_geprueft="Ob die Seite genau eine H1-Ueberschrift (Hauptueberschrift) besitzt.",
                       warum_wichtig="Die H1 zeigt Google und Besuchern auf einen Blick, worum es auf der Seite geht. Mehrere oder gar keine H1 verwirren beide.")
    h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE)
    f3.status = len(h1s) == 1
    f3.description = f"{len(h1s)} H1-Tag(s) gefunden (ideal: genau 1)"
    f3.recommendation = "" if f3.status else "Genau ein H1-Tag pro Seite verwenden - klare Hauptueberschrift fuer Google."
    findings.append(f3)

    f3b = AuditFinding('H2-Zwischenueberschriften', 'SEO', 'MITTEL',
                        was_geprueft="Ob die Seite H2-Zwischenueberschriften nutzt, um den Inhalt zu strukturieren.",
                        warum_wichtig="Zwischenueberschriften machen lange Texte lesbarer und helfen Google, den Aufbau der Seite besser zu verstehen.")
    h2s = re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.IGNORECASE)
    f3b.status = len(h2s) >= 1
    f3b.description = f"{len(h2s)} H2-Tag(s) gefunden"
    f3b.recommendation = "" if f3b.status else "Zwischenueberschriften (H2) einbauen, um Inhalte klarer zu gliedern."
    findings.append(f3b)

    f4 = AuditFinding('Canonical Link', 'SEO', 'MITTEL',
                       was_geprueft="Ob ein Canonical-Tag vorhanden ist, das Google die 'offizielle' Version der Seite mitteilt.",
                       warum_wichtig="Ohne Canonical-Tag kann Google bei aehnlichen/doppelten Seiten (z.B. mit und ohne Parameter) unsicher sein, welche Version sie werten soll.")
    f4.status = bool(re.search(r'rel=["\']canonical["\']', html, re.IGNORECASE))
    f4.description = "Canonical-Tag vorhanden" if f4.status else "Kein Canonical-Tag gefunden"
    f4.recommendation = "" if f4.status else "Canonical-Link ergaenzen, um doppelte Inhalte zu vermeiden."
    findings.append(f4)

    f5 = AuditFinding('Alt-Attribute bei Bildern', 'SEO', 'MITTEL',
                       was_geprueft="Ob Bilder mit einem beschreibenden Alt-Text versehen sind.",
                       warum_wichtig="Alt-Texte helfen Google, Bilder inhaltlich einzuordnen (Bildersuche), und machen die Seite fuer Screenreader-Nutzer (Barrierefreiheit) zugaenglich.")
    imgs = re.findall(r'<img[^>]*>', html, re.IGNORECASE)
    imgs_no_alt = [i for i in imgs if not re.search(r'alt=["\'][^"\']+["\']', i)]
    f5.status = len(imgs) == 0 or len(imgs_no_alt) / max(len(imgs), 1) < 0.2
    f5.description = f"{len(imgs) - len(imgs_no_alt)}/{len(imgs)} Bilder mit Alt-Text"
    f5.recommendation = "" if f5.status else "Alt-Texte fuer Bilder ergaenzen - wichtig fuer SEO und Barrierefreiheit."
    findings.append(f5)

    f6 = AuditFinding('Open Graph Tags', 'SEO', 'MITTEL',
                       was_geprueft="Ob Open-Graph-Meta-Tags vorhanden sind, die bestimmen, wie ein geteilter Link auf Facebook/LinkedIn/WhatsApp aussieht.",
                       warum_wichtig="Ohne Open-Graph-Tags wird beim Teilen der Seite in sozialen Netzwerken oft kein Bild oder ein wahllos gewaehlter Text angezeigt - wirkt unprofessionell.")
    f6.status = bool(re.search(r'property=["\']og:', html, re.IGNORECASE))
    f6.description = "Open-Graph-Tags gefunden" if f6.status else "Keine Open-Graph-Tags gefunden"
    f6.recommendation = "" if f6.status else "Open-Graph-Tags (og:title, og:description, og:image) ergaenzen fuer ansprechendes Teilen in sozialen Netzwerken."
    findings.append(f6)

    f7 = AuditFinding('Twitter Cards', 'SEO', 'INFO',
                       was_geprueft="Ob Twitter/X-spezifische Meta-Tags fuer die Linkvorschau auf X (ehem. Twitter) vorhanden sind.",
                       warum_wichtig="Relevant nur, falls Links zur Seite auf X/Twitter geteilt werden - sorgt dort fuer eine ansprechende Vorschau statt eines nackten Links.")
    f7.status = bool(re.search(r'name=["\']twitter:', html, re.IGNORECASE))
    f7.description = "Twitter-Card-Tags gefunden" if f7.status else "Keine Twitter-Card-Tags gefunden"
    f7.recommendation = "" if f7.status else "Twitter-Card-Tags ergaenzen, falls die Seite auf X/Twitter geteilt werden soll."
    findings.append(f7)

    f8 = AuditFinding('Interne und externe Links', 'SEO', 'MITTEL',
                       was_geprueft="Wie viele Links insgesamt auf der Seite vorhanden sind, unterteilt in interne (eigene Domain) und externe (fremde Domain) Links.",
                       warum_wichtig="Interne Links helfen Besuchern und Google, weitere Inhalte der Seite zu finden. Externe Links zu vertrauenswuerdigen Quellen koennen die Glaubwuerdigkeit staerken.")
    domain = urlparse(url).netloc
    all_links = re.findall(r'href=["\']([^"\'#?]+)["\']', html, re.IGNORECASE)
    internal = [l for l in all_links if not l.startswith('http') or domain in l]
    external = [l for l in all_links if l.startswith('http') and domain not in l]
    f8.status = len(internal) >= 3
    f8.description = f"{len(internal)} interne Links, {len(external)} externe Links gefunden"
    f8.recommendation = "" if f8.status else "Mehr interne Verlinkung einbauen, um Nutzerfuehrung und SEO zu verbessern."
    findings.append(f8)
    return findings


def analyze_content(html, url):
    findings = []
    f1 = AuditFinding('Mindestens 300 Woerter', 'Inhalte', 'MITTEL',
                       was_geprueft="Die Anzahl der sichtbaren Wörter auf der Seite (ohne HTML-Code).",
                       warum_wichtig="Sehr duenne Seiten mit wenig Text haben es bei Google schwerer zu ranken, weil kaum inhaltliche Relevanz erkennbar ist.")
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    word_count = len(text.split())
    f1.status = word_count >= 300
    f1.description = f"Wort-Anzahl: {word_count} (Ziel: >= 300)"
    f1.recommendation = "" if f1.status else "Mehr hochwertigen Text ergaenzen - wichtig fuer SEO-Relevanz."
    findings.append(f1)

    f2 = AuditFinding('Interne Verlinkung', 'Inhalte', 'MITTEL',
                       was_geprueft="Ob ausreichend Links zu anderen Bereichen der eigenen Seite vorhanden sind.",
                       warum_wichtig="Gute interne Verlinkung fuehrt Besucher zu weiteren relevanten Inhalten und erhoeht die Verweildauer auf der Seite.")
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

    f1 = AuditFinding('Impressum vorhanden', 'Rechtlich', 'KRITISCH',
                       was_geprueft="Ob auf der Website (Startseite oder Unterseiten) ein Impressum verlinkt bzw. vorhanden ist.",
                       warum_wichtig="Das Impressum ist in Deutschland gesetzliche Pflicht fuer nahezu jede geschaeftliche Website. Ein fehlendes Impressum kann kostenpflichtig abgemahnt werden.")
    f1.status = bool(re.search(r'impressum', h))
    f1.description = "Impressum verlinkt/gefunden" if f1.status else "Kein Impressum gefunden"
    f1.recommendation = "" if f1.status else "Impressum ergaenzen - in Deutschland gesetzlich Pflicht (Paragraph 5 TMG)."
    f1.legal_basis = "" if f1.status else (
        "Paragraph 5 Telemediengesetz (TMG): Diensteanbieter muessen auf ihrer Website leicht erkennbar, "
        "unmittelbar erreichbar und staendig verfuegbar u.a. Name, Anschrift und Kontaktmoeglichkeiten "
        "bereithalten. Verstoesse koennen als Wettbewerbsverstoss abgemahnt werden."
    )
    findings.append(f1)

    f2 = AuditFinding('Datenschutzerklaerung vorhanden', 'Rechtlich', 'KRITISCH',
                       was_geprueft="Ob eine Datenschutzerklaerung auf der Website auffindbar ist.",
                       warum_wichtig="Jede Website, die personenbezogene Daten verarbeitet (z.B. durch Formulare, Analyse-Tools, Cookies), muss laut DSGVO transparent darueber informieren.")
    f2.status = bool(re.search(r'datenschutz|privacy.?policy', h))
    f2.description = "Datenschutzerklaerung gefunden" if f2.status else "Keine Datenschutzerklaerung gefunden"
    f2.recommendation = "" if f2.status else "Datenschutzerklaerung ergaenzen - DSGVO-Pflicht."
    f2.legal_basis = "" if f2.status else (
        "Art. 13 DSGVO (Datenschutz-Grundverordnung): Bei jeder Erhebung personenbezogener Daten (z.B. "
        "durch Kontaktformulare, Cookies, Tracking) muss der Nutzer transparent ueber Zweck, Umfang und "
        "Rechtsgrundlage der Datenverarbeitung informiert werden."
    )
    findings.append(f2)

    f3 = AuditFinding('Cookie-Banner vorhanden', 'Rechtlich', 'KRITISCH',
                       was_geprueft="Ob ein Cookie-Consent-Banner erkennbar ist, ueber das Besucher der Cookie-Nutzung zustimmen koennen.",
                       warum_wichtig="Werden nicht technisch notwendige Cookies (z.B. fuer Werbung/Tracking) ohne vorherige Einwilligung gesetzt, ist das ein DSGVO/TTDSG-Verstoss.")
    f3.status = any(p in h for p in COOKIE_PATTERNS) or bool(re.search(r'cookie', h))
    f3.description = "Cookie-Hinweis/Banner gefunden" if f3.status else "Kein Cookie-Banner erkannt"
    f3.recommendation = "" if f3.status else "Cookie-Consent-Banner einbauen (z.B. Cookiebot, Borlabs) - Pflicht bei Cookies/Tracking laut DSGVO/TTDSG."
    f3.legal_basis = "" if f3.status else (
        "Paragraph 25 TTDSG (Telekommunikation-Telemedien-Datenschutz-Gesetz), i.V.m. Art. 6 DSGVO: "
        "Fuer nicht technisch notwendige Cookies (z.B. Tracking, Marketing) ist vorherige, aktive "
        "Einwilligung des Nutzers erforderlich - eine reine Information reicht nicht aus."
    )
    findings.append(f3)

    f4 = AuditFinding('SSL-Verschluesselung (rechtlich)', 'Rechtlich', 'KRITISCH',
                       was_geprueft="Ob die Website verschluesselt (HTTPS) ausgeliefert wird - hier aus rein rechtlicher Perspektive betrachtet.",
                       warum_wichtig="Unverschluesselte Datenuebertragung (z.B. bei Formulareingaben) verstoesst gegen die DSGVO-Pflicht zu angemessenen technischen Schutzmassnahmen.")
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
    f5 = AuditFinding('Widerrufsbutton / Widerrufsbelehrung', 'Rechtlich', 'KRITISCH' if is_shop else 'INFO',
                       was_geprueft="Ob bei erkennbarer Kauffunktion (Warenkorb/Checkout) eine Widerrufsbelehrung bzw. elektronische Widerrufsfunktion vorhanden ist.",
                       warum_wichtig="Verbraucher haben bei Online-Kaeufen ein gesetzliches Widerrufsrecht. Fehlt die Widerrufsfunktion, drohen Abmahnungen und im schlimmsten Fall ein verlaengertes Widerrufsrecht der Kunden.")
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

    if is_shop:
        f6 = AuditFinding('AGB vorhanden', 'Rechtlich', 'HOCH',
                           was_geprueft="Ob Allgemeine Geschaeftsbedingungen (AGB) auf der Website verlinkt sind.",
                           warum_wichtig="AGB regeln die Vertragsbedingungen zwischen Haendler und Kunde (z.B. Eigentumsvorbehalt, Gewaehrleistung) und schaffen Rechtssicherheit fuer beide Seiten.")
        f6.status = bool(re.search(r'\bagb\b|geschaeftsbedingungen', h))
        f6.description = "AGB gefunden" if f6.status else "Keine AGB gefunden"
        f6.recommendation = "" if f6.status else "AGB ergaenzen - bei Online-Shops dringend empfohlen, um Vertragsbedingungen klar zu regeln."
        findings.append(f6)

        f7 = AuditFinding('Versandinformationen', 'Rechtlich', 'HOCH',
                           was_geprueft="Ob Informationen zu Versandkosten und Lieferzeiten auffindbar sind.",
                           warum_wichtig="Verbraucher muessen vor Vertragsschluss ueber Liefertermin und anfallende Versandkosten informiert werden (Art. 246a EGBGB).")
        f7.status = bool(re.search(r'versandkosten|lieferzeit|liefertermin|versand ab', h))
        f7.description = "Versandinformationen gefunden" if f7.status else "Keine klaren Versandinformationen gefunden"
        f7.recommendation = "" if f7.status else "Versandkosten und Lieferzeiten transparent angeben - Informationspflicht bei Fernabsatzvertraegen."
        findings.append(f7)

        f8 = AuditFinding('Zahlungsarten', 'Rechtlich', 'MITTEL',
                           was_geprueft="Ob die angebotenen Zahlungsarten (z.B. PayPal, Kreditkarte, Rechnung) transparent aufgefuehrt sind.",
                           warum_wichtig="Kunden moechten vor dem Kauf wissen, wie sie bezahlen koennen - fehlende Transparenz kann zu Kaufabbrüchen fuehren und ist Teil der Informationspflichten.")
        f8.status = bool(re.search(r'paypal|kreditkarte|rechnung|vorkasse|sofort\Wueberweisung|klarna|lastschrift|zahlungsarten', h))
        f8.description = "Zahlungsarten gefunden" if f8.status else "Keine Zahlungsarten-Information gefunden"
        f8.recommendation = "" if f8.status else "Angebotene Zahlungsarten klar auflisten (z.B. in AGB oder eigener Infoseite)."
        findings.append(f8)

    # Firmenangaben rein deskriptiv extrahieren - bewusst OHNE Bewertung, siehe warum_wichtig-Text.
    f9 = AuditFinding('Gefundene Firmenangaben (Impressum)', 'Rechtlich', 'INFO',
                       was_geprueft="Welche typischen Pflichtangaben im Impressum-Text automatisiert erkannt werden konnten.",
                       warum_wichtig="Diese Auflistung dient nur der Information. Diese Analyse prueft ausschliesslich oeffentlich sichtbare Inhalte. Ob die Angaben rechtlich vollstaendig oder korrekt sind, kann nicht beurteilt werden und sollte gegebenenfalls rechtlich ueberprueft werden.")
    found_items = []
    if re.search(r'kleinunternehmer', h):
        found_items.append('Hinweis auf Kleinunternehmerregelung (Paragraph 19 UStG)')
    if re.search(r'ust[\-\s]?id|umsatzsteuer\-?id|de\d{9}', h):
        found_items.append('Umsatzsteuer-Identifikationsnummer')
    if re.search(r'handelsregister|hrb\s?\d+|hra\s?\d+', h):
        found_items.append('Handelsregister-Eintrag')
    if re.search(r'geschaeftsfuehrer|inhaber(in)?:', h):
        found_items.append('Angabe zu Geschaeftsfuehrung/Inhaber')
    if re.search(r'gmbh|ug \(haftungsbeschraenkt\)|e\.?k\.?\b|einzelunternehmen|gbr\b|ohg\b', h):
        found_items.append('Angabe zur Rechtsform')
    if re.search(r'ihk|handwerkskammer|aerztekammer|steuerberaterkammer', h):
        found_items.append('Kammerangabe')
    f9.status = True
    f9.description = ("Folgende Angaben wurden gefunden: " + ', '.join(found_items) + "."
                       if found_items else "Keine der ueblichen zusaetzlichen Pflichtangaben (USt-ID, Handelsregister, Rechtsform etc.) automatisiert erkannt.")
    f9.recommendation = ""
    findings.append(f9)
    return findings


SOCIAL_PLATFORMS = [
    ('Facebook', r'facebook\.com/(?!sharer|share\.php|plugins)([\w.\-]+)'),
    ('Instagram', r'instagram\.com/(?!p/|explore)([\w.\-]+)'),
    ('LinkedIn', r'linkedin\.com/(company|in)/([\w.\-]+)'),
    ('TikTok', r'tiktok\.com/@([\w.\-]+)'),
    ('YouTube', r'youtube\.com/(channel/|c/|@)([\w.\-]+)'),
    ('XING', r'xing\.com/(companies/|profile/)([\w.\-]+)'),
    ('Threads', r'threads\.net/@([\w.\-]+)'),
    ('Pinterest', r'pinterest\.[a-z.]+/([\w.\-]+)'),
]


def analyze_web_presence(combined_html):
    """Findet verlinkte Social-Media-Profile und unterscheidet grob Unternehmens- von
    Privatprofilen (LinkedIn 'company/' vs. 'in/' ist eindeutig erkennbar, bei anderen
    Plattformen ist eine zuverlaessige Unterscheidung ohne API-Zugriff nicht moeglich)."""
    findings = []
    h = combined_html.lower()
    found = []
    for name, pattern in SOCIAL_PLATFORMS:
        m = re.search(pattern, h)
        if m:
            profile_type = ''
            if name == 'LinkedIn':
                profile_type = 'Unternehmensprofil' if m.group(1) == 'company' else 'Privatprofil'
            found.append((name, profile_type))

    f1 = AuditFinding('Social-Media-Praesenz', 'Internetpraesenz', 'MITTEL' if not found else 'INFO',
                       was_geprueft="Welche Social-Media-Kanaele (Facebook, Instagram, LinkedIn, TikTok, YouTube, XING, Threads, Pinterest) auf der Website verlinkt sind.",
                       warum_wichtig="Eine aktive Social-Media-Praesenz erhoeht die Reichweite, staerkt das Vertrauen neuer Kunden und ist ein zusaetzlicher Kanal zur Kundengewinnung.")
    f1.status = bool(found)
    if found:
        listing = ', '.join(f"{n}{' (' + t + ')' if t else ''}" for n, t in found)
        f1.description = f"Gefundene Profile: {listing}"
        f1.recommendation = ""
    else:
        f1.description = "Keine Social-Media-Profile auf der Website verlinkt gefunden"
        f1.recommendation = ("Social-Media-Kanaele passend zur Zielgruppe aufbauen und auf der Website verlinken - "
                              "erhoeht Reichweite, Vertrauen und ist ein zusaetzlicher Kontaktpunkt fuer potenzielle Kunden.")
    findings.append(f1)
    return findings


def analyze_forms(combined_html):
    findings = []
    h = combined_html.lower()

    f1 = AuditFinding('Kontakt-/Anfrageformular vorhanden', 'Formulare', 'HOCH',
                       was_geprueft="Ob ein online ausfuellbares Formular (z.B. Kontakt- oder Anfrageformular) auf der Website vorhanden ist.",
                       warum_wichtig="Ein Online-Formular senkt die Huerde fuer eine Kontaktaufnahme deutlich gegenueber Telefon oder E-Mail und fuehrt in der Praxis zu mehr Anfragen.")
    has_form = bool(re.search(r'<form', h))
    pdf_form_hint = bool(re.search(r'formular[^<]{0,40}\.pdf|\.pdf[^<]{0,40}formular', h))
    f1.status = has_form
    if has_form:
        f1.description = "Mindestens ein Online-Formular gefunden"
        f1.recommendation = ""
    elif pdf_form_hint:
        f1.description = ("Kein Online-Formular gefunden, aber ein Hinweis auf ein PDF-Formular. Das Formular muss "
                           "heruntergeladen, ausgedruckt oder manuell per E-Mail versendet werden. Moderne "
                           "Online-Formulare erhoehen die Benutzerfreundlichkeit und fuehren haeufig zu mehr Anfragen.")
        f1.recommendation = "PDF-Formular durch ein online ausfuellbares und direkt versendbares Formular ersetzen."
    else:
        f1.description = "Kein Formular (weder online noch als PDF) gefunden"
        f1.recommendation = "Ein Online-Kontaktformular ergaenzen, um Anfragen unkompliziert entgegenzunehmen."
    findings.append(f1)

    if has_form:
        f2 = AuditFinding('Pflichtfelder im Formular', 'Formulare', 'MITTEL',
                           was_geprueft="Ob im Formular Pflichtfelder (required-Attribut) markiert sind.",
                           warum_wichtig="Pflichtfelder verhindern unvollstaendige Anfragen und sorgen dafuer, dass wichtige Angaben (z.B. E-Mail) nicht vergessen werden.")
        f2.status = bool(re.search(r'required', h))
        f2.description = "Pflichtfelder erkannt" if f2.status else "Keine Pflichtfelder erkannt"
        f2.recommendation = "" if f2.status else "Wichtige Formularfelder (z.B. E-Mail, Name) als Pflichtfelder markieren."
        findings.append(f2)

        f3 = AuditFinding('Spam-Schutz im Formular', 'Formulare', 'MITTEL',
                           was_geprueft="Ob Hinweise auf einen Spam-Schutz (z.B. Captcha, Honeypot) im Formularbereich vorhanden sind.",
                           warum_wichtig="Ohne Spam-Schutz landen haeufig automatisierte Werbe-/Spam-Anfragen im Postfach, was echte Kundenanfragen erschwert zu erkennen macht.")
        f3.status = bool(re.search(r'captcha|recaptcha|hcaptcha|honeypot', h))
        f3.description = "Spam-Schutz erkannt" if f3.status else "Kein Spam-Schutz erkannt"
        f3.recommendation = "" if f3.status else "Spam-Schutz (z.B. Google reCAPTCHA) ergaenzen, um unerwuenschte automatisierte Anfragen zu reduzieren."
        findings.append(f3)
    return findings


def analyze_images(html, url):
    findings = []
    h = html.lower()
    imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', html, re.IGNORECASE)

    f1 = AuditFinding('Bilder auf der Seite', 'Bilder', 'MITTEL',
                       was_geprueft="Wie viele Bilder auf der Seite eingebunden sind und ob erkennbare Platzhalter-/Fehlerbilder vorkommen.",
                       warum_wichtig="Hochwertige, passende Bilder erhoehen die Verweildauer und das Vertrauen. Sichtbare Platzhalterbilder oder fehlende Bilder wirken unprofessionell.")
    placeholder_hits = [i for i in imgs if re.search(r'placeholder|platzhalter|dummy|coming[-_]?soon|no[-_]?image', i, re.IGNORECASE)]
    f1.status = len(imgs) > 0 and not placeholder_hits
    if not imgs:
        f1.description = "Keine Bilder auf der Seite gefunden"
        f1.recommendation = "Passende Bilder ergaenzen - eine reine Textseite wirkt schnell unprofessionell."
    elif placeholder_hits:
        f1.description = f"{len(imgs)} Bilder gefunden, davon {len(placeholder_hits)} mit Hinweis auf Platzhalter-/Dummy-Bilder"
        f1.recommendation = "Platzhalterbilder durch echte, hochwertige Fotos/Grafiken ersetzen."
    else:
        f1.description = f"{len(imgs)} Bilder gefunden, keine Platzhalter-Hinweise erkannt"
        f1.recommendation = ""
    findings.append(f1)

    f2 = AuditFinding('Moderne Bildformate im Einsatz', 'Bilder', 'MITTEL',
                       was_geprueft="Ob Bilder im modernen, komprimierten WebP- oder AVIF-Format statt nur als JPG/PNG eingebunden sind.",
                       warum_wichtig="Moderne Formate verkleinern Dateigroessen erheblich und beschleunigen den Seitenaufbau, besonders auf mobilen Verbindungen.")
    modern_imgs = [i for i in imgs if i.lower().endswith(('.webp', '.avif'))]
    f2.status = len(imgs) == 0 or len(modern_imgs) / max(len(imgs), 1) >= 0.3
    f2.description = f"{len(modern_imgs)}/{len(imgs)} Bilder in modernem Format (WebP/AVIF)"
    f2.recommendation = "" if f2.status else "Bilder wo moeglich zusaetzlich als WebP/AVIF bereitstellen."
    findings.append(f2)
    return findings


def analyze_trust(combined_html):
    findings = []
    h = combined_html.lower()

    f1 = AuditFinding('Vertrauenselemente auf der Seite', 'Vertrauen', 'MITTEL',
                       was_geprueft="Ob Elemente vorhanden sind, die bei neuen Besuchern Vertrauen schaffen: Kundenbewertungen, Referenzen, Zertifikate, Partnerlogos, Team-/Ueber-uns-Seite.",
                       warum_wichtig="Vertrauenselemente senken die Huerde fuer eine erste Kontaktaufnahme oder Bestellung deutlich, besonders bei Besuchern, die das Unternehmen noch nicht kennen.")
    found = []
    if re.search(r'bewertung|testimonial|kundenstimme|trustpilot|google.?review|sternebewertung', h):
        found.append('Kundenbewertungen')
    if re.search(r'referenz|unsere kunden|case[- ]stud', h):
        found.append('Referenzen')
    if re.search(r'zertifiziert|zertifikat|siegel|auszeichnung|tuev|iso\s?\d{4}', h):
        found.append('Zertifikate/Siegel')
    if re.search(r'partner(logo)?s?\b', h):
        found.append('Partnerlogos')
    if re.search(r'unser team|ueber uns|about[- ]us|wer wir sind', h):
        found.append('Team-/Ueber-uns-Seite')
    f1.status = bool(found)
    f1.description = f"Gefunden: {', '.join(found)}" if found else "Keine der ueblichen Vertrauenselemente gefunden"
    f1.recommendation = "" if found else "Vertrauenselemente ergaenzen (z.B. Kundenbewertungen, Referenzen, Team-Seite) - staerkt die Glaubwuerdigkeit gegenueber neuen Besuchern."
    findings.append(f1)
    return findings


def analyze_customer_acquisition(combined_html):
    findings = []
    h = combined_html.lower()

    f1 = AuditFinding('Kontaktmoeglichkeiten', 'Kundengewinnung', 'HOCH',
                       was_geprueft="Ob Telefonnummer, E-Mail-Adresse und ein direkter Kontaktbutton gut sichtbar vorhanden sind.",
                       warum_wichtig="Je einfacher und schneller ein Interessent Kontakt aufnehmen kann, desto weniger Anfragen gehen verloren, weil der Besucher die Seite vorher wieder verlaesst.")
    has_phone = bool(re.search(r'tel:\+?\d|(\+49[\s\-]?\d[\d\s\-/]{5,})|\b0\d{2,5}[\s/\-]\d{3,}', h))
    has_email = bool(re.search(r'mailto:|[\w.\-]+@[\w\-]+\.[a-z]{2,}', h))
    has_contact_btn = bool(re.search(r'kontaktieren sie uns|jetzt kontaktieren|kontakt aufnehmen', h))
    f1.status = has_phone or has_email
    parts = []
    if has_phone: parts.append('Telefonnummer')
    if has_email: parts.append('E-Mail-Adresse')
    if has_contact_btn: parts.append('Kontakt-Button')
    f1.description = f"Gefunden: {', '.join(parts)}" if parts else "Keine direkten Kontaktmoeglichkeiten gefunden"
    f1.recommendation = "" if f1.status else "Telefonnummer und/oder E-Mail-Adresse gut sichtbar auf der Seite platzieren."
    findings.append(f1)

    f2 = AuditFinding('Moderne Kontaktkanaele (WhatsApp, Live-Chat, Terminbuchung)', 'Kundengewinnung', 'MITTEL',
                       was_geprueft="Ob zusaetzlich zu klassischen Kanaelen moderne Kontaktwege wie WhatsApp-Button, Live-Chat oder Online-Terminbuchung angeboten werden.",
                       warum_wichtig="Diese Kanaele senken die Kontakt-Huerde weiter, besonders bei juengeren Zielgruppen, die ungern anrufen oder E-Mails schreiben.")
    found = []
    if re.search(r'wa\.me/|whatsapp', h): found.append('WhatsApp')
    if re.search(r'live[- ]?chat|intercom|tawk\.to|crisp\.chat|zendesk', h): found.append('Live-Chat')
    if re.search(r'termin (buchen|vereinbaren)|calendly|termin online', h): found.append('Terminbuchung')
    f2.status = bool(found)
    f2.description = f"Gefunden: {', '.join(found)}" if found else "Keine modernen Kontaktkanaele gefunden"
    f2.recommendation = "" if found else "WhatsApp-Kontakt, Live-Chat oder Online-Terminbuchung pruefen - kann die Kontaktaufnahme deutlich vereinfachen."
    findings.append(f2)

    f3 = AuditFinding('Call-to-Action vorhanden', 'Kundengewinnung', 'HOCH',
                       was_geprueft="Ob klare Handlungsaufforderungen (z.B. 'Jetzt anfragen', 'Termin vereinbaren') auf der Seite vorhanden sind.",
                       warum_wichtig="Ohne klare Handlungsaufforderung wissen Besucher oft nicht, was der naechste Schritt sein soll - das kostet Anfragen.")
    f3.status = bool(re.search(r'jetzt anfragen|jetzt buchen|jetzt kaufen|termin vereinbaren|angebot anfordern|kontaktieren sie uns|jetzt bestellen', h))
    f3.description = "Call-to-Action gefunden" if f3.status else "Kein klarer Call-to-Action gefunden"
    f3.recommendation = "" if f3.status else "Klare Handlungsaufforderung ergaenzen (z.B. 'Jetzt unverbindlich anfragen')."
    findings.append(f3)
    return findings


def analyze_broken_images(pw_result):
    """Browserverifizierte Pruefung auf defekte/fehlende Bilder (naturalWidth=0 nach Laden) -
    zuverlaessiger als eine reine HTTP-Statuspruefung, da sie auch fehlerhaft dekodierte oder
    per JS nachgeladene Bilder erfasst."""
    f1 = AuditFinding('Defekte oder fehlende Bilder (Browsertest)', 'Bilder', 'HOCH',
                       was_geprueft="Ob beim tatsaechlichen Laden der Seite im Browser Bilder erkannt wurden, die nicht angezeigt werden konnten (kaputter Link, fehlende Datei, Ladefehler).",
                       warum_wichtig="Defekte Bilder (das bekannte 'kaputtes Bild'-Symbol) wirken auf Besucher sofort unprofessionell und unfertig.")
    if not pw_result or not pw_result.get('available'):
        f1.status = None
        f1.description = pw_result.get('reason', 'Browserbasierte Pruefung nicht verfuegbar.') if pw_result else 'Browserbasierte Pruefung nicht verfuegbar.'
        f1.recommendation = ""
        return [f1]

    broken = pw_result.get('broken_images_max', 0)
    f1.status = broken == 0
    f1.description = f"{broken} defekte(s)/fehlende(s) Bild(er) im Browsertest gefunden" if broken else "Keine defekten oder fehlenden Bilder im Browsertest gefunden"
    f1.recommendation = "" if f1.status else "Bild-Links pruefen und defekte/fehlende Bilder ersetzen oder entfernen."
    return [f1]


def analyze_mobile_devices(pw_result):
    """Wandelt die Playwright-Viewport-Ergebnisse in verstaendliche Findings um. pw_result kann
    {'available': False, ...} sein, wenn Chromium auf dem Server nicht verfuegbar ist - dann
    wird das transparent als INFO-Finding vermerkt statt den ganzen Report scheitern zu lassen."""
    findings = []

    if not pw_result or not pw_result.get('available'):
        f0 = AuditFinding('Mobile-Test auf mehreren Geraeten', 'Mobile', 'INFO',
                           was_geprueft="Ob die Seite auf verschiedenen Bildschirmgroessen automatisiert getestet werden konnte.",
                           warum_wichtig="Diese browserbasierte Pruefung war fuer diesen Report nicht verfuegbar.")
        f0.status = None
        f0.description = pw_result.get('reason', 'Browserbasierte Pruefung nicht verfuegbar.') if pw_result else 'Browserbasierte Pruefung nicht verfuegbar.'
        f0.recommendation = ""
        findings.append(f0)
        return findings

    viewports = pw_result.get('viewports', [])
    tested_names = [v['name'] for v in viewports if v.get('ok')]

    f1 = AuditFinding('Getestete Bildschirmgroessen', 'Mobile', 'INFO',
                       was_geprueft="Auf welchen Bildschirmgroessen die Website automatisiert im echten Browser geladen und geprueft wurde.",
                       warum_wichtig="Ein Grossteil der Besucher nutzt heute Smartphones - eine Seite, die nur am Desktop gut aussieht, verliert dadurch potenzielle Kunden.")
    f1.status = True
    f1.description = (f"Getestet auf {len(tested_names)}/{len(viewports)} Geraeten: " + ', '.join(tested_names)
                       if tested_names else "Kein Geraet konnte erfolgreich getestet werden.")
    f1.recommendation = ""
    findings.append(f1)

    overflow_devices = [v['name'] for v in viewports if v.get('ok') and v.get('overflow')]
    f2 = AuditFinding('Horizontales Scrollen / abgeschnittene Inhalte', 'Mobile', 'HOCH',
                       was_geprueft="Ob die Seite auf einem der getesteten Geraete breiter als der Bildschirm ist und dadurch horizontal gescrollt werden muss.",
                       warum_wichtig="Horizontales Scrollen ist auf Mobilgeraeten ein starkes Warnsignal fuer schlechte Bedienbarkeit und schreckt Besucher ab.")
    f2.status = not overflow_devices
    f2.description = (f"Betroffen: {', '.join(overflow_devices)}" if overflow_devices
                       else "Keine horizontalen Scroll-Probleme auf den getesteten Geraeten gefunden")
    f2.recommendation = "" if f2.status else "Layout/CSS pruefen - vermutlich feste Breiten oder zu grosse Elemente, die auf kleinen Bildschirmen ueberlaufen."
    findings.append(f2)

    tiny_btn_devices = [v['name'] for v in viewports if v.get('ok') and v.get('tinyButtons', 0) > 0]
    f3 = AuditFinding('Buttons/Links auf Mobilgeraeten', 'Mobile', 'MITTEL',
                       was_geprueft="Ob Buttons und klickbare Elemente auf mobilen Bildschirmgroessen gross genug sind, um bequem antippbar zu sein (Richtwert: mind. 32x32 Pixel).",
                       warum_wichtig="Zu kleine Buttons fuehren zu Fehlklicks und Frust, besonders bei der Bedienung mit dem Finger statt der Maus.")
    f3.status = not tiny_btn_devices
    f3.description = (f"Zu kleine Buttons erkannt auf: {', '.join(tiny_btn_devices)}" if tiny_btn_devices
                       else "Keine zu kleinen Buttons auf den getesteten Geraeten gefunden")
    f3.recommendation = "" if f3.status else "Klickbare Elemente auf mobilen Geraeten vergroessern (mind. 32x32 Pixel Touch-Flaeche)."
    findings.append(f3)

    table_overflow_devices = [v['name'] for v in viewports if v.get('ok') and v.get('tableOverflow')]
    if any(v.get('tables', 0) > 0 for v in viewports if v.get('ok')):
        f4 = AuditFinding('Tabellen-Darstellung', 'Mobile', 'MITTEL',
                           was_geprueft="Ob Tabellen auf kleinen Bildschirmen breiter als der sichtbare Bereich sind.",
                           warum_wichtig="Nicht angepasste Tabellen sind auf Smartphones oft nur durch seitliches Scrollen lesbar - schlechte Nutzererfahrung.")
        f4.status = not table_overflow_devices
        f4.description = (f"Tabellen laufen ueber auf: {', '.join(table_overflow_devices)}" if table_overflow_devices
                           else "Tabellen passen sich den getesteten Bildschirmgroessen an")
        f4.recommendation = "" if f4.status else "Tabellen fuer mobile Ansicht responsiv gestalten (z.B. horizontales Scrollen innerhalb der Tabelle statt der ganzen Seite, oder Kartenlayout)."
        findings.append(f4)

    return findings


def analyze_js_errors(pw_result):
    findings = []
    f1 = AuditFinding('JavaScript-/CSS-Fehler', 'Technik', 'MITTEL',
                       was_geprueft="Ob beim Laden der Seite im echten Browser Fehler in der Entwicklerkonsole auftreten (defekte Skripte, fehlerhafte Ressourcen).",
                       warum_wichtig="Skriptfehler koennen dazu fuehren, dass Teile der Seite (z.B. Formulare, Menues, interaktive Elemente) nicht richtig funktionieren, ohne dass es auf den ersten Blick auffaellt.")
    if not pw_result or not pw_result.get('available'):
        f1.status = None
        f1.description = pw_result.get('reason', 'Browserbasierte Pruefung nicht verfuegbar.') if pw_result else 'Browserbasierte Pruefung nicht verfuegbar.'
        f1.recommendation = ""
        return [f1]

    errors = pw_result.get('console_errors', [])
    f1.status = not errors
    f1.description = (f"{len(errors)} unterschiedliche Fehlermeldung(en) in der Browserkonsole gefunden, z.B.: {errors[0][:150]}"
                       if errors else "Keine Fehler in der Browserkonsole gefunden")
    f1.recommendation = "" if f1.status else "Fehlermeldungen in der Browserkonsole pruefen und beheben (z.B. mit den Entwicklertools im Browser)."
    findings.append(f1)
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

    # Parallel: robots.txt, sitemap.xml, interne Unterseiten crawlen, Playwright-Browsercheck
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_robots = ex.submit(fetch_url_ok, f"{urlparse(final_url).scheme}://{domain}/robots.txt")
        fut_sitemap = ex.submit(fetch_url_ok, f"{urlparse(final_url).scheme}://{domain}/sitemap.xml")
        fut_crawl = ex.submit(crawl_site, html, final_url, domain)
        fut_pw = None
        if PLAYWRIGHT_MODULE_AVAILABLE:
            fut_pw = ex.submit(audit_playwright.run_visual_checks, final_url)

        robots_ok = fut_robots.result()
        report(35)
        sitemap_ok = fut_sitemap.result()
        report(45)
        combined_html, crawled_urls = fut_crawl.result()
        report(60)
        if fut_pw is not None:
            try:
                pw_result = fut_pw.result(timeout=120)
            except Exception as e:
                pw_result = {'available': False, 'reason': f'Zeitueberschreitung oder Fehler: {e}'}
        else:
            pw_result = {'available': False, 'reason': 'Playwright-Modul nicht verfuegbar auf diesem Server.'}
        report(75)
    pagespeed_full = None

    result['crawled_pages'] = crawled_urls

    technik_findings = analyze_technical(html, final_url, domain) + analyze_js_errors(pw_result)

    categories = {
        'Indexierung': analyze_indexing(html, final_url, domain, robots_ok, sitemap_ok),
        'Technik': technik_findings,
        'SEO': analyze_seo(html, final_url),
        'Inhalte': analyze_content(html, final_url),
        'Rechtlich': analyze_legal(html, final_url, combined_html, crawled_urls),
        'Formulare': analyze_forms(combined_html),
        'Bilder': analyze_images(html, final_url) + analyze_broken_images(pw_result),
        'Mobile': analyze_mobile_devices(pw_result),
        'Internetpraesenz': analyze_web_presence(combined_html),
        'Vertrauen': analyze_trust(combined_html),
        'Kundengewinnung': analyze_customer_acquisition(combined_html),
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
            'kategorien': {},
            'zusammenfassung': {
                'gesamteindruck': 'KI-Zusammenfassung nicht verfuegbar - es wurden nur die automatisierten technischen Pruefungen durchgefuehrt.',
                'groesste_staerken': [], 'groesste_schwaechen': [], 'dringendste_massnahmen': [],
                'quick_wins': [], 'langfristige_optimierungen': [],
            },
            'textqualitaet': {'auffaelligkeiten_gefunden': None, 'beispiele': [], 'lesbarkeit': '', 'bewertung': ''},
        }
    ai_result.setdefault('zusammenfassung', {})
    ai_result.setdefault('textqualitaet', {})
    ai_result['top_massnahmen'] = build_top_massnahmen(categories)
    result['ai_result'] = ai_result

    report(100)
    return result
