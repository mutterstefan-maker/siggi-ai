# -*- coding: utf-8 -*-
"""
Richtet den Siggi Desktop-Agent im Windows-Autostart ein (kein Admin/Dienst nötig).
Legt eine .vbs-Verknüpfung im Autostart-Ordner an, die agent.py minimiert (ohne
sichtbares Konsolenfenster) über pythonw.exe startet.

Aufruf: python install.py          -> einrichten
        python install.py --remove -> Autostart-Eintrag entfernen
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_PATH = os.path.join(BASE_DIR, 'agent.py')
STARTUP_DIR = os.path.join(
    os.environ['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup'
)
LAUNCHER_PATH = os.path.join(STARTUP_DIR, 'siggi_desktop_agent.vbs')


def pythonw_path():
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return pythonw if os.path.exists(pythonw) else exe


def install():
    pythonw = pythonw_path()
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run """{pythonw}"" ""{AGENT_PATH}""", 0, False\n'
    )
    os.makedirs(STARTUP_DIR, exist_ok=True)
    with open(LAUNCHER_PATH, 'w', encoding='utf-8') as f:
        f.write(vbs_content)
    print(f'Autostart eingerichtet: {LAUNCHER_PATH}')
    print('Der Agent startet automatisch beim nächsten Login. Jetzt manuell starten mit:')
    print(f'  "{pythonw}" "{AGENT_PATH}"')


def remove():
    if os.path.exists(LAUNCHER_PATH):
        os.remove(LAUNCHER_PATH)
        print('Autostart-Eintrag entfernt.')
    else:
        print('Kein Autostart-Eintrag gefunden.')


if __name__ == '__main__':
    if '--remove' in sys.argv:
        remove()
    else:
        install()
