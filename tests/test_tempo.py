"""Regression checks for uncertain/stale tempo feedback; never control Rekordbox."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import controller


def state(a=124.0, b=125.0):
    return {'layoutCalibrated': True, 'decks': [
        {'deck': 1, 'title': 'A', 'bpm': a},
        {'deck': 2, 'title': 'B', 'bpm': b}]}


class TempoFeedback(unittest.TestCase):
    def test_matching_tempo_does_not_toggle_sync(self):
        with patch.object(controller, 'observe', return_value=state(b=124)), patch.object(controller, 'checked') as send:
            controller.sync_tempo(2)
            send.assert_not_called()

    def test_unknown_tempo_sends_nothing(self):
        with patch.object(controller, 'observe', return_value=state(b=None)), patch.object(controller, 'checked') as send:
            with self.assertRaises(RuntimeError):
                controller.sync_tempo(2)
            send.assert_not_called()

    def test_sync_refuses_changed_reference_or_track(self):
        stale_track = state(b=124)
        stale_track['decks'][1]['title'] = 'A different track'
        def verify(predicate):
            self.assertFalse(predicate(state(a=125, b=125)))
            self.assertFalse(predicate(stale_track))
            self.assertTrue(predicate(state(b=124)))
            return state(b=124)
        with patch.object(controller, 'observe', return_value=state()), patch.object(controller, 'checked') as send, patch.object(controller, 'wait_for', side_effect=verify):
            controller.sync_tempo(2)
            send.assert_called_once()

    def test_unconfirmed_step_is_not_resent(self):
        with patch.object(controller, 'observe', return_value=state()), patch.object(controller, 'checked') as send, patch.object(controller, 'wait_for', side_effect=RuntimeError('No fresh confirmation')):
            with self.assertRaises(RuntimeError):
                controller.tempo_step(2, 'up')
            send.assert_called_once()

    def test_opposite_tempo_change_is_not_success(self):
        def verify(predicate):
            self.assertFalse(predicate(state(b=124)))
            self.assertTrue(predicate(state(b=125.62)))
            return state(b=125.62)
        with patch.object(controller, 'observe', return_value=state()), patch.object(controller, 'checked'), patch.object(controller, 'wait_for', side_effect=verify):
            controller.tempo_step(2, 'up')

if __name__ == '__main__':
    unittest.main()
