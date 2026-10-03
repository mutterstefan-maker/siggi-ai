"""Erzeugt die n8n-Workflows fuer Website-Waechter und Server-Waechter.

Laeuft im n8n-Docker-Container auf dem Server (siehe n8n/README.md). Neu einspielen:
  python build_workflows.py   -> watch.json, server.json
  docker cp watch.json n8n:/tmp/ && docker exec n8n n8n import:workflow --input=/tmp/watch.json
  docker exec n8n n8n publish:workflow --id=SiggiWebWaechter && docker restart n8n
"""
import json
import uuid

AUTH = {"parameters": [{"name": "Authorization", "value": "=Bearer {{ $env.SIGGI_TOKEN }}"}]}


def http(name, agent, body_expr, pos, timeout=60000):
    return {
        "id": str(uuid.uuid4()), "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "position": pos,
        "parameters": {
            "method": "POST",
            "url": "={{ $env.SIGGI_URL }}/api/agents/hook/" + agent,
            "sendHeaders": True, "headerParameters": AUTH,
            "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify(" + body_expr + ") }}",
            "options": {"timeout": timeout},
        },
    }


def code(name, js, pos):
    return {"id": str(uuid.uuid4()), "name": name, "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": pos, "parameters": {"jsCode": js}}


def webhook(name, path, pos):
    return {"id": str(uuid.uuid4()), "name": name, "type": "n8n-nodes-base.webhook", "typeVersion": 2,
            "position": pos, "webhookId": str(uuid.uuid4()),
            "parameters": {"httpMethod": "POST", "path": path, "responseMode": "onReceived", "options": {}}}


def note(text, pos, w=420, h=200):
    return {"id": str(uuid.uuid4()), "name": "Notiz " + str(uuid.uuid4())[:4], "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1, "position": pos, "parameters": {"content": text, "width": w, "height": h}}


def link(*pairs):
    conns = {}
    for src, dst, *idx in pairs:
        out = idx[0] if idx else 0
        conns.setdefault(src, {"main": []})
        while len(conns[src]["main"]) <= out:
            conns[src]["main"].append([])
        conns[src]["main"][out].append({"node": dst, "type": "main", "index": 0})
    return conns


# ── Website-Waechter ──────────────────────────────────────────────────
PLAN_RUN = ("[{label:'Websites von Siggi holen', state:'done'},"
            "{label:'Websites prüfen', state:'active', note: $json.index + '/' + $json.total},"
            "{label:'Ergebnis auswerten', state:'open'},"
            "{label:'Bei Problemen Mail an Stefan', state:'open'}]")

watch_nodes = [
    note("## Website-Wächter\nPrüft jede Website aus Siggi (Agenten → Website-Wächter → Websites): erreichbar?, Ladezeit, Tage bis SSL-Ablauf.\n\nJeder Schritt wird live an Siggi gemeldet. Probleme schickt Siggi per Mail.\n\nWebsites ändern: im Siggi-Dashboard, nicht hier.", [-40, -260], 520, 220),
    {"id": str(uuid.uuid4()), "name": "Jeden Montag 8:00", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [0, 0], "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * 1"}]}}},
    webhook("Start aus Siggi", "siggi-watch-run", [0, 200]),
    http("Bei Siggi anmelden", "watch", "{type:'start', bubble:'Hole die Liste der Websites …', text:'Lauf gestartet'}", [260, 100]),
    code("Websites vorbereiten", """// Pausiert? Dann hier aufhören.
if (!$json.run) return [];
const sites = ($json.config && $json.config.sites) || [];
return sites.map((url, i) => ({ json: { url, index: i + 1, total: sites.length } }));""", [500, 100]),
    {"id": str(uuid.uuid4()), "name": "Eine nach der anderen", "type": "n8n-nodes-base.splitInBatches", "typeVersion": 3,
     "position": [740, 100], "parameters": {"batchSize": 1, "options": {}}},
    http("Fortschritt an Siggi", "watch",
         "{type:'status', status:'working', bubble:'Prüfe ' + $json.url.replace(/^https?:\\/\\//,'') + ' (' + $json.index + ' von ' + $json.total + ')', progress: ($json.index - 1) / $json.total, plan: " + PLAN_RUN + "}",
         [980, 260]),
    http("Website prüfen", "watch", "{type:'site_check', url: $('Eine nach der anderen').item.json.url}", [1220, 260]),
    code("Ergebnis auswerten", """const results = $input.all().map(i => i.json);
const problems = [];
const host = u => u.replace(/^https?:\\/\\//, '').replace(/\\/$/, '');
const steps = results.map(r => {
  const h = host(r.url);
  const own = [];
  if (!r.ok) own.push(`${h}: nicht erreichbar (${r.status || r.error || 'keine Antwort'})`);
  else if (r.ms > 4000) own.push(`${h}: lädt langsam (${(r.ms / 1000).toFixed(1)} s)`);
  if (r.ssl_days != null && r.ssl_days < 14) own.push(`${h}: SSL-Zertifikat läuft in ${r.ssl_days} Tagen ab`);
  if (r.ssl_error) own.push(`${h}: SSL-Problem (${r.ssl_error})`);
  problems.push(...own);
  const time = r.ms < 1000 ? `${r.ms} ms` : `${(r.ms / 1000).toFixed(1)} s`;
  const note = r.ok ? `${time} · SSL ${r.ssl_days != null ? r.ssl_days + ' T.' : '–'}` : 'nicht erreichbar';
  return { label: h, state: own.length ? 'error' : 'done', note };
});
const summary = problems.length ? `${problems.length} Problem(e) bei ${results.length} Websites` : `Alle ${results.length} Websites OK`;
return [{ json: {
  type: 'finish', status: 'sleeping', summary, problems,
  bubble: problems.length ? summary + ' – Mail ist raus' : summary + ' – schlafe bis Montag',
  plan: [{ label: 'Websites von Siggi holen', state: 'done' }, ...steps,
         { label: 'Bei Problemen Mail an Stefan', state: 'done', note: problems.length ? 'gesendet' : 'nicht nötig' }],
} }];""", [980, -60]),
    http("Ergebnis an Siggi", "watch", "$json", [1220, -60]),
]
watch = {
    "id": "SiggiWebWaechter", "versionId": str(uuid.uuid4()),
    "name": "Website-Wächter", "nodes": watch_nodes, "active": False,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"},
    "connections": link(
        ("Jeden Montag 8:00", "Bei Siggi anmelden"),
        ("Start aus Siggi", "Bei Siggi anmelden"),
        ("Bei Siggi anmelden", "Websites vorbereiten"),
        ("Websites vorbereiten", "Eine nach der anderen"),
        ("Eine nach der anderen", "Ergebnis auswerten", 0),
        ("Eine nach der anderen", "Fortschritt an Siggi", 1),
        ("Fortschritt an Siggi", "Website prüfen"),
        ("Website prüfen", "Eine nach der anderen"),
        ("Ergebnis auswerten", "Ergebnis an Siggi"),
    ),
}

# ── Server-Waechter ───────────────────────────────────────────────────
server_nodes = [
    note("## Server-Wächter\nMisst alle 10 Minuten CPU, Arbeitsspeicher, Festplatte und ob Siggi, Mail-Abruf, nginx und Docker laufen.\n\nDie Grenzen (ab wann gewarnt wird) stellst du in Siggi ein. Bei Überschreitung schickt Siggi eine Mail – höchstens alle 6 Stunden pro Problem.", [-40, -260], 520, 220),
    {"id": str(uuid.uuid4()), "name": "Alle 10 Minuten", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [0, 0], "parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}}},
    webhook("Start aus Siggi", "siggi-server-run", [0, 200]),
    http("Bei Siggi anmelden", "server", "{type:'start', quiet:true, bubble:'Messe die Auslastung …'}", [260, 100]),
    code("Pausiert?", "// Pausiert? Dann hier aufhören.\nreturn $json.run ? [$input.first()] : [];", [500, 100]),
    http("Messwerte holen", "server", "{type:'metrics'}", [740, 100]),
    code("Auswerten", """const m = $json.metrics;
const cfg = $('Bei Siggi anmelden').first().json.config || {};
const cpuWarn = cfg.cpu_warn || 85, ramWarn = cfg.ram_warn || 90, diskWarn = cfg.disk_warn || 85;
// CPU über 5 Minuten gemittelt (Load / Kerne) - ein Einzelwert schwankt zu stark
const cpu5 = Math.round(100 * m.load[1] / m.cpu_count);
const down = Object.entries(m.services).filter(([, s]) => s !== 'active').map(([u, s]) => `${u.replace('.service', '')} (${s})`);
const problems = [];
if (cpu5 > cpuWarn) problems.push(`CPU: ${cpu5} % Auslastung (Grenze ${cpuWarn} %)`);
if (m.ram_percent > ramWarn) problems.push(`Arbeitsspeicher: ${m.ram_percent} % belegt (Grenze ${ramWarn} %)`);
if (m.disk_percent > diskWarn) problems.push(`Festplatte: ${m.disk_percent} % voll, nur noch ${m.disk_free_gb} GB frei`);
if (down.length) problems.push(`Dienste: ${down.join(', ')} läuft nicht`);
// Ampel: rot ab Warngrenze, gelb ab 80 % der Grenze ("grenzwertig"), sonst gruen
const lvl = (v, w) => v > w ? 'error' : v >= 0.8 * w ? 'warn' : 'done';
const plan = [
  { label: 'CPU-Auslastung (5 Min.)', state: lvl(cpu5, cpuWarn), note: `${cpu5} %` },
  { label: 'Arbeitsspeicher', state: lvl(m.ram_percent, ramWarn), note: `${m.ram_used_gb} / ${m.ram_total_gb} GB` },
  { label: 'Festplatte', state: lvl(m.disk_percent, diskWarn), note: `${m.disk_percent} % · ${m.disk_free_gb} GB frei` },
  { label: 'Dienste: Siggi, Mail, nginx, Docker', state: down.length ? 'error' : 'done', note: down.length ? down.length + ' aus' : 'alle aktiv' },
  { label: 'Server läuft seit', state: 'done', note: `${m.uptime_days} Tagen` },
];
return [{ json: {
  type: 'finish', quiet: true, status: problems.length ? 'error' : 'ready', problems,
  summary: problems.length ? `${problems.length} Problem(e)` : 'Alles im grünen Bereich',
  bubble: `CPU ${cpu5} % · RAM ${m.ram_percent} % · Platte ${m.disk_percent} %`,
  gauges: [
    { label: 'CPU', value: cpu5, warn: cpuWarn, detail: `Last über 5 Min., ${m.cpu_count} Kerne` },
    { label: 'RAM', value: m.ram_percent, warn: ramWarn, detail: `${m.ram_used_gb} von ${m.ram_total_gb} GB` },
    { label: 'Platte', value: m.disk_percent, warn: diskWarn, detail: `${m.disk_free_gb} GB frei` },
  ],
  plan,
} }];""", [980, 100]),
    http("Ergebnis an Siggi", "server", "$json", [1220, 100]),
]
server = {
    "id": "SiggiSrvWaechter", "versionId": str(uuid.uuid4()),
    "name": "Server-Wächter", "nodes": server_nodes, "active": False,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"},
    "connections": link(
        ("Alle 10 Minuten", "Bei Siggi anmelden"),
        ("Start aus Siggi", "Bei Siggi anmelden"),
        ("Bei Siggi anmelden", "Pausiert?"),
        ("Pausiert?", "Messwerte holen"),
        ("Messwerte holen", "Auswerten"),
        ("Auswerten", "Ergebnis an Siggi"),
    ),
}

# ── Wochenbericht ─────────────────────────────────────────────────────
REPORT_PLAN = lambda a, b, c, d: ("[{label:'Zahlen der Woche bei Siggi holen', state:'" + a + "'},"
                                   "{label:'Bericht schreiben', state:'" + b + "'},"
                                   "{label:'Per Mail an Stefan schicken', state:'" + c + "'},"
                                   "{label:'Fertig', state:'" + d + "'}]")
report_nodes = [
    note("## Wochenbericht\nJeden Montag 07:30: holt die Zahlen der letzten 7 Tage aus Siggi (Mails, Posts, Kontakte, Probleme der Agenten, Server) und schickt eine Zusammenfassung per Mail.\n\nOhne KI – kostet nichts.", [-40, -260], 520, 200),
    {"id": str(uuid.uuid4()), "name": "Jeden Montag 7:30", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
     "position": [0, 0], "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "30 7 * * 1"}]}}},
    webhook("Start aus Siggi", "siggi-report-run", [0, 200]),
    http("Bei Siggi anmelden", "report", "{type:'start', bubble:'Sammle die Zahlen der Woche …', text:'Wochenbericht gestartet'}", [260, 100]),
    code("Pausiert?", "// Pausiert? Dann hier aufhören.\nreturn $json.run ? [$input.first()] : [];", [500, 100]),
    http("Fortschritt: sammeln", "report", "{type:'status', status:'working', bubble:'Sammle die Zahlen der Woche …', plan: " + REPORT_PLAN('active', 'open', 'open', 'open') + "}", [740, 100]),
    http("Zahlen holen", "report", "{type:'report_data'}", [980, 100]),
    code("Bericht schreiben", """const s = $json.stats;
const n = v => (v === null || v === undefined) ? '–' : v;
const from = new Date(Date.now() - 7 * 86400000).toLocaleDateString('de-DE');
const to = new Date().toLocaleDateString('de-DE');
const lines = [];
lines.push(`Hallo Stefan,`, ``, `hier ist dein Siggi-Wochenbericht (${from} – ${to}).`, ``);
lines.push(`MAILS`);
lines.push(`- ${n(s.mails_eingang)} eingegangen, davon ${n(s.mails_auto_beantwortet)} automatisch beantwortet, ${n(s.mails_spam)} Spam`);
lines.push(`- ${n(s.mails_gesendet)} Mails verschickt`);
lines.push(`- ${n(s.neue_kontakte)} neue Kontakte`);
if (s.mail_entwuerfe_offen) lines.push(`- ${s.mail_entwuerfe_offen} Mail-Entwürfe warten auf deine Freigabe`);
lines.push(``, `SOCIAL MEDIA`);
lines.push(`- Instagram: ${n(s.instagram_posts)} Bilder gepostet${s.instagram_fehler ? `, ${s.instagram_fehler} fehlgeschlagen` : ''}`);
lines.push(`- Stories/Reels: ${n(s.stories_gepostet)} gepostet`);
lines.push(`- LinkedIn: ${n(s.linkedin_entwuerfe)} Entwürfe, ${n(s.linkedin_gepostet)} gepostet, ${n(s.linkedin_warteschlange)} in der Warteschlange${s.linkedin_offen ? `, ${s.linkedin_offen} warten auf Freigabe` : ''}`);
lines.push(`- Bilder: ${n(s.bilder_erzeugt)} erzeugt${s.bilder_fehlgeschlagen ? `, ${s.bilder_fehlgeschlagen} fehlgeschlagen` : ''}`);
if (s.audits) lines.push(``, `WEBSITE-AUDITS`, `- ${s.audits} Audits erstellt`);
lines.push(``, `PROBLEME DER AGENTEN`);
const probs = s.agenten_probleme || [];
if (!probs.length) lines.push('- keine 🎉');
for (const p of probs.slice(0, 10)) lines.push(`- ${p.agent}: ${p.text}`);
if (s.server) {
  const m = s.server;
  lines.push(``, `SERVER`, `- CPU-Last ${Math.round(100 * m.load[1] / m.cpu_count)} %, Arbeitsspeicher ${m.ram_percent} %, Festplatte ${m.disk_percent} % (${m.disk_free_gb} GB frei)`);
}
lines.push(``, `Details im Dashboard unter „Agenten“.`, ``, `Siggi`);
const headline = `${n(s.mails_eingang)} Mails · ${n(s.instagram_posts)} Insta-Posts · ${n(s.linkedin_gepostet)} LinkedIn · ${probs.length} Problem(e)`;
return [{ json: { subject: `Siggi-Wochenbericht ${from} – ${to}`, body: lines.join('\\n'), headline } }];""", [1220, 100]),
    http("Fortschritt: senden", "report", "{type:'status', status:'working', bubble:'Schicke dir den Bericht per Mail …', plan: " + REPORT_PLAN('done', 'done', 'active', 'open') + "}", [1460, 100]),
    http("Mail an Stefan", "report", "{type:'send_mail', subject: $('Bericht schreiben').item.json.subject, body: $('Bericht schreiben').item.json.body}", [1700, 100]),
    http("Ergebnis an Siggi", "report", "{type:'finish', status:'sleeping', summary: 'Bericht verschickt: ' + $('Bericht schreiben').item.json.headline, bubble: 'Bericht ist raus – ' + $('Bericht schreiben').item.json.headline, plan: " + REPORT_PLAN('done', 'done', 'done', 'done') + "}", [1940, 100]),
]
report = {
    "id": "SiggiWochenBrcht", "versionId": str(uuid.uuid4()),
    "name": "Wochenbericht", "nodes": report_nodes, "active": False,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin"},
    "connections": link(
        ("Jeden Montag 7:30", "Bei Siggi anmelden"),
        ("Start aus Siggi", "Bei Siggi anmelden"),
        ("Bei Siggi anmelden", "Pausiert?"),
        ("Pausiert?", "Fortschritt: sammeln"),
        ("Fortschritt: sammeln", "Zahlen holen"),
        ("Zahlen holen", "Bericht schreiben"),
        ("Bericht schreiben", "Fortschritt: senden"),
        ("Fortschritt: senden", "Mail an Stefan"),
        ("Mail an Stefan", "Ergebnis an Siggi"),
    ),
}

for fname, wf in (("watch.json", watch), ("server.json", server), ("report.json", report)):
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=1)
print("ok")
