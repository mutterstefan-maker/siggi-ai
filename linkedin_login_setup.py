"""
Einmaliges, manuelles LinkedIn-Login zum Erzeugen einer Session-Datei.

Ablauf:
1. Skript ausfuehren: python linkedin_login_setup.py
2. Es oeffnet ein echtes Chrome-Fenster auf linkedin.com/login.
3. Du loggst dich normal ein (inkl. 2FA falls noetig).
4. Sobald du im Feed bist, druecke im Terminal ENTER.
5. Die Session (Cookies) wird in linkedin_browser_state.json gespeichert.

Diese Datei enthaelt KEIN Passwort, nur die eingeloggte Browser-Session -
trotzdem sensibel behandeln (nicht ins Git-Repo committen, per gitignore ausgeschlossen).
"""
from playwright.sync_api import sync_playwright

STATE_PATH = "linkedin_browser_state.json"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        print("\nBitte GENAU in DIESEM automatisch geoeffneten Fenster bei LinkedIn einloggen")
        print("(nicht in einem anderen, bereits offenen Chrome-Fenster!).")
        print("Wenn du danach wirklich in deinem Feed bist (Startseite mit Beitraegen), hier ENTER druecken...")
        input()

        # Sicherheitscheck: ohne 'li_at'-Cookie war der Login nicht erfolgreich.
        cookies = context.cookies()
        if not any(c["name"] == "li_at" for c in cookies):
            print("\nFEHLER: Kein 'li_at'-Cookie gefunden - du warst noch nicht eingeloggt, als ENTER gedrueckt wurde.")
            print("Bitte Skript neu starten und erst ENTER druecken, wenn der Feed in DIESEM Fenster sichtbar ist.")
            browser.close()
            return

        context.storage_state(path=STATE_PATH)
        print(f"Session erfolgreich gespeichert in: {STATE_PATH}")
        browser.close()

if __name__ == "__main__":
    main()
