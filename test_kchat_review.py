"""Freigabe-Schranken testen, ohne Telegram oder kChat zu kontaktieren."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kchat_engine as k


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in [('DB_PATH', str(Path(self.tmp.name) / 'test.db')),
                            ('_channels', lambda: []), ('_telegram', lambda *a, **kw: True)]:
            p = patch.object(k, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.event = patch.object(k.agents_engine, 'event').start()
        self.addCleanup(patch.stopall)
        patch('telegram_engine.approval_buttons', return_value=[]).start()
        self.send = patch.object(k, '_senden_freigegeben', return_value='sent-id').start()

    def draft(self, ms=100):
        return k.antwort_entwurf('Michael', 'Kein Thema 😋',
                                 channel={'id': 'dm', 'name': 'Michael'}, source_ms=ms)['entwurf']

    def test_no_send_without_approval_and_only_once(self):
        draft = self.draft()
        k.sende_faellige_entwuerfe()
        self.send.assert_not_called()
        with patch.object(k, '_api', return_value={}), patch.object(k, '_posts', return_value=[{'ms': 100}]):
            k.entscheiden(draft, True)
            k.entscheiden(draft, True)
        self.send.assert_called_once()

    def test_reject_and_stale_never_send(self):
        k.entscheiden(self.draft(), False)
        draft = self.draft()
        with patch.object(k, '_api', return_value={}), patch.object(k, '_posts', return_value=[{'ms': 101}]):
            self.assertIn('verworfen', k.entscheiden(draft, True))
        self.send.assert_not_called()

    def test_new_proposal_invalidates_old_and_timer_rejected(self):
        old = self.draft()
        self.draft(101)
        self.assertIn('schon entschieden', k.entscheiden(old, True))
        with self.assertRaises(ValueError):
            k.antwort_entwurf('Michael', 'test', senden_in_min=5)
        self.send.assert_not_called()

    def test_already_answered_skips_generation(self):
        with patch.object(k, '_api', return_value={}), patch.object(k, '_posts', return_value=[{'user_id': 'stefan'}]), patch.object(k.requests, 'post') as ai:
            c = k._db()
            try:
                self.assertFalse(k._dm_entwurf({'kanal_id': 'dm'}, c))
            finally:
                c.close()
            ai.assert_not_called()


if __name__ == '__main__':
    unittest.main()
