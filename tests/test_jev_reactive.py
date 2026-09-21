import copy
import io
import json
from pathlib import Path
import sys
import unittest
import tempfile
from urllib.error import URLError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_reactive as reactive


def observation():
    return {'layoutCalibrated': True, 'folder': '26', 'decks': [
        {'deck': 1, 'title': 'A', 'bpm': 124., 'fader': 1., 'playingIndicator': True},
        {'deck': 2, 'title': 'B', 'bpm': 125., 'fader': 0., 'playingIndicator': False}]}


def answer(prepared, choice):
    options = prepared['payload']['questions']['next_action']['criteria']
    return {'type': 'choice', 'choice': choice, 'confidence': 1.,
            'probabilities': {key: float(key == choice) for key in options}}


def fixture_ask(prepared, api_key):
    return {'answers': {'next_action': answer(prepared, 'deck2_play')},
            'request_seconds': .01, 'inference_performed': True}


def no_log(prepared, api_key, evaluator):
    return evaluator(prepared, api_key)


class ReactiveContract(unittest.TestCase):
    def test_widget_pipe_supplies_key_once_without_logging_or_saving_it(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(reactive, 'ROOT', Path(folder)), \
             patch.object(reactive.sys, 'argv', ['jev_reactive.py', '--key-stdin']), \
             patch.object(reactive.sys, 'stdin', io.StringIO('test-widget-secret\n')), \
             patch.object(reactive.sys, 'stdout', io.StringIO()) as output, \
             patch.object(reactive, 'trial') as trial:
            self.assertEqual(reactive.main(), 0)
            trial.assert_called_once_with('test-widget-secret', 3, Path(folder)/'evidence')
            self.assertNotIn('test-widget-secret', output.getvalue())
            for file in Path(folder).rglob('*'):
                if file.is_file():
                    self.assertNotIn('test-widget-secret', file.read_text())

    def test_widget_pipe_rejects_empty_oversized_or_control_character_values(self):
        for value in ('', '\n', 'x'*4097, 'abc\x00def\n'):
            with self.assertRaises(ValueError):
                reactive.pipe_key(io.StringIO(value))

    def test_received_late_answer_is_preserved_for_inspection(self):
        prepared = reactive.prepare(observation())
        response = {'model': 'fixture', 'answers': {'next_action': answer(prepared, 'deck2_play')}}
        from unittest.mock import MagicMock
        stream = MagicMock()
        stream.__enter__.return_value.read.return_value = json.dumps(response).encode()
        with patch.object(reactive.time, 'monotonic', side_effect=[10, 15]):
            result = reactive.ask(prepared, 'test', timeout=3, transport=lambda *a, **kw: stream)
        self.assertEqual(result['request_seconds'], 5)
        self.assertEqual(result['answers']['next_action']['choice'], 'deck2_play')
        self.assertNotIn('execution', result)

    def test_connection_failure_is_not_mislabeled_as_model_timeout(self):
        def failing(*args, **kwargs):
            raise URLError(ConnectionRefusedError())
        with self.assertRaisesRegex(RuntimeError, 'verbindingsfout \\(ConnectionRefusedError\\)'):
            reactive.ask(reactive.prepare(observation()), 'test', transport=failing)

    def test_fresh_state_and_history_reach_next_question_without_mix_plans(self):
        prepared = reactive.prepare(observation(), [{'choice': 'wait'}])
        payload = prepared['payload']
        self.assertEqual(set(payload['questions']), {'next_action'})
        self.assertEqual(payload['state']['recent_actions'], [{'choice': 'wait'}])
        self.assertEqual(payload['state']['music']['observation'], observation())
        self.assertNotIn('selection_procedure', payload['state']['tutorial_guidance'])
        options = payload['questions']['next_action']['criteria']
        self.assertIn('deck2_play', options)
        self.assertIn('deck1_fader_5', options)
        self.assertIn('deck2_sync', options)
        self.assertNotIn('proposal', json.dumps(options))

    def test_changed_track_discards_answer_before_control(self):
        before = observation()
        after = copy.deepcopy(before)
        after['decks'][1]['title'] = 'Changed'
        with patch.object(reactive, 'execute') as executor:
            result = reactive.cycle('test', observer=iter([before, after]).__next__,
                                    asker=fixture_ask, executor=executor, recorder=no_log)
        self.assertEqual(result['execution']['status'], 'stale')
        executor.assert_not_called()

    def test_answer_causes_exactly_one_control_with_no_preplanned_followup(self):
        calls = []
        def execute(action, state):
            calls.append(action)
            return {'status': 'verified'}
        result = reactive.cycle('test', observer=observation, asker=fixture_ask,
                                executor=execute, recorder=no_log)
        self.assertEqual(calls, [{'label': 'Deck 2: starten', 'operation': 'play', 'deck': 2}])
        self.assertEqual(result['execution']['status'], 'verified')
        self.assertIn('control_and_readback_seconds', result['timing'])

    def test_expired_answer_never_reaches_controls(self):
        with patch.object(reactive, 'execute') as executor:
            result = reactive.cycle('test', observer=observation, asker=fixture_ask,
                                    executor=executor, recorder=no_log, max_age=0)
        self.assertEqual(result['execution']['status'], 'expired')
        executor.assert_not_called()

    def test_unknown_action_is_rejected(self):
        prepared = reactive.prepare(observation())
        bad = answer(prepared, 'deck2_play')
        bad['choice'] = 'invented'
        with self.assertRaises(ValueError):
            reactive.validate(prepared, {'model': 'test', 'answers': {'next_action': bad}})

    def test_pause_does_not_stop_only_playing_deck(self):
        with patch.object(reactive.controller, 'checked') as bridge:
            result = reactive.execute({'operation': 'pause', 'deck': 1}, observation())
        self.assertEqual(result['status'], 'blocked')
        bridge.assert_not_called()

    def test_control_uses_native_readback_without_an_extra_observation(self):
        state = observation()
        after = copy.deepcopy(state)
        after['decks'][1]['playingIndicator'] = True
        with patch.object(reactive.controller, 'checked', return_value={'after': 'raw'}) as bridge, \
             patch.object(reactive.controller, 'normalized_state', return_value=after), \
             patch.object(reactive.controller, 'observe') as observe:
            result = reactive.execute({'operation': 'play', 'deck': 2}, state)
        self.assertEqual(result['status'], 'verified')
        bridge.assert_called_once_with({'command': 'action', 'action': 'deck2.playPause', 'expectedTrack': 'B'})
        observe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
