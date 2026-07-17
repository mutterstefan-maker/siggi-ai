# -*- coding: utf-8 -*-
"""Tray-Icon für den Siggi Desktop-Agent (Status + Beenden)."""
import threading
import time

from PIL import Image, ImageDraw
import pystray


def _make_icon(color):
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=color)
    return img


ICON_CONNECTED = _make_icon((0, 200, 90, 255))
ICON_DISCONNECTED = _make_icon((200, 60, 60, 255))


def run(status, sio, start_fn):
    start_fn()

    def on_quit(icon, item):
        icon.stop()
        try:
            sio.disconnect()
        except Exception:
            pass

    icon = pystray.Icon(
        'siggi-desktop-agent',
        ICON_DISCONNECTED,
        'Siggi Desktop-Agent (verbindet...)',
        menu=pystray.Menu(pystray.MenuItem('Beenden', on_quit))
    )

    def watch_status():
        while True:
            if status['connected']:
                icon.icon = ICON_CONNECTED
                icon.title = 'Siggi Desktop-Agent (verbunden)'
            else:
                icon.icon = ICON_DISCONNECTED
                icon.title = 'Siggi Desktop-Agent (getrennt)'
            time.sleep(3)

    threading.Thread(target=watch_status, daemon=True).start()
    icon.run()
