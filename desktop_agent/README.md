# Siggi Desktop-Agent

Lokaler Companion-Agent, der Siggi (remote gehostet) Zugriff auf diesen PC gibt:
Dateien lesen/schreiben (nur whitelistete Ordner), Screenshots, Programme öffnen,
Word-Dokumente erstellen, Maus/Tastatur-Steuerung.

## Einrichtung

1. In Siggi unter **Einstellungen > Desktop-Zugriff** einen Pairing-Token erzeugen.
2. `agent_config.example.json` nach `agent_config.json` kopieren, `server_url` und
   `device_token` eintragen, `allowed_dirs` auf die gewünschten Ordner anpassen.
3. Dependencies installieren: `pip install -r requirements.txt`
4. Testlauf: `python agent.py` — ein Tray-Icon erscheint (grün = verbunden).
5. Autostart einrichten: `python install.py`

## Sicherheit

- Es sind nur Aktionen innerhalb der in `allowed_dirs` gelisteten Ordner erlaubt.
- Riskante Aktionen (Schreiben, Programme starten/schließen, Klicks/Tastatur) werden
  serverseitig erst nach Bestätigung im Chat an den Agent geschickt.
- Der Device-Token ist in Siggis Einstellungen jederzeit widerrufbar.
