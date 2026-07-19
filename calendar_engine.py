# -*- coding: utf-8 -*-
"""
Google Calendar Engine für SIGGI
Liest und erstellt Termine im Google Kalender von Stefan Mutter
"""
import os
import json
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'google_credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'google_token.json')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
SCOPES = ['https://www.googleapis.com/auth/calendar']

# ──────────────────────────────────────────────
# TERMINIERUNGS-REGELN
# ──────────────────────────────────────────────

DEFAULT_SCHEDULING_RULES = {
    'work_start_hour': 8,        # nie vor 8 Uhr
    'work_end_hour': 18,
    'blocked_start_hour': 11,    # 11-14 Uhr geblockt
    'blocked_end_hour': 14,
    'friday_emergency_only': True,  # freitags nur im Notfall
}


def get_scheduling_rules():
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        return {**DEFAULT_SCHEDULING_RULES, **settings.get('scheduling_rules', {})}
    except Exception:
        return dict(DEFAULT_SCHEDULING_RULES)


def get_scheduling_rules_text():
    """Für SIGGIs System-Prompt: die aktuell geltenden Terminierungs-Regeln als Text."""
    r = get_scheduling_rules()
    lines = [
        'TERMINIERUNGS-REGELN (immer einhalten wenn du Termine vorschlägst oder anlegst):',
        f"- Nie vor {r['work_start_hour']}:00 Uhr",
        f"- Keine Termine zwischen {r['blocked_start_hour']}:00 und {r['blocked_end_hour']}:00 Uhr",
    ]
    if r['friday_emergency_only']:
        lines.append('- Freitags nur im absoluten Notfall Termine vereinbaren')
    lines.append('- Diese Regeln gelten NICHT für private Termine (Parameter privat=true) - die dürfen jederzeit angelegt werden')
    return '\n'.join(lines)


def is_slot_allowed(dt, is_emergency=False, is_private=False):
    """Prüft ob ein Zeitpunkt gegen die Terminierungs-Regeln verstößt.

    Private Termine sind von den Arbeitszeit-/Blocked-Zeit-Regeln ausgenommen,
    da diese Regeln nur für geschäftliche Terminvereinbarungen gedacht sind.
    """
    r = get_scheduling_rules()
    if is_private:
        return True, ''
    if dt.hour < r['work_start_hour'] or dt.hour >= r['work_end_hour']:
        return False, f"außerhalb der Arbeitszeit ({r['work_start_hour']}-{r['work_end_hour']} Uhr)"
    if r['blocked_start_hour'] <= dt.hour < r['blocked_end_hour']:
        return False, f"in der geblockten Zeit ({r['blocked_start_hour']}-{r['blocked_end_hour']} Uhr)"
    if dt.weekday() == 4 and r['friday_emergency_only'] and not is_emergency:
        return False, 'freitags werden nur im Notfall Termine vereinbart'
    return True, ''


def get_calendar_service():
    """Gibt einen autorisierten Google Calendar Service zurück."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None

        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_PATH, 'w') as f:
                    f.write(creds.to_json())
            else:
                # Kein Token — google_auth.py einmalig manuell ausführen
                print("[Calendar] Kein Token vorhanden — bitte python google_auth.py ausführen")
                return None

        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"[Calendar] Fehler beim Verbinden: {e}")
        return None


def get_todays_events():
    """Gibt die heutigen Kalendertermine zurück."""
    try:
        service = get_calendar_service()
        if not service:
            return []

        now = datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
        end = now.replace(hour=23, minute=59, second=59).isoformat() + 'Z'

        result = service.events().list(
            calendarId='primary',
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        return result.get('items', [])
    except Exception as e:
        print(f"[Calendar] Fehler beim Abrufen: {e}")
        return []


def get_upcoming_events(days=3):
    """Gibt Termine der nächsten X Tage zurück."""
    try:
        service = get_calendar_service()
        if not service:
            return []

        now = datetime.utcnow()
        end = now + timedelta(days=days)

        result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat() + 'Z',
            timeMax=end.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime',
            maxResults=10
        ).execute()

        return result.get('items', [])
    except Exception as e:
        print(f"[Calendar] Fehler: {e}")
        return []


def find_free_slots(duration_minutes=60, days_ahead=7, max_results=5, is_emergency=False):
    """Sucht freie Zeitfenster in den nächsten X Tagen unter Beachtung der
    Terminierungs-Regeln (Arbeitszeit, geblockte Stunden, Freitags-Regel) und
    bestehender Kalendertermine. Gibt eine Liste von (start_dt, end_dt) zurück."""
    events = get_upcoming_events(days=days_ahead) or []
    busy = []
    for ev in events:
        start = ev.get('start', {})
        end = ev.get('end', {})
        if 'dateTime' not in start or 'dateTime' not in end:
            continue  # ganztägige Termine ignorieren wir hier
        busy.append((
            datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00')).replace(tzinfo=None),
            datetime.fromisoformat(end['dateTime'].replace('Z', '+00:00')).replace(tzinfo=None)
        ))

    r = get_scheduling_rules()
    slots = []
    now = datetime.now()
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    while cursor < now + timedelta(days=days_ahead) and len(slots) < max_results:
        allowed, _ = is_slot_allowed(cursor, is_emergency)
        if allowed:
            slot_end = cursor + timedelta(minutes=duration_minutes)
            overlaps = any(cursor < b_end and slot_end > b_start for b_start, b_end in busy)
            if not overlaps and slot_end.hour <= r['work_end_hour']:
                slots.append((cursor, slot_end))
        cursor += timedelta(hours=1)

    return slots


def create_event(title, start_dt, end_dt=None, description=''):
    """Erstellt einen neuen Kalendereintrag."""
    try:
        service = get_calendar_service()
        if not service:
            return None

        if end_dt is None:
            end_dt = start_dt + timedelta(hours=1)

        event = {
            'summary': title,
            'description': description,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Berlin'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Berlin'},
        }
        created = service.events().insert(calendarId='primary', body=event).execute()
        print(f"[Calendar] Termin erstellt: {title}")
        return created
    except Exception as e:
        print(f"[Calendar] Fehler beim Erstellen: {e}")
        return None


def format_event(event):
    """Formatiert einen Termin für die Anzeige."""
    title = event.get('summary', 'Kein Titel')
    start = event.get('start', {})
    if 'dateTime' in start:
        dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
        time_str = dt.strftime('%H:%M')
    else:
        time_str = 'Ganztags'
    return f"{time_str} Uhr — {title}"


def get_calendar_context():
    """Gibt Kalender-Info als Text für SIGGIs System-Prompt zurück."""
    try:
        events = get_upcoming_events(days=3)
        if not events:
            return 'GOOGLE KALENDER: Keine Termine in den nächsten 3 Tagen.'

        today = datetime.now().date()
        lines = ['GOOGLE KALENDER (nächste 3 Tage):']
        for ev in events:
            start = ev.get('start', {})
            if 'dateTime' in start:
                dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
                day = dt.date()
                if day == today:
                    label = 'Heute'
                elif day == today + timedelta(days=1):
                    label = 'Morgen'
                else:
                    label = dt.strftime('%A %d.%m.')
                lines.append(f"  {label} {dt.strftime('%H:%M')} — {ev.get('summary', 'Kein Titel')}")
            else:
                lines.append(f"  Ganztags — {ev.get('summary', 'Kein Titel')}")

        return '\n'.join(lines)
    except Exception as e:
        return f'GOOGLE KALENDER: Nicht verfügbar ({e})'
