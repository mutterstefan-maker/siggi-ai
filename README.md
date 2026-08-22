# Siggi AI

Persönlicher KI-Assistent für Stefan Mutter (Chefblick) – E-Mail-Management,
Website-Audits, Social-Media-Automatisierung und mehr, per Chat (Text + Sprache)
bedienbar. Läuft als Flask-App auf einem eigenen VPS.

**Live:** https://www.stean.info

## Tech Stack

- **Backend:** Python / Flask, SQLite (`mails.db`), Gunicorn, systemd
- **Frontend:** Vanilla JS/HTML (`index.html`), kein Framework
- **KI:** Anthropic Claude (Sonnet 5 für Haupt-Chat und Content-Pipelines,
  mit Prompt-Caching auf dem großen System-Prompt; Haiku 4.5 für einfache
  Captions/Themen-Auswahl), OpenAI (`gpt-image-1` für Bildgenerierung)
- **Mobile:** natives Flutter-Android-App (`siggi_mobile/`) – Bottom-Nav mit
  Icon-Hubs statt Seitenmenü, spricht per REST + Session-Cookie mit demselben
  Backend wie das Web-Dashboard (kein separates Auth-System)
- **Deploy:** Cron pollt GitHub alle 2 Minuten, synced nach `/opt/stean`,
  installiert neue Requirements, startet `stean.service` neu
  (`deploy_siggi.sh`, siehe unten)

## Features

- 📧 **E-Mail-Management** – IMAP-Postfach, KI-Antwortvorschläge, Wissenslücken-
  Lernmechanismus (manuelle Prüfung bis Schwelle erreicht, danach Automatik)
- 🔍 **Website-Audit** – automatische Analyse von Kunden-Websites
- 🖥️ **Desktop-Agent** – optionaler Hintergrunddienst auf Stefans PC
  (`desktop_agent/`), verbindet sich per Socket.IO, kann Screenshots machen,
  Dateien lesen/schreiben, Apps starten/beenden (nur auf Zuruf, keine
  Dauerüberwachung)
- 📁 **COWORK-Zugriff** – `D:\COWORK` wird periodisch auf den Server gespiegelt
  (`sync_to_siggi.ps1`, Windows-Taskplaner), Siggi kann darin per Chat Dateien
  suchen und Inhalte anzeigen (`cowork_datei_suchen`, `cowork_datei_anzeigen`,
  inkl. PDF-Textextraktion). Der `GRAVEDA`-Unterordner ist bewusst ausgeschlossen.
- 💼 **LinkedIn-Content-Pipeline** – täglicher Post-Entwurf im Stil von Stefans
  bisherigen Posts, Review-Tab im Dashboard, nach 50 manuellen Freigaben
  automatischer Modus (`linkedin_pipeline_engine.py`)
- 🖼️ **Bild-Pipeline (Instagram/Facebook)** – täglich ein neues ChefBlick-
  Werbebild nach festen Marken-Vorgaben (Farben, Logo, „KI-GENERIERT“-
  Kennzeichnung, Themen-/Layout-Rotation, Einhorn-Maskottchen max. jedes
  10. Bild). Landet zur manuellen Freigabe in einer Prüf-Warteschlange,
  erst danach in der echten Instagram-Auto-Post-Queue
  (`instagram_flyer_engine.py`). Themenwahl bezieht zusätzlich eine vom
  Nutzer gepflegte Themen-Ideen-Datenbank ein (`/api/topics`), um
  Wiederholungen zu vermeiden
- 🎬 **Reels (Instagram-Story)** – baut automatisch stille 9:16-Slideshow-
  Videos (ffmpeg, Crossfades, Blur-Letterbox, „KI-GENERIERT“-Einblendung)
  aus bereits geposteten Flyer-Bildern (themen-gruppiert per Haiku), inkl.
  eigenem Freigabe-Workflow wie bei der Bild-Pipeline (`reels_engine.py`)
- 👥 **Kontakte/CRM** – einfache Kontaktverwaltung (manuell oder aus dem
  Mail-Verlauf), `/api/contacts`
- 🤖 **Selbstverbesserung** – tägliche Analyse von Wissenslücken/Tool-Fehlern,
  automatisch behobene Fixes im „Selbstverbesserung“-Tab protokolliert
- 🔐 **Sicherheit** – gehärtetes SSH/Firewall-Setup, Live-Angriffsstatistik-
  Dashboard, KI-Kennzeichnungspflicht in allen generierten Social-Media-Texten
- 🗂️ **Dashboard** – Kachel-Startseite mit Kennzahlen + Schnellzugriff
  (gruppiert nach Mail/Social/Tools, roter Ring bei offenen Freigaben),
  Seitenmenü in auf-/zuklappbare Kategorien gruppiert statt einer langen
  flachen Liste

## Cron-Jobs (Server, `crontab -l`)

| Zeit | Skript | Zweck |
|---|---|---|
| `*/2 * * * *` | `deploy_siggi.sh` | Auto-Deploy bei neuem Git-Commit |
| `0 4 * * *` | `security_check.py` | tägliche Sicherheitsprüfung |
| `30 5 * * *` | `self_improve_engine.py` | Selbstverbesserung |
| `*/15 * * * *` | `deals_stats.py` | Traffic-Stats für deals.stean.info |
| `0 8 * * *` | `linkedin_pipeline_engine.py` | LinkedIn-Post-Entwurf |
| `0 9 * * *` | `instagram_flyer_engine.py` | ChefBlick-Werbebild-Entwurf |

## Installation & Entwicklung (lokal)

```bash
pip install -r requirements.txt
python app.py
```

Für vollen Funktionsumfang werden `settings.json` und `config/.env`
(Anthropic-/OpenAI-Keys, nicht im Repo, siehe `.gitignore`) benötigt.

### Mobile App (`siggi_mobile/`)

```bash
cd siggi_mobile
flutter pub get
flutter build apk --release   # → build/app/outputs/flutter-apk/app-release.apk
```

Benötigt Flutter + Android-SDK lokal (`flutter doctor`). Server-URL und Login
werden beim ersten Start in der App abgefragt (Standard: `stean.info`,
gleicher Login wie das Web-Dashboard).

## License

Proprietary – Alle Rechte vorbehalten.

---

**Made with ❤️ by Stefan Mutter**
