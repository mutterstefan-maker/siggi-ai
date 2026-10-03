import os
import time
import traceback
import mail_engine as me
import agents_engine as agents

MAIL_PLAN = lambda fetch, reply: [
    {'label': 'Neue Mails abrufen und sortieren', 'state': fetch},
    {'label': 'Fällige Auto-Antworten verschicken', 'state': reply},
]


FETCH_NOW = '/opt/stean/.mail_fetch_now'  # legt Siggi an ("Abruf" / "Jetzt starten")


def wait_for_next_cycle(seconds):
    """Wartet bis zum naechsten Abruf - oder kuerzer, wenn Siggi einen Sofort-Abruf anstoesst."""
    waited = 0
    while waited < seconds:
        if os.path.exists(FETCH_NOW):
            try:
                os.remove(FETCH_NOW)
            except OSError:
                pass
            return
        time.sleep(15)
        waited += 15


def get_interval_seconds():
    try:
        s = me.load_settings()
        return max(1, int(s.get('interval_minutes', 21))) * 60
    except Exception:
        return 21 * 60


print("[mail_loop] Gestartet.")
while True:
    # Pausiert in der Agenten-Zentrale: weder abrufen noch automatisch antworten.
    if agents.is_paused('mail'):
        time.sleep(60)
        continue
    try:
        # quiet: Routine-Abrufe ohne neue Mails nicht in den Verlauf schreiben
        agents.start('mail', 'Rufe neue Mails ab …', quiet=True, plan=MAIL_PLAN('active', 'open'))
        new_count = me.fetch_mails()
        agents.step('mail', 'Verschicke fällige Antworten …', plan=MAIL_PLAN('done', 'active'))
        me.send_auto_replies()
        summary = f'{new_count} neue Mail(s) verarbeitet' if new_count else 'Keine neuen Mails'
        agents.done('mail', summary, quiet=not new_count, plan=MAIL_PLAN('done', 'done'),
                    bubble=summary + ' – nächster Abruf in ' + str(get_interval_seconds() // 60) + ' Min.')
        print(f"[mail_loop] Zyklus fertig, neue Mails: {new_count}")
    except Exception as e:
        print(f"[mail_loop] Fehler: {e}")
        traceback.print_exc()
        agents.fail('mail', e)
    wait_for_next_cycle(get_interval_seconds())
