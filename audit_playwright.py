# -*- coding: utf-8 -*-
"""Browserbasierte Zusatzpruefungen fuer den Website-Audit (Stufe 2): Mobile-Darstellung auf
mehreren Bildschirmgroessen, JavaScript-/CSS-Fehler und tatsaechlich (browserverifiziert)
defekte oder fehlende Bilder. Nutzt Playwright/Chromium - laeuft, weil der Audit bereits als
Hintergrund-Job ausgefuehrt wird, weshalb ein paar Sekunden zusaetzliche Laufzeit unkritisch sind.

Faellt sauber auf 'nicht verfuegbar' zurueck, wenn Chromium auf dem Server (noch) nicht
installiert ist - der restliche Audit funktioniert dann unveraendert weiter (Stufe 1)."""

VIEWPORTS = [
    ('Kleines Smartphone', 360, 640),
    ('Grosses Smartphone', 430, 932),
    ('Tablet Hochformat', 768, 1024),
    ('Tablet Querformat', 1024, 768),
    ('Notebook', 1366, 768),
    ('Desktop', 1920, 1080),
    ('Grosser Monitor', 2560, 1440),
]

NAV_JS = """() => {
    const overflow = document.documentElement.scrollWidth > window.innerWidth + 5;
    const buttons = Array.from(document.querySelectorAll('button, a.btn, [role="button"], input[type=submit]'));
    const tinyButtons = buttons.filter(b => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.width < 32 || r.height < 32);
    }).length;
    const forms = document.querySelectorAll('form').length;
    let formsVisible = 0;
    document.querySelectorAll('form').forEach(f => {
        const r = f.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) formsVisible++;
    });
    const tables = document.querySelectorAll('table').length;
    let tableOverflow = false;
    document.querySelectorAll('table').forEach(t => {
        if (t.getBoundingClientRect().width > window.innerWidth + 5) tableOverflow = true;
    });
    const brokenImgs = Array.from(document.querySelectorAll('img')).filter(
        img => img.complete && img.naturalWidth === 0
    ).length;
    const totalImgs = document.querySelectorAll('img').length;
    return {
        overflow, tinyButtons, buttons: buttons.length, forms, formsVisible,
        tables, tableOverflow, brokenImgs, totalImgs,
    };
}"""


def run_visual_checks(url, per_viewport_timeout_ms=12000):
    """Fuehrt fuer jede der 7 definierten Bildschirmgroessen einen echten Seitenaufruf durch
    und sammelt Layout-/Fehlerdaten. Gibt {'available': False, 'reason': ...} zurueck, wenn
    Playwright/Chromium auf diesem Server nicht nutzbar ist."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'available': False, 'reason': 'Playwright ist nicht installiert.'}

    results = {'available': True, 'viewports': [], 'console_errors': [], 'broken_images_max': 0}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=['--no-sandbox'])
            for name, w, h in VIEWPORTS:
                page = browser.new_page(viewport={'width': w, 'height': h})
                console_errors_this_page = []
                page.on('console', lambda msg: console_errors_this_page.append(msg.text) if msg.type == 'error' else None)
                page.on('pageerror', lambda exc: console_errors_this_page.append(str(exc)))
                entry = {'name': name, 'width': w, 'height': h, 'ok': False}
                try:
                    page.goto(url, timeout=per_viewport_timeout_ms, wait_until='domcontentloaded')
                    page.wait_for_timeout(800)  # kurz warten, damit Lazy-Content/JS nachladen kann
                    data = page.evaluate(NAV_JS)
                    entry.update(data)
                    entry['ok'] = True
                    results['broken_images_max'] = max(results['broken_images_max'], data.get('brokenImgs', 0))
                except Exception as e:
                    entry['error'] = str(e)
                results['console_errors'].extend(console_errors_this_page)
                results['viewports'].append(entry)
                page.close()
            browser.close()
    except Exception as e:
        return {'available': False, 'reason': f'Playwright-Check fehlgeschlagen: {e}'}

    # Fehlermeldungen deduplizieren (JS-Fehler wiederholen sich oft pro Viewport)
    seen = set()
    deduped = []
    for err in results['console_errors']:
        if err not in seen:
            seen.add(err)
            deduped.append(err)
    results['console_errors'] = deduped[:15]
    return results
