"""Sprache -> Text fuer Telegram-Sprachnachrichten (lokal mit faster-whisper, keine API-Kosten,
die Aufnahme verlaesst den Server nicht).

Laeuft bewusst als eigener Prozess (telegram_engine ruft es per subprocess auf): das Modell
braucht ~500 MB RAM und ist danach sofort wieder freigegeben, statt dauerhaft im Web-Prozess
zu liegen.

Aufruf: python stt_transcribe.py <audiodatei>  -> gibt den erkannten Text auf stdout aus
"""
import os
import sys

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.whisper_models')
# Der App-User 'stean' hat kein beschreibbares Home - Download-Cache daher in den App-Ordner
os.environ.setdefault('HF_HOME', os.path.join(MODEL_DIR, 'hf'))
os.environ.setdefault('XDG_CACHE_HOME', os.path.join(MODEL_DIR, 'cache'))
MODEL_SIZE = os.environ.get('SIGGI_STT_MODEL', 'small')


def _load_audio(path):
    """Audio per ffmpeg zu 16-kHz-Mono dekodieren. Nicht ueber PyAV (faster-whispers Standard):
    die venv sieht wegen --system-site-packages eine zu alte System-Version davon."""
    import subprocess
    import numpy as np
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = 'ffmpeg'
    raw = subprocess.run([ffmpeg, '-nostdin', '-i', path, '-f', 's16le', '-ac', '1', '-ar', '16000', '-'],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL_SIZE, device='cpu', compute_type='int8', download_root=MODEL_DIR, cpu_threads=2)
    # initial_prompt: Eigennamen, die sonst falsch erkannt werden ("Siggi" -> "Siege")
    segments, _ = model.transcribe(_load_audio(path), language='de', beam_size=1, vad_filter=True,
                                   initial_prompt='Siggi, Stefan, Chefblick, Fischmann, LinkedIn, Instagram')
    return ' '.join(s.text.strip() for s in segments).strip()


if __name__ == '__main__':
    print(transcribe(sys.argv[1]))
