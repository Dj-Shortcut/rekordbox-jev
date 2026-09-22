import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import contextual_live as live
import jev_reactive


def raw_empty():
    return {'sampledAtMonotonicNS': 1_000_000_000, 'layoutCalibrated': True,
            'browserHeading': 'Verzameling', 'observationMS': 300,
            'decks': [{'deck': n, 'title': 'Not Loaded', 'metadata': '', 'displayedBPM': ''} for n in (1, 2)],
            'playingIndicators': {'deck1': False, 'deck2': False},
            'faders': {'deck1': 1., 'deck2': 1.},
            'mixer': {'crossfader_position': .5, 'deck_assignments': {'1': 'left', '2': 'right'},
                      'red_bar_aligned': None, 'eq_neutral': {'1': {'low': True}, '2': {'low': True}}}}


def library():
    return [{'file': f'{i}.mp3', 'title': f'Track {i}', 'artist': 'Test',
             'music_scope': '26', 'original_bpm': 124., 'key': 'Abm'} for i in (1, 2)]


def reply(prepared):
    qid = prepared['decision']['question_id']
    keys = list(prepared['payload']['questions'][qid]['criteria'])
    return {'model': 'fixture', 'answers': {qid: {'type': 'choice', 'choice': keys[0],
            'confidence': 1., 'probabilities': {k: float(k == keys[0]) for k in keys}}}}


class ContextualLiveTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(live, 'write_event')
        self.write_event = patcher.start()
        self.addCleanup(patcher.stop)
    def test_real_empty_marker_routes_to_track_question_not_duration(self):
        prepared = live.prepare(raw_empty(), library(), now_ns=1_500_000_000)
        self.assertEqual(prepared['decision']['question_id'], 'opening_track')
        self.assertEqual(len(prepared['payload']['questions']), 1)
        self.assertNotIn('phrase_boundary_now', prepared['payload']['state']['current'])
        self.assertFalse(prepared['execution_enabled'])

    def test_blank_ocr_does_not_issue_track_question(self):
        raw = raw_empty(); raw['decks'][0]['title'] = ''
        prepared = live.prepare(raw, library(), now_ns=1_500_000_000)
        self.assertEqual(prepared['decision']['source'], 'code')
        self.assertEqual(prepared['decision']['action'], 'OBSERVE_AGAIN')
        self.assertNotIn('payload', prepared)

    def test_evaluation_preserves_provider_answer_and_never_invents_choice(self):
        prepared = live.prepare(raw_empty(), library(), now_ns=1_500_000_000)
        expected = reply(prepared)
        def transport(request, timeout):
            self.assertEqual(json.loads(request.data), prepared['payload'])
            return io.BytesIO(json.dumps(expected).encode())
        result = live.evaluate(prepared, 'test-only-key', transport=transport)
        self.assertEqual(result['raw_response'], expected)
        self.assertEqual(result['execution']['commands_sent'], 0)
        expected['answers']['opening_track']['choice'] = 'not-an-option'
        with self.assertRaises(ValueError):
            live.evaluate(prepared, 'test-only-key', transport=transport)

    def test_run_only_observes_and_keeps_key_out_of_evidence(self):
        commands = []
        def native(payload):
            commands.append(payload['command'])
            self.assertEqual(payload, {'command': 'observe'})
            return {'ok': True, 'result': raw_empty()}
        def evaluator(prepared, api_key):
            self.assertEqual(api_key, 'test-only-key')
            return {'answers': reply(prepared)['answers'], 'model': 'fixture'}
        def recorder(prepared, api_key, evaluator):
            return evaluator(prepared, api_key)
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', io.StringIO()):
            result = live.run('test-only-key', native=native, read_library=library,
                              evaluator=evaluator, recorder=recorder, output_root=directory,
                              now_ns=lambda: 1_500_000_000)
            self.assertEqual(result['status'], 'answered')
            self.assertEqual(commands, ['observe', 'observe'])
            self.assertEqual(result['control_commands_sent'], 0)
            for path in Path(directory).rglob('*.json'):
                self.assertNotIn('test-only-key', path.read_text())

    def test_code_proposal_is_neither_sent_to_model_nor_executed(self):
        raw = raw_empty(); raw['layoutCalibrated'] = False
        def unexpected(*args, **kwargs):
            raise AssertionError('Must not call model')
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', io.StringIO()):
            result = live.run('test-only-key', native=lambda p: {'ok': True, 'result': raw},
                              read_library=library, recorder=unexpected, output_root=directory,
                              now_ns=lambda: 1_500_000_000)
            self.assertEqual(result['status'], 'code_proposal_only')
            self.assertFalse(result['inference_performed'])
            self.assertEqual(self.write_event.call_args.args[0]['status'], 'not_sent')

    def test_failed_readback_preserves_a_real_answer(self):
        calls = 0
        def native(payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Window closed')
            return {'ok': True, 'result': raw_empty()}
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', io.StringIO()):
            result = live.run('test-only-key', native=native, read_library=library,
                              recorder=lambda p, **kw: reply(p), output_root=directory,
                              now_ns=lambda: 1_500_000_000)
            self.assertEqual(result['status'], 'answered')
            self.assertEqual(result['readback_error_type'], 'RuntimeError')
            self.assertIsNone(result['deck_and_mixer_fields_unchanged'])

    def test_signed_host_observer_branch_cannot_fall_into_old_mix_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'config').mkdir()
            (root/'config/live_trial.json').write_text('{"implementation":"contextual_observer"}')
            with patch.object(jev_reactive, 'ROOT', root), \
                 patch.object(sys, 'argv', ['jev_reactive.py', '--dj-test', '--key-stdin']), \
                 patch.object(sys, 'stdin', io.StringIO('test-only-key\n')), \
                 patch.object(live, 'run') as run, \
                 patch.object(jev_reactive, 'trial', side_effect=AssertionError('No legacy control')):
                self.assertEqual(jev_reactive.main(), 0)
                run.assert_called_once_with('test-only-key')

    def test_widget_start_rejects_legacy_or_missing_config_without_control(self):
        for implementation in ('continuous_session', 'playground_live', 'unknown'):
            with self.subTest(implementation=implementation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); (root/'config').mkdir()
                (root/'config/live_trial.json').write_text(json.dumps({'implementation': implementation}))
                with patch.object(jev_reactive, 'ROOT', root), \
                     patch.object(sys, 'argv', ['jev_reactive.py', '--dj-test', '--key-stdin']), \
                     patch.object(sys, 'stdin', io.StringIO('test-only-key\n')), \
                     patch.object(sys, 'stdout', io.StringIO()), \
                     patch.object(jev_reactive, 'trial', side_effect=AssertionError('No legacy control')), \
                     patch.object(live, 'run', side_effect=AssertionError('No observer with wrong config')):
                    self.assertEqual(jev_reactive.main(), 1)


if __name__ == '__main__':
    unittest.main()
