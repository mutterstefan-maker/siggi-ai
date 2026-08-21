"""Zugriff auf den gespiegelten D:\\COWORK-Ordner (liegt auf dem Server unter
/opt/stean/cowork/, wird per Windows-Taskplaner vom PC aus synchronisiert -
siehe D:\\COWORK\\sync_to_siggi.ps1). Erlaubt Siggi, Dateien zu suchen und
anzuzeigen, auch wenn der PC von Stefan gerade aus ist.
"""
import os

COWORK_DIR = '/opt/stean/cowork'

TEXT_EXTENSIONS = {'.md', '.txt', '.py', '.json', '.csv', '.log', '.yml', '.yaml'}


def _safe_join(rel_path):
    """Verhindert Path-Traversal - gibt None zurück, wenn der Pfad den Cowork-Ordner verlässt."""
    full = os.path.normpath(os.path.join(COWORK_DIR, rel_path))
    if not full.startswith(os.path.normpath(COWORK_DIR)):
        return None
    return full


def search_files(query, limit=15):
    """Durchsucht Dateinamen (nicht Inhalte) rekursiv nach dem Suchbegriff."""
    if not os.path.isdir(COWORK_DIR):
        return []
    query_lower = query.lower()
    results = []
    for root, dirs, files in os.walk(COWORK_DIR):
        for fname in files:
            if query_lower in fname.lower():
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, COWORK_DIR)
                try:
                    stat = os.stat(full_path)
                    results.append({
                        'name': fname,
                        'path': rel_path.replace('\\', '/'),
                        'size_kb': round(stat.st_size / 1024, 1),
                    })
                except OSError:
                    continue
                if len(results) >= limit:
                    return results
    return results


def read_file_text(rel_path, max_chars=4000):
    """Liest Text-Inhalt einer Datei (nur bekannte Text-Formate). Gibt (ok, text_or_error) zurück."""
    full = _safe_join(rel_path)
    if not full or not os.path.isfile(full):
        return False, 'Datei nicht gefunden.'

    ext = os.path.splitext(full)[1].lower()

    if ext == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(full)
            text = '\n'.join((page.extract_text() or '') for page in reader.pages[:15])
            if not text.strip():
                return False, 'PDF enthält keinen extrahierbaren Text (evtl. gescannt/Bild-PDF).'
            return True, text[:max_chars]
        except ImportError:
            return False, 'PDF-Textextraktion ist auf dem Server nicht installiert (pypdf fehlt).'
        except Exception as e:
            return False, f'PDF konnte nicht gelesen werden: {e}'

    if ext in TEXT_EXTENSIONS:
        try:
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                return True, f.read(max_chars)
        except Exception as e:
            return False, f'Datei konnte nicht gelesen werden: {e}'

    return False, f'Dateityp {ext} kann nicht als Text angezeigt werden - nutze den Download-Link.'


def get_download_path(rel_path):
    """Gibt den validierten absoluten Pfad zurück oder None, falls unsicher/nicht vorhanden."""
    full = _safe_join(rel_path)
    if not full or not os.path.isfile(full):
        return None
    return full
