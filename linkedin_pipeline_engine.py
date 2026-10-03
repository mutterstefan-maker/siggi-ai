"""LinkedIn-Content-Pipeline für Siggi.

Erstellt taeglich einen LinkedIn-Post-Entwurf im Stil von Stefans bisherigen
Posts (Chefblick / E-Commerce-Beratung), abgeleitet aus linkedin_posts.json.
Freigeben legt einen Entwurf in die Warteschlange (status 'approved'); gepostet
wird automatisch nach Zeitplan (linkedin_post_settings, wie bei Instagram) -
immer der aelteste freigegebene Beitrag. Faellt die Warteschlange unter
QUEUE_WARN_BELOW, meldet der LinkedIn-Agent das (Zentrale + Mail).
Ab der 50. Freigabe kommen neue Entwuerfe ohne manuelle Pruefung direkt in die
Warteschlange (analog zum Wissensluecken-Lernmechanismus bei Mails).
"""
import json
import random
import re
import sqlite3
import datetime
import os

import requests
from dotenv import load_dotenv

import linkedin_engine
import settings_store
import siggi_time

load_dotenv('/opt/stean/config/.env')

DB_PATH = '/opt/stean/mails.db'
SETTINGS_PATH = '/opt/stean/settings.json'
POSTS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linkedin_posts.json')

AUTO_POST_THRESHOLD = 50
QUEUE_WARN_BELOW = 2   # weniger freigegebene Beitraege -> Agent meldet sich
STOCK_TARGET = 3       # Siggi schreibt selbst nach, bis freigegebene + wartende Entwuerfe diese Zahl erreichen
MAX_NEW_PER_RUN = 3    # hoechstens so viele neue Entwuerfe pro Lauf (Kostenbremse)
DEFAULT_POST_SETTINGS = {'auto_enabled': '1', 'post_times': '10:00', 'post_days': 'mon,tue,wed,thu,fri,sat,sun'}

TOPIC_FOCUS = (
    "Chefblick / E-Commerce-Beratung: Website-Erstellung, Online-Shops, "
    "Digitalisierung fuer Unternehmer, IT-Sicherheit, DSGVO/Rechtssicherheit "
    "im Netz, KI im Business (pragmatisch, nicht hypey), Alltag als "
    "Agentur-Inhaber."
)


def load_settings():
    return settings_store.load_or_empty()


def save_settings(settings):
    return settings_store.save(settings)


def init_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS linkedin_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        text TEXT,
        format_style TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        decided_at DATETIME
    )''')
    existing_cols = {row[1] for row in c.execute('PRAGMA table_info(linkedin_drafts)').fetchall()}
    for col_def in ('format_style TEXT', 'rating TEXT', 'feedback_tags TEXT', 'feedback_comment TEXT',
                    'posted_at DATETIME', 'post_error TEXT'):
        col_name = col_def.split()[0]
        if col_name not in existing_cols:
            c.execute(f'ALTER TABLE linkedin_drafts ADD COLUMN {col_def}')
    conn.commit()
    conn.close()


FORMAT_STYLES = [
    {
        'key': 'hook_bulletliste',
        'label': 'Hook-Frage + Bulletpoint-Liste',
        'desc': (
            "Einstieg mit Hook-Frage oder ueberraschender Aussage, dann eine klare "
            "Bulletpoint-Liste mit Problemen/Symptomen (Emoji wie ❌), Abschluss mit "
            "offener Frage. Das ist das klassische Format - nur nutzen wenn es lange "
            "nicht mehr dran war."
        ),
    },
    {
        'key': 'kurze_these',
        'label': 'Kurze steile These (kein Bulletpoint)',
        'desc': (
            "Kurzer, knackiger Fliesstext OHNE Bulletpoints/Listen. Eine steile, "
            "vielleicht leicht kontroverse These zum Thema aufstellen, dann in 2-3 "
            "kurzen Absaetzen begruenden. Max 120 Woerter. Wirkt wie ein spontaner "
            "LinkedIn-Gedanke, nicht wie ein Ratgeber-Post."
        ),
    },
    {
        'key': 'persoenliche_anekdote',
        'label': 'Persoenliche Alltagsgeschichte',
        'desc': (
            "Erzaehlt chronologisch eine kurze, konkrete Alltagsszene/Kundengespraech "
            "aus Stefans Agentur-Alltag (mit Datum/Situation), OHNE Bulletpoints, "
            "die dann zu einer Erkenntnis/Lehre fuehrt. Persoenlich, story-artig."
        ),
    },
    {
        'key': 'zahlen_fakten',
        'label': 'Zahlen/Fakten-Format',
        'desc': (
            "Startet mit einer konkreten Zahl oder Statistik zum Thema, erklaert kurz "
            "die Relevanz fuer Unternehmer, nennt 2-3 konkrete Handlungsempfehlungen "
            "als kurze nummerierte Liste (1. 2. 3., NICHT mit Emoji-Bullets)."
        ),
    },
    {
        'key': 'vorher_nachher',
        'label': 'Vorher/Nachher-Kontrast',
        'desc': (
            "Beschreibt kurz einen 'Vorher'-Zustand (Problem/Chaos) und dann den "
            "'Nachher'-Zustand nach der Loesung, in zwei klar getrennten kurzen "
            "Abschnitten (koennen mit 'Vorher:' / 'Nachher:' markiert sein), ohne "
            "lange Bulletlisten."
        ),
    },
    {
        'key': 'mythos_check',
        'label': 'Mythos-Check / Missverstaendnis auflösen',
        'desc': (
            "Beginnt mit einem weit verbreiteten Irrglauben/Missverstaendnis als Zitat "
            "in Anfuehrungszeichen, widerlegt ihn dann sachlich in Fliesstext (kein "
            "Bulletpoint-Zwang), schliesst mit einer klaren Handlungsempfehlung."
        ),
    },
]


def _pick_format_style(exclude_last_n=3):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT format_style FROM linkedin_drafts WHERE format_style IS NOT NULL "
        "ORDER BY created_at DESC LIMIT ?", (exclude_last_n,)
    )
    recent = {r[0] for r in c.fetchall() if r[0]}
    conn.close()
    candidates = [s for s in FORMAT_STYLES if s['key'] not in recent] or FORMAT_STYLES
    return random.choice(candidates)


def _load_example_posts(limit=6):
    if not os.path.exists(POSTS_LOG_PATH):
        return []
    try:
        with open(POSTS_LOG_PATH, encoding='utf-8') as f:
            posts = json.load(f)
        return [p['text'] for p in posts[-limit:]]
    except Exception:
        return []


def _call_claude(system_prompt, user_content, api_key):
    response = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'claude-sonnet-5',
            'max_tokens': 1200,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_content}]
        },
        timeout=60
    )
    result = response.json()
    if 'content' not in result:
        raise Exception(f'Claude-API-Fehler: {result}')
    for block in result['content']:
        if block.get('type') == 'text':
            return block['text'].strip()
    raise Exception(f'Claude-API-Antwort ohne Text-Block: {result}')


def _recent_feedback_block(limit=6):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT text, rating, feedback_tags, feedback_comment FROM linkedin_drafts "
        "WHERE rating IS NOT NULL AND rating != '' ORDER BY decided_at DESC LIMIT ?", (limit,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return ''
    lines = []
    for text, rating, tags_json, comment in rows:
        try:
            tags = json.loads(tags_json) if tags_json else []
        except Exception:
            tags = []
        detail = ', '.join(tags) if tags else ''
        if comment:
            detail = f'{detail} - Kommentar: {comment}' if detail else f'Kommentar: {comment}'
        snippet = (text or '')[:120].replace('\n', ' ')
        lines.append(f"- Post \"{snippet}...\" wurde als '{rating}' bewertet. {detail}".strip())
    return (
        "\n\nWICHTIG - FEEDBACK VON STEFAN ZU FRUEHEREN POSTS (unbedingt beruecksichtigen "
        "und diese Kritikpunkte bei diesem neuen Post vermeiden):\n" + '\n'.join(lines)
    )


def _build_system_prompt(examples, format_style):
    examples_block = '\n\n---\n\n'.join(examples) if examples else '(noch keine Beispiel-Posts vorhanden)'
    feedback_block = _recent_feedback_block()
    return f"""{siggi_time.now_line()} (Jahreszeit, Feiertage und "letzte Woche" daran ausrichten.)

Du schreibst LinkedIn-Posts fuer Stefan Mutter, Inhaber der Agentur ChefBlick
(Website-Erstellung, E-Commerce-Beratung, Digitalisierung fuer Unternehmer).

THEMENFOKUS:
{TOPIC_FOCUS}

GRUNDTON (aus Stefans bisherigen Posts abgeleitet, halte dich daran):
- Direkter, persoenlicher Ton, oft mit Bezug zu echten Alltags-Erlebnissen/Kundengespraechen
- Ehrlich, kein Verkaufs-Sprech, auch mal selbstkritisch oder nachdenklich
- 3-5 passende Hashtags am Ende
- Laenge: 100-250 Woerter

WICHTIG - FORMAT FUER DIESEN POST (dieses Mal GENAU dieses Format nutzen, nicht das
uebliche Hook+Bulletpoints-Schema, damit die Posts insgesamt abwechslungsreich bleiben):
{format_style['label']}: {format_style['desc']}

BEISPIELE VON STEFANS BISHERIGEN POSTS (Referenz fuer Ton, NICHT das Format/die Struktur kopieren):
{examples_block}
{feedback_block}

AUFGABE: Schreibe GENAU EINEN neuen, eigenstaendigen LinkedIn-Post zu einem
aktuell relevanten Thema aus dem Themenfokus, in dem oben vorgegebenen Format.
Kein Thema doppeln, das in den Beispielen schon vorkommt. Gib NUR den fertigen
Post-Text zurueck, keine Erklaerung, keine Anfuehrungszeichen drumherum."""


def generate_draft():
    settings = load_settings()
    # env-Key (funktionierendes Hauptkonto) hat Vorrang vor settings.json,
    # falls dort ein separater/veralteter Key hinterlegt ist
    api_key = os.environ.get('ANTHROPIC_API_KEY') or settings.get('anthropic_api_key', '')
    if not api_key or api_key == 'HIER_API_KEY_EINTRAGEN':
        return None

    examples = _load_example_posts()
    format_style = _pick_format_style()
    system_prompt = _build_system_prompt(examples, format_style)
    text = _call_claude(system_prompt, 'Schreibe jetzt den Post.', api_key)
    text = re.sub(r'^["\']|["\']$', '', text.strip())

    approved_count = settings.get('linkedin_approved_count', 0)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if approved_count >= AUTO_POST_THRESHOLD:
        # Auto-Modus: ohne Pruefung direkt in die Warteschlange (gepostet wird nach Zeitplan)
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, format_style, status, decided_at) VALUES (?, ?, ?, 'approved', CURRENT_TIMESTAMP)",
            ('auto', text, format_style['key'])
        )
    else:
        c.execute(
            "INSERT INTO linkedin_drafts (topic, text, format_style, status) VALUES (?, ?, ?, 'pending')",
            ('manual-review', text, format_style['key'])
        )

    conn.commit()
    draft_id = c.lastrowid
    conn.close()
    return draft_id


def get_drafts(status=None, limit=30):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if status:
        c.execute("SELECT id, topic, text, format_style, status, created_at FROM linkedin_drafts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        c.execute("SELECT id, topic, text, format_style, status, created_at FROM linkedin_drafts ORDER BY created_at DESC LIMIT ?", (limit,))
    cols = ['id', 'topic', 'text', 'format_style', 'status', 'created_at']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows


def _store_feedback(c, draft_id, rating, tags, comment):
    if rating or tags or comment:
        c.execute(
            "UPDATE linkedin_drafts SET rating=?, feedback_tags=?, feedback_comment=? WHERE id=?",
            (rating, json.dumps(tags or [], ensure_ascii=False), comment or '', draft_id)
        )


def approve_draft(draft_id, rating=None, tags=None, comment=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT text FROM linkedin_drafts WHERE id=? AND status='pending'", (draft_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {'error': 'Entwurf nicht gefunden oder bereits entschieden.'}

    # Nicht sofort posten - der Beitrag kommt in die Warteschlange und geht nach Zeitplan raus
    c.execute("UPDATE linkedin_drafts SET status='approved', decided_at=CURRENT_TIMESTAMP, post_error=NULL WHERE id=?", (draft_id,))
    _store_feedback(c, draft_id, rating, tags, comment)
    conn.commit()
    conn.close()

    settings_store.update(lambda st: st.__setitem__('linkedin_approved_count', st.get('linkedin_approved_count', 0) + 1))
    n = len(get_queue())
    return {'success': True, 'queued': True, 'queue_length': n,
            'message': f'In die Warteschlange gelegt ({n} freigegeben)'}


def reject_draft(draft_id, rating=None, tags=None, comment=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE linkedin_drafts SET status='rejected', decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (draft_id,)
    )
    affected = c.rowcount
    _store_feedback(c, draft_id, rating, tags, comment)
    conn.commit()
    conn.close()
    return {'success': affected > 0}


def get_feedback_stats(limit=40):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rating, COUNT(*) FROM linkedin_drafts WHERE rating IS NOT NULL AND rating != '' GROUP BY rating")
    counts = {r: n for r, n in c.fetchall()}
    c.execute(
        "SELECT topic, format_style, rating, feedback_tags, feedback_comment, decided_at FROM linkedin_drafts "
        "WHERE rating IS NOT NULL AND rating != '' ORDER BY decided_at DESC LIMIT ?", (limit,)
    )
    recent = []
    for topic, format_style, rating, tags_json, comment, decided_at in c.fetchall():
        try:
            tags = json.loads(tags_json) if tags_json else []
        except Exception:
            tags = []
        recent.append({
            'topic': topic, 'format_style': format_style, 'rating': rating,
            'tags': tags, 'comment': comment, 'decided_at': decided_at
        })
    conn.close()
    return {'counts': counts, 'recent': recent}


def get_progress():
    settings = load_settings()
    approved = settings.get('linkedin_approved_count', 0)
    return {
        'approved_count': approved,
        'threshold': AUTO_POST_THRESHOLD,
        'auto_mode': approved >= AUTO_POST_THRESHOLD,
        'queue_length': len(get_queue()),
        'queue_warn_below': QUEUE_WARN_BELOW,
        'post_settings': get_post_settings(),
    }


# ─── Warteschlange & Zeitplan (wie bei Instagram) ────────────────────

def get_queue():
    """Freigegebene, noch nicht gepostete Beitraege - der aelteste zuerst."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, topic, text, format_style, status, created_at, decided_at, post_error FROM linkedin_drafts "
              "WHERE status='approved' ORDER BY decided_at ASC, id ASC")
    cols = ['id', 'topic', 'text', 'format_style', 'status', 'created_at', 'decided_at', 'post_error']
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    conn.close()
    return rows


def unqueue_draft(draft_id):
    """Aus der Warteschlange zurueck zu den offenen Entwuerfen."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE linkedin_drafts SET status='pending', decided_at=NULL WHERE id=? AND status='approved'", (draft_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return {'success': ok}


def get_post_settings():
    s = dict(DEFAULT_POST_SETTINGS)
    s.update({k: v for k, v in (load_settings().get('linkedin_post_settings') or {}).items() if k in DEFAULT_POST_SETTINGS or k == 'auto_post_last_slot'})
    return s


def save_post_settings(data):
    days = [d for d in str(data.get('post_days', '')).split(',') if d in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')]
    times = [t.strip() for t in str(data.get('post_times', '')).split(',') if re.match(r'^\d{1,2}:\d{2}$', t.strip())]
    def mut(st):
        ps = st.setdefault('linkedin_post_settings', {})
        ps['auto_enabled'] = '1' if str(data.get('auto_enabled')) in ('1', 'true', 'True') else '0'
        ps['post_times'] = ','.join(times) or DEFAULT_POST_SETTINGS['post_times']
        ps['post_days'] = ','.join(days) or DEFAULT_POST_SETTINGS['post_days']
    settings_store.update(mut)
    return get_post_settings()


def _queue_problem(n):
    if n >= QUEUE_WARN_BELOW:
        return []
    return [f'LinkedIn-Warteschlange: nur noch {n} freigegebene(r) Beitrag/Beiträge – Siggi hat Entwürfe geschrieben, '
            f'bitte freigeben (Telegram-Knopf oder Dashboard), sonst wird bald nichts mehr gepostet']


def post_next_in_queue():
    """Postet den aeltesten freigegebenen Beitrag. Schlaegt das fehl (z.B. Token abgelaufen),
    bleibt er in der Warteschlange - nichts geht verloren."""
    import agents_engine as agents
    queue = get_queue()
    if not queue:
        agents.done('linkedin', 'Nichts gepostet – Warteschlange ist leer', problems=_queue_problem(0), notify=True,
                    bubble='Warteschlange leer – bitte Entwürfe freigeben')
        return {'success': False, 'error': 'Keine freigegebenen Beiträge in der Warteschlange.'}
    item = queue[0]
    plan = lambda *st: [{'label': l, 'state': x} for l, x in zip(
        ['Ältesten freigegebenen Beitrag nehmen', 'Auf LinkedIn veröffentlichen', 'Warteschlange prüfen'], st)]
    agents.start('linkedin', 'Veröffentliche den nächsten Beitrag auf LinkedIn …', plan=plan('done', 'active', 'open'))
    result = linkedin_engine.post_share(item['text'])
    ok = 'veröffentlicht' in result
    conn = sqlite3.connect(DB_PATH)
    if ok:
        conn.execute("UPDATE linkedin_drafts SET status='approved_posted', posted_at=CURRENT_TIMESTAMP, post_error=NULL WHERE id=?", (item['id'],))
    else:
        conn.execute("UPDATE linkedin_drafts SET post_error=? WHERE id=?", (str(result)[:500], item['id']))
    conn.commit()
    conn.close()
    if not ok:
        agents.fail('linkedin', f'LinkedIn-Post fehlgeschlagen (bleibt in der Warteschlange): {str(result)[:200]}',
                    plan=plan('done', 'error', 'open'))
        return {'success': False, 'error': result}
    left = len(get_queue())
    agents.done('linkedin', f'Beitrag gepostet – noch {left} in der Warteschlange',
                plan=plan('done', 'done', 'warn' if left < QUEUE_WARN_BELOW else 'done'))
    if left + len(get_drafts('pending')) < QUEUE_WARN_BELOW:
        try:
            run_as_agent(max_new=1)  # Nachschub schreiben - kommt per Telegram zur Freigabe
        except Exception as e:
            print(f'[LinkedIn] Nachschub fehlgeschlagen: {e}')
    elif left < QUEUE_WARN_BELOW:
        agents.notify_stefan('📝 ' + _queue_problem(left)[0], mail_subject='LinkedIn: bitte Entwürfe freigeben')
    return {'success': True, 'message': result, 'queue_length': left}


def maybe_auto_post():
    """Jede Minute aufgerufen (Loop in app.py): postet zur eingestellten Zeit, einmal pro Slot."""
    import agents_engine as agents
    ps = get_post_settings()
    if ps.get('auto_enabled') != '1' or agents.is_paused('linkedin'):
        return
    now = datetime.datetime.now()
    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    if day_map[now.weekday()] not in ps['post_days'].split(','):
        return
    current_hm = now.strftime('%H:%M')
    if current_hm not in [t.strip().zfill(5) for t in ps['post_times'].split(',')]:
        return
    slot_key = f'{now.strftime("%Y-%m-%d")}_{current_hm}'
    if ps.get('auto_post_last_slot') == slot_key:
        return
    settings_store.update(lambda st: st.setdefault('linkedin_post_settings', {}).__setitem__('auto_post_last_slot', slot_key))
    post_next_in_queue()


def run_as_agent(max_new=MAX_NEW_PER_RUN):
    """Taeglicher Lauf (Cron 08:00), Nachschub nach einem Post, oder 'Jetzt starten'.
    Stefan schreibt die Posts nicht selbst - Siggi haelt deshalb Vorrat: es werden so viele Entwuerfe
    geschrieben, bis freigegebene + zur Freigabe wartende STOCK_TARGET erreichen (max. max_new).
    Jeder Entwurf kommt per Telegram mit Freigabe-Knopf. Liefert die Liste der neuen Entwurfs-IDs."""
    import agents_engine as agents
    init_table()
    queued, pending = len(get_queue()), len(get_drafts('pending'))
    to_write = min(max_new, max(0, STOCK_TARGET - queued - pending))
    plan = lambda *st: [{'label': l, 'state': x} for l, x in zip(
        ['Vorrat prüfen', f'{to_write} Entwurf/Entwürfe in deinem Stil schreiben', 'Per Telegram zur Freigabe schicken'], st)]
    if not to_write:
        agents.done('linkedin', f'Genug Vorrat ({queued} freigegeben, {pending} warten auf Freigabe) – kein neuer Entwurf nötig',
                    plan=plan('done', 'done', 'done'), quiet=True)
        return []
    if not agents.start('linkedin', f'Schreibe {to_write} LinkedIn-Entwurf/Entwürfe in deinem Stil …', plan=plan('done', 'active', 'open')):
        return []
    new_ids = []
    try:
        for i in range(to_write):
            agents.step('linkedin', f'Schreibe Entwurf {i + 1} von {to_write} …', progress=i / to_write)
            new_id = generate_draft()
            if not new_id:
                break
            new_ids.append(new_id)
            if get_progress().get('approved_count', 0) < AUTO_POST_THRESHOLD:
                draft = next((d for d in get_drafts('pending') if d['id'] == new_id), None)
                agents.notify_stefan(f"📝 Neuer LinkedIn-Entwurf ({len(get_queue())} freigegeben in der Warteschlange):\n\n"
                                     f"{(draft or {}).get('text', '')[:3300]}\n\n(Freigeben = wird nach Zeitplan gepostet.)",
                                     mail_subject='Freigabe nötig: LinkedIn-Entwurf', approve=('linkedin', new_id))
    except Exception as e:
        agents.fail('linkedin', e, plan=plan('done', 'error', 'open'))
        raise
    problems = _queue_problem(len(get_queue()))
    agents.done('linkedin', f'{len(new_ids)} neue(r) Entwurf/Entwürfe zur Freigabe geschickt', plan=plan('done', 'done', 'done'),
                problems=problems, notify=False)  # die Entwuerfe selbst kamen schon per Telegram
    return new_ids


if __name__ == '__main__':
    new_ids = run_as_agent()
    print(f'Neue Entwuerfe: {new_ids}')
