"""Selbstverbesserungs-Engine für Siggi.

Analysiert regelmäßig Wissenslücken-Mails und protokollierte Tool-Fehler,
lässt Claude daraus konkrete Verbesserungsvorschläge ableiten und legt sie
in der Tabelle `improvement_suggestions` ab. Eindeutige, wiederkehrende
Wissenslücken mit hoher Konfidenz werden automatisch als neuer
knowledge_gap_prompt übernommen; alles andere bleibt als Vorschlag zur
manuellen Freigabe stehen. Jede Statusänderung bleibt als Log-Eintrag
(mit Zeitstempel) erhalten, nichts wird gelöscht.
"""
import json
import re
import sqlite3
import datetime

import requests

DB_PATH = '/opt/stean/mails.db'
SETTINGS_PATH = '/opt/stean/settings.json'


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings):
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def init_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS improvement_suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        title TEXT,
        detail TEXT,
        confidence TEXT,
        auto_apply_prompt TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    conn.commit()
    conn.close()


def _gather_context():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()

    c.execute(
        "SELECT subject, from_addr, created_at FROM mails "
        "WHERE category='knowledge_gap' AND deleted=0 AND created_at > ? "
        "ORDER BY created_at DESC LIMIT 40", (cutoff,)
    )
    gaps = [{'subject': r[0], 'from': r[1], 'at': r[2]} for r in c.fetchall()]

    try:
        c.execute(
            "SELECT tool, input, output, created_at FROM siggi_actions "
            "WHERE created_at > ? AND "
            "(output LIKE '%Fehler%' OR output LIKE '%nicht konfiguriert%' OR output LIKE '%nicht verfügbar%') "
            "ORDER BY created_at DESC LIMIT 40", (cutoff,)
        )
        action_errors = [{'tool': r[0], 'input': r[1], 'output': r[2], 'at': r[3]} for r in c.fetchall()]
    except sqlite3.OperationalError:
        action_errors = []

    conn.close()
    return gaps, action_errors


def _call_claude(system_prompt, user_content, api_key):
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 2000,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_content}]
        },
        timeout=60
    )
    result = response.json()
    text = result['content'][0]['text'].strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'```$', '', text).strip()
    return json.loads(text)


def _insert_suggestion(c, type_, title, detail, confidence, auto_apply_prompt, status):
    c.execute(
        'INSERT INTO improvement_suggestions '
        '(type, title, detail, confidence, auto_apply_prompt, status, decided_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (type_, title, detail, confidence,
         json.dumps(auto_apply_prompt, ensure_ascii=False) if auto_apply_prompt else None,
         status,
         datetime.datetime.now().isoformat() if status != 'pending' else None)
    )
    return c.lastrowid


def run_analysis():
    """Analysiert die letzten 14 Tage und legt neue Vorschläge an.
    Gibt (anzahl_neuer_vorschlaege, anzahl_auto_uebernommen) zurück."""
    init_table()
    settings = load_settings()
    api_key = settings.get('anthropic_api_key', '')
    if not api_key or api_key == 'HIER_API_KEY_EINTRAGEN':
        return 0, 0

    gaps, action_errors = _gather_context()
    if not gaps and not action_errors:
        return 0, 0

    existing_prompts = settings.get('knowledge_gap_prompts', [])

    system_prompt = """Du analysierst den Betrieb des E-Mail-Assistenten SIGGI (Webdesign-/Software-Agentur ChefBlick)
und schlägst konkrete Selbstverbesserungen vor: fehlende Wissens-Prompts (wiederkehrende Wissenslücken,
für die es noch keine Anweisung gibt) und fehlende Tools/Fähigkeiten (wiederkehrende Fehler bei Tool-Aufrufen).

Antworte NUR mit einem JSON-Array, jedes Element:
{
  "type": "new_prompt" | "tool_idea" | "process",
  "title": "kurzer Titel (max 80 Zeichen)",
  "detail": "1-3 Sätze Begründung, was genau beobachtet wurde",
  "confidence": "high" | "medium" | "low",
  "auto_apply_prompt": {"trigger": "...", "response": "..."} oder null
}

Setze "auto_apply_prompt" NUR bei type="new_prompt" UND confidence="high" UND wenn du im Material
mindestens 3 klar gleichartige, wiederkehrende Fälle siehst, die eindeutig dieselbe Anweisung brauchen.
In allen anderen Fällen (weniger eindeutig, nur 1-2 Fälle, oder type != new_prompt): auto_apply_prompt=null,
das bleibt dann ein manuell zu prüfender Vorschlag. Erfinde nichts, was aus den Daten nicht hervorgeht.
Wenn nichts Sinnvolles vorzuschlagen ist, gib ein leeres Array [] zurück."""

    user_content = json.dumps({
        'bereits_vorhandene_prompts': existing_prompts,
        'wissensluecken_letzte_14_tage': gaps,
        'tool_fehler_letzte_14_tage': action_errors,
    }, ensure_ascii=False)

    try:
        suggestions = _call_claude(system_prompt, user_content, api_key)
    except Exception as e:
        print(f'[SelfImprove] Analyse-Fehler: {e}')
        return 0, 0

    if not isinstance(suggestions, list):
        return 0, 0

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    new_count = 0
    auto_count = 0

    for s in suggestions:
        type_ = s.get('type', 'process')
        title = (s.get('title') or '').strip()
        detail = (s.get('detail') or '').strip()
        confidence = s.get('confidence', 'low')
        auto_prompt = s.get('auto_apply_prompt')
        if not title:
            continue

        if type_ == 'new_prompt' and confidence == 'high' and auto_prompt and auto_prompt.get('trigger') and auto_prompt.get('response'):
            existing_prompts.append({'trigger': auto_prompt['trigger'], 'response': auto_prompt['response']})
            _insert_suggestion(c, type_, title, detail, confidence, auto_prompt, 'auto_applied')
            auto_count += 1
        else:
            _insert_suggestion(c, type_, title, detail, confidence, None, 'pending')
        new_count += 1

    conn.commit()
    conn.close()

    if auto_count:
        settings['knowledge_gap_prompts'] = existing_prompts
        save_settings(settings)

    return new_count, auto_count


def list_suggestions():
    init_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM improvement_suggestions ORDER BY created_at DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def approve_suggestion(suggestion_id):
    """Übernimmt einen manuell freigegebenen Vorschlag. Bei type=new_prompt ohne
    vorbereiteten auto_apply_prompt wird nur der Status gesetzt (Prompt-Text
    kommt in dem Fall vom Nutzer über die Wissenslücken-Ansicht selbst)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM improvement_suggestions WHERE id=?', (suggestion_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    if row['auto_apply_prompt']:
        settings = load_settings()
        prompts = settings.get('knowledge_gap_prompts', [])
        prompt = json.loads(row['auto_apply_prompt'])
        prompts.append({'trigger': prompt['trigger'], 'response': prompt['response']})
        settings['knowledge_gap_prompts'] = prompts
        save_settings(settings)

    c.execute(
        "UPDATE improvement_suggestions SET status='approved', decided_at=? WHERE id=?",
        (datetime.datetime.now().isoformat(), suggestion_id)
    )
    conn.commit()
    conn.close()
    return True


def dismiss_suggestion(suggestion_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE improvement_suggestions SET status='dismissed', decided_at=? WHERE id=?",
        (datetime.datetime.now().isoformat(), suggestion_id)
    )
    conn.commit()
    affected = c.rowcount
    conn.close()
    return bool(affected)


if __name__ == '__main__':
    n, a = run_analysis()
    print(f'Self-Improve-Check: {n} neue Vorschläge, davon {a} automatisch übernommen.')
