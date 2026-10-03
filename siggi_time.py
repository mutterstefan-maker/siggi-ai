"""Aktuelles Datum/Uhrzeit als Satz fuer KI-Prompts.

Claude kennt das heutige Datum nicht von selbst und raet sonst (z.B. falscher Wochentag bei
"Passt Ihnen naechsten Dienstag?"). Jeder Prompt, der Texte fuer Stefan oder Kunden schreibt,
bekommt deshalb diese Zeile. Der Server laeuft in Europe/Berlin.
"""
from datetime import datetime

_WEEKDAYS = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']


def now_line():
    now = datetime.now()
    return f"Heute ist {_WEEKDAYS[now.weekday()]}, der {now.strftime('%d.%m.%Y')}, es ist {now.strftime('%H:%M')} Uhr (deutsche Zeit)."
