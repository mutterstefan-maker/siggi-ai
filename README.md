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
- 🏢 **kChat-Agent (Steler)** – behält den Firmen-Chat der Steler GmbH
  (Infomaniak kChat) im Blick: Lagebericht per Telegram, Erwähnungen sofort,
  Fragen im Chat – nur lesen (`kchat_engine.py`, Details unten)

## Bereiche & Trennung

Stefans Vorgabe: **Chefblick, Privat und Steler GmbH werden nie vermischt** –
jeder Bereich hat eigene Daten, eigene Zugänge und eigene Regeln.

| Bereich | Modul | Daten / Zugang | Darf Siggi senden? | Hinweise an Stefan |
|---|---|---|---|---|
| Chefblick | `mail_engine.py` u.a. | `mails.db`, IMAP-Konten in `settings.json` | ja (Auto-Antworten, Freigaben) | Telegram / Mail |
| Privat (Outlook) | `outlook_engine.py` | `outlook.db`, `outlook_token.json` | **nein** – nur `Mail.Read` | **nur Telegram** |
| Steler GmbH (kChat) | `kchat_engine.py` | `kchat.db`, `kchat_token.json`, Einstellung `kchat` | **nein** – nur lesen | **nur Telegram** |

- Privat- und Steler-Module berühren nie `mails.db`, `mail_engine`, Kontakte,
  Nachfass-Agent, Auto-Antworten, Autopilot oder das Gedächtnis.
- Hinweise aus diesen Bereichen gehen bewusst **nicht per Mail** (der
  Mail-Fallback würde sie ins Chefblick-Postfach legen, das der Mail-Agent
  abruft und automatisch beantwortet).
- Die Chat-Werkzeuge (`outlook_privat`, `kchat_steler`) verbieten in ihrer
  Beschreibung, Inhalte in Chefblick-Mails, Posts, LinkedIn, Gedächtnis oder an
  Dritte weiterzugeben.
- Token-Dateien liegen mit Rechten `0600` auf dem Server und sind gitignored.

### kChat-Agent (Steler GmbH)

**Zweck:** Stefan hat immer einen Überblick, was im Firmen-Chat gerade aktuell
ist – ohne selbst alle Kanäle durchzulesen.

**Stand:** **nur Lesezugriff.** Vorerst geht nichts automatisch raus – keine
Nachrichten, keine Antworten, keine Reaktionen. `_api()` lehnt jeden
POST-Aufruf ab außer den zwei lesenden (`/posts/search`, `/users/ids`); ein
Schreibbefehl ist im Code nicht vorhanden. Stefans Ungelesen-Zähler in kChat
bleibt unverändert. Hinweis: Das Infomaniak-Token selbst erlaubt technisch
auch Schreiben (kein reines Lese-Recht verfügbar) – die Sperre liegt im Code.

**Was er macht:**
- **Lagebericht per Telegram** zu festen Uhrzeiten (Standard 08:00 und 16:00,
  Mo–Fr): Was ist aktuell, was wartet auf Stefan (➡️). Zusammenfassung per
  Claude Haiku (Bruchteil eines Cents), abschaltbar → dann nur Liste je Kanal.
  Kein Bericht, wenn nichts Neues kam. Verpasste Uhrzeit wird bis 2 Std.
  danach nachgeholt.
- **Sofort-Hinweis** per Telegram bei Direktnachricht, `@`-Erwähnung,
  `@channel/@all/@here` oder einstellbaren Stichwörtern – mit Knopf
  „In kChat öffnen“. Jede Nachricht wird nur einmal gemeldet.
- **Chat-Werkzeug `kchat_steler`** (Dashboard-Chat und Telegram):
  `ueberblick` (Ungelesenes + Nachrichten der letzten Stunden), `kanal`
  (Verlauf eines Kanals), `suchen` (Volltext), `kanaele` (Liste mit
  Ungelesen-Zahlen) – z. B. „Was ist bei Steler los?“.
- **Agenten-Karte** zeigt Ungelesen-Zahlen je Kanal; „Jetzt starten“ schickt
  sofort einen Lagebericht. Prüfintervall (Standard 15 Min.), Uhrzeiten,
  Stichwörter und stumme Kanäle sind dort einstellbar.

**Sofort-Meldungen:** Prüfintervall bis 1 Minute möglich. Nachrichten, die in
kChat als „Dringend“/„Wichtig“ markiert sind (`metadata.priority`), werden
immer sofort gemeldet.

**Schreibstil lernen (Stufe 1, nur lesen):** Siggi speichert **nur Stefans
eigene** kChat-Nachrichten (Tabelle `own_posts` in `kchat.db`, bei neuen
Nachrichten zusätzlich die Nachricht davor als Kontext). Beim Verbinden bzw.
per Knopf „Stil jetzt neu lernen“ werden die letzten 180 Tage per Suche
`from:<benutzer>` eingelesen. Daraus baut Haiku wöchentlich ein Stilprofil
(Ton, Länge, Anrede, Schreibweise, typische Wendungen, Unterschiede je
Person). Es ist auf der Agenten-Karte und per Chat (`kchat_steler`,
`aktion=mein_stil`) abrufbar. Gesendet wird nichts. Geplant: Stufe 2 =
Antwort-Vorschläge mit Freigabe-Knopf in Telegram, Stufe 3 = eng begrenzte
Selbst-Antworten – beides erst nach ausdrücklicher Entscheidung (hebt die
Lese-Sperre auf).

**Technik:** kChat ist ein Mattermost – REST-API
`https://<team>.kchat.infomaniak.com/api/v4`, Header
`Authorization: Bearer <Token>`. Genutzt: `users/me`, `teams/name/<team>`,
`users/me/teams/<id>/channels(/members)`, `channels/<id>/posts?since=`,
`teams/<id>/posts/search`, `users/ids`. Hintergrund-Schleife `_kchat_loop` in
`app.py` (prüft jede Minute, ob Lauf oder Bericht fällig ist). KI-Kosten
laufen in der Verbrauchsübersicht unter „kChat (Steler)“.

**Einrichtung (einmalig):**
1. [manager.infomaniak.com → API-Token](https://manager.infomaniak.com/v3/ng/accounts/token/list)
   → **Token erstellen**, Name „Siggi“, Bereich nur **kChat**. Token wird nur
   einmal angezeigt.
2. Siggi → Agenten → **kChat-Agent (Steler)** → Team (aus der kChat-Adresse,
   z. B. `steler-gmbh`) und Token eintragen → **Verbinden**.
3. Ab dem Verbinden zählt nur Neues – alte Nachrichten werden nicht gemeldet.

Wird das Token ungültig (gelöscht, abgelaufen), entfernt Siggi die Verbindung
und meldet das per Telegram. Infomaniak deaktiviert Tokens, die ein Jahr
nicht benutzt wurden – bei laufendem Agenten passiert das nicht.

### Infomaniak kSuite – weitere Möglichkeiten (noch nicht gebaut)

| kSuite-Teil | Schnittstelle | Stand |
|---|---|---|
| kChat | Mattermost-API | ✅ gebaut (nur lesen) |
| Mail (Steler-Postfach) | IMAP/SMTP `mail.infomaniak.com` | offen – **nicht** in den Chefblick-Mail-Agenten hängen (der antwortet automatisch); wenn, dann als eigener Steler-Bereich nur lesend |
| Kalender | CalDAV | offen |
| Kontakte | CardDAV | offen |
| kDrive | WebDAV | offen |

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
