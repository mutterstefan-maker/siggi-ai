"""Zentrale, sichere Verwaltung von settings.json.

Hintergrund: frueher hatte jedes Modul eigene load/save-Funktionen, die mit
open(..., 'w') direkt in settings.json geschrieben haben. Das leert die Datei
kurz, bevor der neue Inhalt drinsteht. Hat ein anderer Thread/Prozess (z.B. der
Instagram-Autoposter oder stean-mail-loop) genau in dem Moment gelesen, bekam
er {} zurueck und hat danach nur seinen eigenen Teil zurueckgeschrieben - alle
anderen Einstellungen (E-Mail-Konten, API-Keys, Instagram-Token) waren weg.

Dieses Modul verhindert das:
- Schreiben atomar (Temp-Datei + os.replace), Leser sehen nie eine halbe Datei
- Dateisperre (flock) ueber Prozessgrenzen hinweg
- Leseversuche werden bei kaputtem JSON wiederholt statt still {} zu liefern
- Wachhund: ein Speichern, das geschuetzte oder mehrere Top-Level-Eintraege
  verlieren wuerde, wird verweigert und geloggt
- taegliches Backup nach settings_backups/
"""
import contextlib
import datetime
import json
import os
import shutil
import tempfile
import time

try:
    import fcntl
except ImportError:  # Windows (lokale Entwicklung) - kein flock
    fcntl = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
LOCK_PATH = SETTINGS_PATH + '.lock'
BACKUP_DIR = os.path.join(BASE_DIR, 'settings_backups')
BACKUP_KEEP_DAYS = 30

# Diese Eintraege duerfen nie durch ein Speichern verschwinden
PROTECTED_KEYS = ('accounts', 'instagram_settings', 'anthropic_api_key')
# So viele sonstige Top-Level-Eintraege darf ein Speichern hoechstens entfernen
# (desktop_engine entfernt z.B. legitim 'desktop_agent_token_hash')
MAX_REMOVED_KEYS = 1


class SettingsCorruptError(Exception):
    pass


@contextlib.contextmanager
def _locked(exclusive):
    if fcntl is None:
        yield
        return
    with open(LOCK_PATH, 'a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_file():
    with open(SETTINGS_PATH, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('settings.json enthaelt kein JSON-Objekt')
    return data


def load():
    """Liest settings.json. Fehlt die Datei, kommt {} zurueck. Ist sie kaputt,
    wird mehrfach neu gelesen und notfalls SettingsCorruptError geworfen -
    niemals still {} (das war die Ursache fuer das Loeschen aller Einstellungen)."""
    last_error = None
    for attempt in range(5):
        try:
            with _locked(exclusive=False):
                return _read_file()
        except FileNotFoundError:
            return {}
        except (ValueError, UnicodeDecodeError) as e:
            last_error = e
            time.sleep(0.2 * (attempt + 1))
    print(f'[settings] settings.json nicht lesbar: {last_error}')
    raise SettingsCorruptError(str(last_error))


def load_or_empty():
    """Fuer reine Lesezugriffe, die bei kaputter Datei mit {} weiterlaufen
    sollen. Ein spaeteres save() mit so einem {} wird vom Wachhund blockiert."""
    try:
        return load()
    except SettingsCorruptError:
        return {}


def _daily_backup():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        today = datetime.date.today().isoformat()
        target = os.path.join(BACKUP_DIR, f'settings-{today}.json')
        if not os.path.exists(target) and os.path.exists(SETTINGS_PATH):
            shutil.copy2(SETTINGS_PATH, target)
            os.chmod(target, 0o600)
        cutoff = time.time() - BACKUP_KEEP_DAYS * 86400
        for name in os.listdir(BACKUP_DIR):
            path = os.path.join(BACKUP_DIR, name)
            if name.startswith('settings-') and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except Exception as e:
        print(f'[settings] Backup fehlgeschlagen: {e}')


def save(settings):
    """Schreibt settings.json atomar. Gibt False zurueck (und schreibt nichts),
    wenn dabei geschuetzte oder zu viele Eintraege verloren gingen."""
    if not isinstance(settings, dict):
        raise TypeError('settings muss ein dict sein')
    with _locked(exclusive=True):
        try:
            current = _read_file()
        except FileNotFoundError:
            current = None
        except (ValueError, UnicodeDecodeError):
            current = None
            ts = int(time.time())
            try:
                shutil.copy2(SETTINGS_PATH, f'{SETTINGS_PATH}.corrupt-backup-{ts}')
            except Exception:
                pass

        if current:
            removed = [k for k in current if k not in settings]
            removed_protected = [k for k in removed if k in PROTECTED_KEYS]
            if removed_protected or len(removed) > MAX_REMOVED_KEYS:
                print(f'[settings] SPEICHERN VERWEIGERT - wuerde Eintraege loeschen: {removed}')
                return False
            _daily_backup()

        fd, tmp_path = tempfile.mkstemp(prefix='.settings-', suffix='.tmp', dir=BASE_DIR)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(SETTINGS_PATH):
                st = os.stat(SETTINGS_PATH)
                shutil.copymode(SETTINGS_PATH, tmp_path)
                if hasattr(os, 'chown') and os.geteuid() == 0:
                    os.chown(tmp_path, st.st_uid, st.st_gid)  # z.B. bei manuellem Aufruf als root
            os.replace(tmp_path, SETTINGS_PATH)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.remove(tmp_path)
            raise
    return True


def update(mutator):
    """Frisch lesen, aendern, sofort schreiben - haelt das Fenster, in dem eine
    parallele Aenderung verloren gehen kann, so klein wie moeglich.
    mutator(settings) aendert das dict in-place."""
    settings = load()
    mutator(settings)
    return save(settings)
