# n8n-Agenten für Siggi

n8n läuft als Docker-Container `n8n` auf dem VPS (`127.0.0.1:5678`), erreichbar unter
https://n8n.stean.info (nginx: `/etc/nginx/sites-available/n8n`, Let's-Encrypt-Zertifikat).

- Konfiguration: `/opt/n8n/n8n.env` (root, 600) – enthält `SIGGI_URL` und `SIGGI_TOKEN`.
  Den Token erzeugt `agents_engine.generate_token()`; in Siggi liegt nur sein Hash
  (`agents_api_token_hash` in settings.json). Neuer Token => n8n.env anpassen und
  Container neu erstellen (`docker rm -f n8n` + `docker run …`, Daten liegen im Volume `n8n_data`).
- Login-Daten der n8n-Oberfläche: `/opt/n8n/owner_login.txt`.
- Die Agenten melden jeden Schritt an `POST /api/agents/hook/<agent_id>` (siehe `agents_engine.py`),
  Siggi zeigt das live unter „Agenten“. Start-Webhooks `/webhook/siggi-*` sind von außen gesperrt
  und werden von Siggi intern über `127.0.0.1:5678` aufgerufen.
- Workflows: `build_workflows.py` erzeugt sie reproduzierbar (Website-Wächter, Server-Wächter).
