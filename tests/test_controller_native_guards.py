import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import controller
from mix_safety import MixBlocked


def native_status():
    return {'ok': True, 'result': {'protocolVersion': 3, 'nativeMixGuards': {
        'version': 1, 'freshAlignmentBeforeInput': True,
        'typedPreDispatchRejections': True, 'expectedTracksAndDeadline': True,
        'mixStep': True}}}


def observation(mixer=None):
    raw = {'sampledAtMonotonicNS': time.monotonic_ns(), 'observationMS': 100,
           'layoutCalibrated': True, 'browserHeading': '26', 'decks': []}
    if mixer is not None:
        raw['mixer'] = mixer
    return raw


class NativeGuardContract(unittest.TestCase):
    def test_recovery_requires_explicit_no_input_and_known_retryable_code(self):
        response = {'ok': False, 'error': 'Unreadable marker', 'code': 'alignment_not_confirmed',
                    'errorKind': 'pre_dispatch_guard', 'commandsSent': False, 'retryable': True}
        with patch('controller.request', return_value=response):
            with self.assertRaises(controller.PreDispatchRejected) as caught:
                controller.checked({'command': 'observe'})
        self.assertFalse(caught.exception.commands_sent)
        self.assertTrue(caught.exception.retryable)
        for changes in ({'commandsSent': True}, {'commandsSent': None}, {'retryable': False},
                        {'code': 'deadline_expired'}, {'errorKind': 'execution_error'}):
            with self.subTest(changes=changes), patch('controller.request', return_value={**response, **changes}):
                with self.assertRaises(controller.NativeCommandError) as caught:
                    controller.checked({'command': 'observe'})
                self.assertNotIsInstance(caught.exception, controller.PreDispatchRejected)
                self.assertFalse(caught.exception.retryable)

    def test_native_mixed_step_needs_no_legacy_screenshot(self):
        command = {'command': 'mixStep', 'crossfader': 0.3,
                   'outgoing': 1, 'incoming': 2, 'pixels': 5.0,
                   'expectedTracks': {'1': 'A', '2': 'B'}, 'notAfterMonotonicNS': 200}
        with patch('controller.request', side_effect=[native_status(), {'ok': True, 'result': {'dispatched': True}}]) as request:
            controller.checked(command)
        self.assertEqual([call.args[0] for call in request.call_args_list], [{'command': 'status'}, command])

    def test_native_fader_guard_is_not_cached_across_bridge_restarts(self):
        bad_mixer = {'red_bar_aligned': False, 'red_bar_alignment_error_px': 25}
        responses = [native_status(), {'ok': True, 'result': {}},
                     {'ok': True, 'result': {'protocolVersion': 2}},
                     {'ok': True, 'result': observation(bad_mixer)}]
        with patch('controller.request', side_effect=responses) as request:
            controller.checked({'command': 'crossfader', 'value': 0.2})
            with self.assertRaises(MixBlocked):
                controller.checked({'command': 'crossfader', 'value': 0.4})
        self.assertEqual([c.args[0]['command'] for c in request.call_args_list],
                         ['status', 'crossfader', 'status', 'observe'])

    def test_old_bridge_cannot_silently_ignore_deadline_or_mixed_step(self):
        for command in ({'command': 'mixStep', 'crossfader': 0.5},
                        {'command': 'crossfader', 'value': 0.5, 'notAfterMonotonicNS': 123},
                        {'command': 'eqPair', 'outgoing': 1, 'incoming': 2, 'pixels': 5.0,
                         'expectedTracks': {'1': 'A', '2': 'B'}}):
            with self.subTest(command=command), patch('controller.request', return_value={
                    'ok': True, 'result': {'protocolVersion': 2}}) as request:
                with self.assertRaises(controller.NativeCommandError) as caught:
                    controller.checked(command)
                self.assertEqual(caught.exception.code, 'native_guard_unavailable')
                self.assertFalse(caught.exception.commands_sent)
                request.assert_called_once_with({'command': 'status'})

    def test_normalization_keeps_same_frame_mixer(self):
        mixer = {'red_bar_aligned': True, 'crossfader_position': 0.2,
                 'eq_neutral': {'1': {'low': True}}}
        raw = observation(mixer)
        state = controller.normalized_state(raw)
        self.assertIs(state['mixer'], mixer)
        self.assertEqual(state['sampledAtMonotonicNS'], raw['sampledAtMonotonicNS'])


if __name__ == '__main__':
    unittest.main()
