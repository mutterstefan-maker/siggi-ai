"""
LinkedIn-Browser-Fallback fuer Kommentare lesen/beantworten.

Grund: LinkedIns offizielle API (/socialActions/.../comments) erlaubt normalen
OAuth-Apps kein Auslesen von Kommentaren (nur Partner mit Community Management
API-Zugang duerfen das) - siehe linkedin_engine.py fetch_comments_raw/reply_to_comment,
die bei "Berechtigung verweigert" fehlschlagen.

Dieses Modul umgeht das per Playwright (Headless-Chromium) mit einer einmalig
manuell erzeugten Login-Session (siehe linkedin_login_setup.py). Kein Passwort
wird hier je gespeichert oder automatisiert eingegeben - nur die bereits
eingeloggte Browser-Session (Cookies) wird wiederverwendet.

Risiko: LinkedIn erkennt automatisiertes Verhalten und kann Accounts bei Verdacht
einschraenken/sperren. Deshalb bewusst nur fuer die zwei Aktionen genutzt, die per
API nicht gehen (lesen, kommentieren) - das Posten von Beitraegen laeuft weiter
ganz normal ueber die offizielle API (linkedin_engine.post_share).
"""
import os
import json

from playwright.sync_api import sync_playwright

STATE_PATH = os.environ.get('LINKEDIN_BROWSER_STATE_PATH', os.path.join(os.path.dirname(__file__), 'linkedin_browser_state.json'))


def is_session_available():
    return os.path.exists(STATE_PATH)


def _post_url_from_urn(urn):
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _open_post_page(playwright, urn):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=STATE_PATH)
    page = context.new_page()
    page.goto(_post_url_from_urn(urn), wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    return browser, page


def get_comments_via_browser(urn):
    """Liest Kommentare zu einem Beitrag per Browser-Session. Gibt Liste von dicts zurueck."""
    if not is_session_available():
        return {'error': 'Keine LinkedIn-Browser-Session vorhanden (linkedin_login_setup.py einmalig ausfuehren).'}

    with sync_playwright() as p:
        browser, page = _open_post_page(p, urn)
        try:
            if "/login" in page.url:
                return {'error': 'LinkedIn-Session ist abgelaufen - linkedin_login_setup.py erneut ausfuehren.'}

            items = page.locator("article.comments-comment-entity")
            count = items.count()
            comments = []
            for i in range(count):
                item = items.nth(i)
                try:
                    author = item.locator(".comments-comment-meta__description-title").first.inner_text(timeout=2000).strip()
                except Exception:
                    author = "Unbekannt"
                try:
                    text = item.locator(".comments-comment-item__main-content").first.inner_text(timeout=2000).strip()
                except Exception:
                    text = ""
                try:
                    when = item.locator(".comments-comment-meta__data").first.inner_text(timeout=2000).strip()
                except Exception:
                    when = ""
                comment_id = item.get_attribute("data-id") or f"idx-{i}"
                comments.append({'id': comment_id, 'author': author, 'text': text, 'when': when})
            return comments
        finally:
            browser.close()


def reply_to_comment_via_browser(urn, text):
    """Schreibt einen Kommentar unter den Beitrag per Browser-Session."""
    if not is_session_available():
        return {'error': 'Keine LinkedIn-Browser-Session vorhanden (linkedin_login_setup.py einmalig ausfuehren).'}

    with sync_playwright() as p:
        browser, page = _open_post_page(p, urn)
        try:
            if "/login" in page.url:
                return {'error': 'LinkedIn-Session ist abgelaufen - linkedin_login_setup.py erneut ausfuehren.'}

            editor = page.locator(".comments-comment-box-comment__text-editor .ql-editor").first
            editor.click()
            page.wait_for_timeout(300)
            editor.type(text, delay=20)
            page.wait_for_timeout(500)

            submit_btn = page.locator("button.comments-comment-box__submit-button--cr").first
            submit_btn.click(timeout=5000)
            page.wait_for_timeout(2500)

            if text[:40] in page.content():
                return {'success': True}
            return {'error': 'Kommentar wurde vermutlich nicht veroeffentlicht (nicht im HTML gefunden).'}
        except Exception as e:
            return {'error': str(e)}
        finally:
            browser.close()
