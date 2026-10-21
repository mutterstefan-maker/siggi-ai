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
- 📬 **Outlook-Agent (privat)** – liest Stefans privates Hotmail nur mit
  (Microsoft Graph, Recht `Mail.Read`), wichtige Mails per Telegram
  (`outlook_engine.py`)

## Kunden-Agenten (Chefblick)

Alle Mails an Kunden sind **Entwürfe mit Freigabe-Knopf in Telegram** – nichts
geht ungefragt raus. Versand mit Stefans Signatur, PDFs als echte Anhänge.

- 🛡️ **Website-Wächter + Wartung** (`kunden_engine.py`, `kunden_pdf.py`) –
  Kunden-Websites (Agenten → Website-Wächter → „+ Kunden-Website“, Tabelle
  `kunden_websites` in `mails.db`) werden an die n8n-Liste angehängt
  (`agents_engine.watch_sites`) und alle 15 Min. mitgeprüft. Jeder Check landet
  in `agents.db/site_checks` (400 Tage) → Erreichbarkeit, Ausfälle (≥ 2
  Fehlschläge in Folge), Ladezeit. Täglich 07:00: Kontaktformular gefunden und
  erreichbar (es wird nichts abgeschickt), Domain-Ablauf per RDAP (bei .de gibt
  DENIC kein Datum heraus). Ab dem Monatsersten 09:00: Wartungsbericht-PDF je
  Website mit Häkchen „Monatsbericht“ → Mail-Entwurf an den Kunden.
- 💌 **Check-in-Agent** (`checkin_engine.py`) – 30 und 90 Tage nach dem
  Go-live (Datum in der Kundenliste) eine kurze persönliche Mail mit genau
  einem passenden nächsten Schritt (Claude Haiku). Täglich 10:00.
- 📄 **Angebots-Agent** (`angebot_engine.py`) – prüft neue Mails nach jedem
  Abruf (Stichwort-Vorfilter → Haiku). Bei einer Projektanfrage: Analyse der
  bisherigen Website (Audit, Kundenversion), Claude Sonnet wählt das Paket und
  schreibt die Texte. **Preise kommen nur aus der Paketliste** (Agent-Karte,
  Einstellung `angebot_settings`; das Modell nennt nur die Paket-Nummer),
  brutto inkl. 19 % MwSt. Angebots-PDF + Analyse-PDF als Entwurf (`angebote/`,
  Tabelle `angebote`). Keine Angebote für Online-Shops. Der Mail-Agent
  beantwortet die Anfrage weiterhin sofort kurz; das Angebot kommt danach.

Schleife `_kunden_agents_loop` in `app.py` (alle 2 Min.); KI-Kosten in der
Verbrauchsübersicht unter „Angebote“ und „Check-ins“.

## Bereiche & Trennung

Stefans Vorgabe: **Chefblick und Privat werden nie vermischt** –
jeder Bereich hat eigene Daten, eigene Zugänge und eigene Regeln.

| Bereich | Modul | Daten / Zugang | Darf Siggi senden? | Hinweise an Stefan |
|---|---|---|---|---|
| Chefblick | `mail_engine.py` u.a. | `mails.db`, IMAP-Konten in `settings.json` | ja (Auto-Antworten, Freigaben) | Telegram / Mail |
| Privat (Outlook) | `outlook_engine.py` | `outlook.db`, `outlook_token.json` | **nein** – nur `Mail.Read` | **nur Telegram** |

- Das Privat-Modul berührt nie `mails.db`, `mail_engine`, Kontakte,
  Nachfass-Agent, Auto-Antworten, Autopilot oder das Gedächtnis.
- Hinweise aus diesem Bereich gehen bewusst **nicht per Mail** (der
  Mail-Fallback würde sie ins Chefblick-Postfach legen, das der Mail-Agent
  abruft und automatisch beantwortet).
- Das Chat-Werkzeug `outlook_privat` verbietet in seiner
  Beschreibung, Inhalte in Chefblick-Mails, Posts, LinkedIn, Gedächtnis oder an
  Dritte weiterzugeben.
- Token-Dateien liegen mit Rechten `0600` auf dem Server und sind gitignored.

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
