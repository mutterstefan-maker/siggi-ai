# -*- coding: utf-8 -*-
"""
SIGGI Memory & Reminder Engine
Dauerhaftes Gedächtnis und Erinnerungsfunktion für SIGGI
"""
import sqlite3
import os
import re
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'maildesk.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db():
    """Erstellt die Gedächtnis- und Erinnerungs-Tabellen."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS siggi_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS siggi_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# ── GEDÄCHTNIS ──────────────────────────────────────────────────────────────

def save_memory(content, category='general'):
    """Speichert einen Gedächtnis-Eintrag."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO siggi_memory (content, category, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (content.strip(), category, now, now)
    )
    conn.commit()
    conn.close()


def get_all_memories():
    """Gibt alle gespeicherten Erinnerungen zurück."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, content, category, created_at FROM siggi_memory ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_memory(memory_id):
    conn = get_db()
    conn.execute("DELETE FROM siggi_memory WHERE id=?", (memory_id,))
    conn.commit()
    conn.close()


def get_memory_context():
    """Gibt alle Gedächtnis-Einträge als formatierten Text für den System-Prompt zurück."""
    memories = get_all_memories()
    if not memories:
        return ''
    lines = [f"- {m['content']} (gespeichert am {m['created_at'][:10]})" for m in memories]
    return "DEIN GEDÄCHTNIS (was Mutti dir gesagt hat):\n" + "\n".join(lines)


def detect_and_save_memory(text):
    """
    Erkennt ob Mutti SIGGI etwas merken lassen will.
    Gibt den gespeicherten Inhalt zurück oder None.
    """
    patterns = [
        r'merk dir[,:]?\s*(.+)',
        r'merke dir[,:]?\s*(.+)',
        r'vergiss nicht[,:]?\s*(.+)',
        r'behalte im kopf[,:]?\s*(.+)',
        r'wichtig[,:]?\s*(.+)',
        r'notier[e]? dir[,:]?\s*(.+)',
        r'ich möchte dass du dir merkst[,:]?\s*(.+)',
        r'bitte merk dir[,:]?\s*(.+)',
        # Projekte & Aufgaben
        r'auf meinem schirm[,:]?\s*(.+)',
        r'auf dem schirm[,:]?\s*(.+)',
        r'(.+?)\s+(?:steht|ist) auf (?:meinem|dem) schirm',
        r'projekt[,:]?\s*(.+)',
        r'ich arbeite (?:gerade |noch )?an[,:]?\s*(.+)',
        r'ich muss noch[,:]?\s*(.+)',
        r'muss noch[,:]?\s*(.+)',
        r'todo[,:]?\s*(.+)',
        r'aufgabe[,:]?\s*(.+)',
        r'diese woche (?:mache ich|muss ich)[,:]?\s*(.+)',
        r'die nächsten tage (?:mache ich|muss ich)[,:]?\s*(.+)',
        r'hab[e]? noch[,:]?\s*(.+?)\s+(?:zu erledigen|offen|anstehen)',
    ]
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            content = match.group(1).strip().rstrip('.')
            if len(content) > 3:
                save_memory(content)
                return content
    return None


# ── ERINNERUNGEN ────────────────────────────────────────────────────────────

def save_reminder(message, remind_at):
    """Speichert eine Erinnerung."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO siggi_reminders (message, remind_at, done, created_at) VALUES (?, ?, 0, ?)",
        (message.strip(), remind_at, now)
    )
    conn.commit()
    conn.close()


def get_due_reminders():
    """Gibt fällige, noch nicht erledigte Erinnerungen zurück."""
    now = datetime.now().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, message, remind_at FROM siggi_reminders WHERE done=0 AND remind_at <= ?",
        (now,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_reminder_done(reminder_id):
    conn = get_db()
    conn.execute("UPDATE siggi_reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()
    conn.close()


def get_upcoming_reminders():
    """Gibt kommende Erinnerungen für den System-Prompt zurück."""
    now = datetime.now().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT message, remind_at FROM siggi_reminders WHERE done=0 AND remind_at > ? ORDER BY remind_at LIMIT 5",
        (now,)
    ).fetchall()
    conn.close()
    if not rows:
        return ''
    lines = []
    for r in rows:
        dt = datetime.fromisoformat(r['remind_at'])
        lines.append(f"- {r['message']} (um {dt.strftime('%d.%m.%Y %H:%M')} Uhr)")
    return "ANSTEHENDE ERINNERUNGEN:\n" + "\n".join(lines)


def parse_reminder_time(text):
    """
    Parst natürliche Zeitangaben auf Deutsch.
    Gibt datetime zurück oder None.
    """
    now = datetime.now()
    text = text.lower().strip()

    MONTHS = {
        'januar': 1, 'februar': 2, 'märz': 3, 'maerz': 3, 'april': 4, 'mai': 5, 'juni': 6,
        'juli': 7, 'august': 8, 'september': 9, 'oktober': 10, 'november': 11, 'dezember': 12,
    }

    # explizites Datum ausgeschrieben "8. August" / "8. August 2025" (optional mit "um HH:MM")
    m = re.search(
        r'\b(\d{1,2})\.?\s+(' + '|'.join(MONTHS.keys()) + r')(?:\s+(\d{4}))?\b', text
    )
    if m:
        day, month = int(m.group(1)), MONTHS[m.group(2)]
        year_part = m.group(3)
        year = int(year_part) if year_part else now.year
        try:
            dt = datetime(year, month, day)
        except ValueError:
            dt = None
        if dt:
            tm = re.search(r'um (\d{1,2})[:\.](\d{2})', text)
            if tm:
                dt = dt.replace(hour=int(tm.group(1)), minute=int(tm.group(2)))
            else:
                dt = dt.replace(hour=9, minute=0)
            if not year_part and dt < now.replace(hour=0, minute=0, second=0, microsecond=0):
                dt = dt.replace(year=year + 1)
            return dt

    # explizites Datum "DD.MM.YYYY" oder "DD.MM." (optional mit "um HH:MM")
    m = re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?\b', text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year_part = m.group(3)
        if year_part:
            year = int(year_part) if len(year_part) == 4 else 2000 + int(year_part)
        else:
            year = now.year
        try:
            dt = datetime(year, month, day)
        except ValueError:
            dt = None
        if dt:
            tm = re.search(r'um (\d{1,2})[:\.](\d{2})', text)
            if tm:
                dt = dt.replace(hour=int(tm.group(1)), minute=int(tm.group(2)))
            else:
                dt = dt.replace(hour=9, minute=0)
            if not year_part and dt < now.replace(hour=0, minute=0, second=0, microsecond=0):
                dt = dt.replace(year=year + 1)
            return dt

    # "in X minuten/stunden"
    m = re.search(r'in (\d+)\s*(minute[n]?|min)', text)
    if m:
        return now + timedelta(minutes=int(m.group(1)))

    m = re.search(r'in (\d+)\s*(stunde[n]?|std)', text)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    m = re.search(r'in (\d+)\s*(tag[en]?)', text)
    if m:
        return now + timedelta(days=int(m.group(1)))

    # "um HH:MM"
    m = re.search(r'um (\d{1,2})[:\.](\d{2})', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt

    # "morgen um HH:MM"
    m = re.search(r'morgen.*?um (\d{1,2})[:\.](\d{2})', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        dt = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        return dt

    # "heute abend" → 18:00
    if 'heute abend' in text or 'heut abend' in text:
        return now.replace(hour=18, minute=0, second=0, microsecond=0)

    # "morgen früh" → 08:00
    if 'morgen früh' in text or 'morgen fruh' in text:
        return (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

    return None


def detect_and_save_reminder(text):
    """
    Erkennt ob Mutti eine Erinnerung setzen will.
    Gibt (message, remind_at) zurück oder None.
    """
    patterns = [
        r'erinnere mich (.+)',
        r'erinne?r mich (.+)',
        r'vergiss nicht mich zu erinnern (.+)',
        r'setz eine? erinnerung (.+)',
        r'remind me (.+)',
    ]
    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            remainder = match.group(1).strip()
            remind_at = parse_reminder_time(remainder)
            if remind_at:
                # Nachricht extrahieren: alles vor der Zeitangabe
                msg = re.sub(
                    r'(in \d+ (minuten?|stunden?|tagen?)|um \d+[:.]\d+|morgen|heute abend|morgen früh)',
                    '', remainder
                ).strip().strip('.,').strip()
                if not msg:
                    msg = 'Erinnerung von SIGGI'
                save_reminder(msg, remind_at.isoformat())
                return msg, remind_at
    return None


# ── WINDOWS BENACHRICHTIGUNG ─────────────────────────────────────────────────

def send_windows_notification(title, message):
    """Sendet eine Windows-Toast-Benachrichtigung."""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=10, threaded=True)
        return True
    except ImportError:
        # Fallback: PowerShell
        try:
            import subprocess
            ps_cmd = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
                f'$template.SelectSingleNode("//text[@id=1]").InnerText = "{title}";'
                f'$template.SelectSingleNode("//text[@id=2]").InnerText = "{message}";'
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template);'
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("MailDeskAI").Show($toast);'
            )
            subprocess.Popen(['powershell', '-Command', ps_cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False
    except Exception:
        return False


def check_and_fire_reminders():
    """Wird vom Background-Loop aufgerufen — feuert fällige Erinnerungen."""
    due = get_due_reminders()
    for r in due:
        send_windows_notification('⏰ SIGGI Erinnerung', r['message'])
        mark_reminder_done(r['id'])
        print(f"[Memory] Erinnerung gefeuert: {r['message']}")


# ── PROAKTIVE OPTIMIERUNGS-CHECKS ──────────────────────────────────────────

_last_proactive_check = 0
_PROACTIVE_INTERVAL = 3600 * 6  # alle 6 Stunden


def run_proactive_checks():
    """
    Prüft GSC, GA4 und GMB auf Auffälligkeiten und sendet Windows-Benachrichtigungen.
    Wird vom Background-Loop aufgerufen.
    """
    global _last_proactive_check
    import time
    now = time.time()
    if now - _last_proactive_check < _PROACTIVE_INTERVAL:
        return
    _last_proactive_check = now

    alerts = []

    # ── Google Search Console ──────────────────────────────────────────────
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from gsc_engine import get_overview as gsc_overview, get_top_keywords
        gsc = gsc_overview(28)
        if gsc:
            if gsc.get('ctr', 1) < 0.3:
                alerts.append(f"🔍 SEO: CTR nur {gsc['ctr']}% bei {gsc['impressions']} Impressionen — Titles/Descriptions optimieren!")
            if gsc.get('position', 0) > 30:
                alerts.append(f"📉 SEO: Ø Position {gsc['position']} — noch nicht auf Seite 1")
            if gsc.get('clicks', 99) < 3:
                alerts.append(f"⚠️ SEO: Nur {gsc['clicks']} Klicks in 28 Tagen — dringend Content verbessern")
    except Exception as e:
        print(f'[Proaktiv] GSC-Fehler: {e}')

    # ── Google Analytics ───────────────────────────────────────────────────
    try:
        from ga4_engine import get_overview as ga4_overview
        ga4 = ga4_overview(28)
        if ga4:
            if ga4.get('bounce_rate', 0) > 80:
                alerts.append(f"📊 Analytics: {ga4['bounce_rate']}% Absprungrate — Landingpage überarbeiten")
            if ga4.get('sessions', 99) < 10:
                alerts.append(f"📊 Analytics: Nur {ga4['sessions']} Sessions in 28 Tagen — Traffic fehlt")
    except Exception as e:
        print(f'[Proaktiv] GA4-Fehler: {e}')

    # ── Google Business Profile ────────────────────────────────────────────
    try:
        from gmb_engine import is_connected, get_reviews, get_posts
        if is_connected():
            # Unbeantwortete Bewertungen
            reviews = get_reviews(10)
            unanswered = [r for r in reviews if not r.get('reply')]
            if unanswered:
                alerts.append(f"⭐ GMB: {len(unanswered)} unbeantwortete Bewertung(en) — Kunden warten!")
            # Letzter Post
            posts = get_posts(1)
            if not posts:
                alerts.append("📢 GMB: Noch kein Google-Post — sichtbarer werden mit einem Beitrag")
    except Exception as e:
        print(f'[Proaktiv] GMB-Fehler: {e}')

    # ── Benachrichtigungen senden ──────────────────────────────────────────
    if alerts:
        for alert in alerts[:3]:  # max 3 auf einmal
            send_windows_notification('💡 SIGGI Optimierungstipp', alert)
            print(f'[Proaktiv] Alert: {alert}')
    else:
        print('[Proaktiv] Alles im grünen Bereich.')
