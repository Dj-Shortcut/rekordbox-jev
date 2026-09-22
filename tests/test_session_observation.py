import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dj_session


class SameFrameObservation(unittest.TestCase):
    def check_observation(self, inline):
        mixer = {'red_bar_aligned': True}
        state = {'layoutCalibrated': True, 'folder': '26',
                 'decks': [{'library': {'file': 'A'}}, {'library': {'file': 'B'}}]}
        if inline:
            state['mixer'] = mixer
        with patch.object(dj_session.controller, 'checked', return_value={}), \
             patch.object(dj_session.controller, 'normalized_state', return_value=state), \
             patch.object(dj_session.music_context, 'library', return_value=[]), \
             patch.object(dj_session.music_context, 'read_frame', return_value=mixer) as disk, \
             patch.object(dj_session.music_context, 'enrich', return_value=state) as enrich:
            self.assertIs(dj_session.get_observation(), state)
            self.assertIs(enrich.call_args.args[2], mixer)
            self.assertEqual(disk.call_count, 0 if inline else 1)

    def test_protocol_three_does_not_replace_current_mixer_with_disk_frame(self):
        self.check_observation(True)

    def test_older_bridge_uses_existing_disk_fallback(self):
        self.check_observation(False)


if __name__ == '__main__':
    unittest.main()
