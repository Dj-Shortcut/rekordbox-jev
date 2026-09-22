"""Runner contract tests: fake hardware/provider, no Rekordbox or network input."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import URLError
from urllib.request import Request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dj_set
import jev_reactive


class Brain:
    @staticmethod
    def prepare(raw, tracks, *, session, now_ns):
        if raw.get('unreadable'):
            return {'decision': {'source': 'code', 'action': 'OBSERVE_AGAIN', 'reason': 'OCR temporarily missing'}}
        options = {name: {'action': name, 'parameters': {}}
                   for name in raw['available']}
        questions = {'action': {'type': 'choice', 'instructions': 'Choose the next action',
                                'criteria': {name: name for name in options}}}
        return {'execution_enabled': False, 'payload': {'questions': questions, 'state': raw},
                'payload_id': str(raw['revision']), 'context_id': str(raw['revision']),
                'decision': {'source': 'jev', 'option_actions': options}}

    @staticmethod
    def resolve(prepared, result):
        return deepcopy(prepared['decision']['option_actions'][result['answers']['action']['choice']])

    @staticmethod
    def applicable(action, prepared):
        return action in prepared['decision']['option_actions'].values()


class Hardware:
    def __init__(self):
        self.raw = {'revision': 0, 'decks': [{'deck': 1, 'title': 'Not Loaded'}, {'deck': 2, 'title': 'Not Loaded'}],
                    'playingIndicators': {'deck1': False, 'deck2': False},
                    'available': ['HOLD', 'LOAD_C_A', 'LOAD_B_A', 'LOAD_A_A']}
        self.calls, self.executed = [], []
        self.reads = 0
        self.on_observe = None

    def native(self, payload):
        self.calls.append(deepcopy(payload))
        result = {'protocolVersion': 3} if payload['command'] == 'status' else {}
        return {'ok': True, 'result': result}

    def observe(self, native):
        self.reads += 1
        if self.on_observe:
            self.on_observe(self)
        return deepcopy(self.raw)

    def execute(self, action, raw, tracks, session, *, native, now_ns):
        name = action['action']
        assert name in self.raw['available']
        self.executed.append(name)
        self.raw['revision'] += 1
        if name.startswith('LOAD_'):
            _, track, target = name.split('_')
            deck = 1 if target == 'A' else 2
            self.raw['decks'][deck-1]['title'] = 'Track '+track
            self.raw['available'] = ['HOLD', ('START_' if len(self.executed) == 1 else 'LAUNCH_')+target]
        elif name.startswith(('START_', 'LAUNCH_')):
            target = name.split('_')[1]
            self.raw['playingIndicators']['deck'+('1' if target == 'A' else '2')] = True
            self.raw['available'] = ['HOLD', 'LOAD_B_B', 'LOAD_A_B'] if name.startswith('START_') else ['HOLD', 'HANDOFF_'+target]
        elif name.startswith('HANDOFF_'):
            incoming = name.split('_')[1]
            outgoing = 2 if incoming == 'A' else 1
            self.raw['playingIndicators'][f'deck{outgoing}'] = False
            self.raw['available'] = ['HOLD', 'LOAD_A_A', 'LOAD_C_A'] if incoming == 'B' else ['HOLD', 'LOAD_C_B']
        return deepcopy(self.raw), {**session, 'pending_action': 'none', 'action_confirmed': True}


class DJSetTests(unittest.TestCase):
    def run_set(self, hardware, choices, *, iterations=None, evaluator_hook=None, configure=None):
        sleeps, requests = [], []
        def evaluator(prepared, api_key):
            self.assertEqual(api_key, 'private-test-key')
            requests.append(deepcopy(prepared))
            if evaluator_hook:
                evaluator_hook(prepared)
            choice = next(choices)
            return {'inference_performed': True, 'payload_id': prepared['payload_id'],
                    'answers': {'action': {'choice': choice}}, 'request_seconds': .01}
        with tempfile.TemporaryDirectory() as directory:
            runner = dj_set.DJSet('private-test-key', native=hardware.native, controls=hardware,
                read_library=lambda: [], brain=Brain, evaluator=evaluator, output_root=directory,
                sleeper=sleeps.append)
            if configure:
                configure(runner)
            report = runner.run(max_iterations=iterations)
            for file in Path(directory).rglob('*'):
                if file.is_file():
                    self.assertNotIn('private-test-key', file.read_text())
            return report, requests, sleeps

    def test_provider_choices_drive_three_tracks_and_two_handoffs_without_stopping(self):
        hardware = Hardware()
        actions = ['LOAD_C_A', 'START_A', 'LOAD_B_B', 'LAUNCH_B', 'HANDOFF_B',
                   'LOAD_A_A', 'LAUNCH_A', 'HANDOFF_A', 'HOLD']
        report, requests, sleeps = self.run_set(hardware, iter(actions), iterations=len(actions))
        self.assertEqual(report['status'], 'stopped')
        self.assertEqual(report['reason'], 'test_iteration_limit')
        self.assertEqual(hardware.executed, actions[:-1])
        self.assertEqual(report['jev_requests'], len(actions))
        self.assertEqual(report['verified_actions'], 8)
        self.assertEqual(report['session']['recent_tracks'], ['Track C', 'Track B', 'Track A'])
        self.assertEqual(sleeps, [.2])
        # Chosen C was not the first or alphabetically first option. No local
        # selection replaced the supplied provider answers.
        self.assertEqual(hardware.raw['decks'][0]['title'], 'Track A')
        self.assertTrue(hardware.raw['playingIndicators']['deck1'])
        self.assertFalse(hardware.raw['playingIndicators']['deck2'])

    def test_stale_provider_answer_is_discarded_then_current_state_is_asked(self):
        hardware = Hardware()
        def change_on_answer(prepared):
            if prepared['payload_id'] == '0':
                hardware.raw.update(revision=1, available=['HOLD'])
        report, requests, _ = self.run_set(hardware, iter(['LOAD_C_A', 'HOLD']),
                                          iterations=2, evaluator_hook=change_on_answer)
        self.assertEqual(hardware.executed, [])
        self.assertEqual(report['jev_requests'], 2)
        self.assertEqual(requests[1]['payload_id'], '1')
        self.assertTrue(any(e['event'] == 'answer_discarded' for e in report['events']))

    def test_transient_missing_ocr_retries_without_stopping_or_choosing(self):
        hardware = Hardware()
        hardware.on_observe = lambda h: h.raw.update(unreadable=h.reads <= 3)
        report, requests, _ = self.run_set(hardware, iter(['LOAD_B_A']), iterations=4)
        self.assertEqual(report['verified_actions'], 1)
        self.assertEqual(report['jev_requests'], 1)
        self.assertEqual(hardware.executed, ['LOAD_B_A'])

    def test_network_failure_never_dispatches_a_local_fallback(self):
        hardware = Hardware()
        def fail(prepared):
            raise RuntimeError('provider timeout private-test-key')
        report, _, _ = self.run_set(hardware, iter([]), iterations=3, evaluator_hook=fail)
        self.assertEqual(report['jev_requests'], 3)
        self.assertEqual(report['jev_answers'], 0)
        self.assertEqual(hardware.executed, [])
        self.assertEqual(hardware.reads, 3)

    def test_unconfirmed_input_stops_without_replaying_it(self):
        hardware = Hardware()
        def uncertain(*args, **kwargs):
            hardware.executed.append('unconfirmed')
            raise RuntimeError('Input sent; result unknown')
        hardware.execute = uncertain
        report, _, _ = self.run_set(hardware, iter(['LOAD_C_A']), iterations=10)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(hardware.executed, ['unconfirmed'])
        self.assertEqual(report['jev_requests'], 1)

    def test_undispatched_changed_guard_reasks_instead_of_stopping(self):
        hardware = Hardware()
        original = hardware.execute
        class Changed(RuntimeError):
            retryable = True
            dispatched = False
        calls = 0
        def execute(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise Changed('Markers changed before dispatch')
            return original(*args, **kwargs)
        hardware.execute = execute
        report, _, _ = self.run_set(hardware, iter(['LOAD_C_A', 'LOAD_B_A']), iterations=2)
        self.assertEqual(report['status'], 'stopped')
        self.assertEqual(hardware.executed, ['LOAD_B_A'])

    def test_default_unbounded_loop_stops_only_when_requested_and_leaves_playback(self):
        hardware = Hardware()
        holder = {}
        def stop(prepared):
            holder['runner'].request_stop()
        report, _, _ = self.run_set(hardware, iter(['LOAD_C_A']), evaluator_hook=stop,
                                   configure=lambda runner: holder.update(runner=runner))
        self.assertEqual(report['reason'], 'user_stop')
        self.assertEqual(hardware.executed, [])

    def test_widget_production_route_calls_set_runner_not_legacy_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'config').mkdir()
            (root/'config/live_trial.json').write_text('{"implementation":"dj_set"}')
            with patch.object(jev_reactive, 'ROOT', root), \
                 patch.object(sys, 'argv', ['jev_reactive.py', '--dj-test', '--key-stdin']), \
                 patch.object(sys, 'stdin', io.StringIO('private-test-key\n')), \
                 patch.object(dj_set, 'run', return_value={'status': 'stopped'}) as run, \
                 patch.object(jev_reactive, 'trial', side_effect=AssertionError('No legacy trial')):
                self.assertEqual(jev_reactive.main(), 0)
                run.assert_called_once_with('private-test-key')


class TransportTests(unittest.TestCase):
    def test_reuses_connection_across_requests_and_never_replays_failed_post(self):
        connections = []
        class Connection:
            def __init__(self, *args, **kwargs):
                self.requests = []
                self.sock = None
                self.fail = False
                connections.append(self)
            def request(self, *args, **kwargs):
                self.requests.append((args, kwargs))
                if self.fail:
                    raise OSError('Connection closed')
            def getresponse(self):
                response = io.BytesIO(b'{}'); response.status = 200
                return response
            def close(self):
                pass
        transport = dj_set.PersistentTransport(connection_factory=Connection)
        request = Request(dj_set.ENDPOINT, data=b'{}', method='POST')
        for _ in range(2):
            with transport(request) as response:
                self.assertEqual(response.read(), b'{}')
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(connections[0].requests), 2)
        connections[0].fail = True
        with self.assertRaises(URLError):
            transport(request)
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(connections[0].requests), 3)
        with transport(request):
            pass
        self.assertEqual(len(connections), 2)


class RealBrainRunnerTests(unittest.TestCase):
    def test_actual_brain_and_runner_continue_through_two_handoffs(self):
        import dj_brain
        from test_contextual_live import raw_empty
        tracks = [{'file': f'{title}.mp3', 'title': title, 'artist': 'Fixture',
                   'music_scope': '26', 'original_bpm': 124., 'key': 'Abm',
                   'beatgrid': [{'position_seconds': .1, 'bpm': 124., 'beat_in_bar': 1, 'meter': '4/4'}]}
                  for title in ('First', 'Second', 'Third')]
        raw = raw_empty()
        raw['mixer'].update(eq_neutral={str(n): dict.fromkeys(('trim', 'high', 'mid', 'low'), True) for n in (1, 2)},
                            eq_position={str(n): dict.fromkeys(('trim', 'high', 'mid', 'low'), 0.) for n in (1, 2)},
                            beat_sync_lit={'1': False, '2': False}, master_lit={'1': False, '2': False})
        executed, prepared_requests = [], []
        plans = iter([('load_A', 'Third'), ('play_A', None), ('load_B', 'Second'),
                      ('prepare_B', None), ('play_B', None), ('mix_center', None),
                      ('bass_B', None), ('mix_B', None), ('stop_A', None),
                      ('load_A', 'First'), ('prepare_A', None), ('play_A', None),
                      ('mix_center', None), ('bass_A', None), ('mix_A', None), ('stop_B', None), ('hold', None)])

        def native(payload):
            return {'ok': True, 'result': {'protocolVersion': 3}}

        class Controls:
            @staticmethod
            def observe(native):
                return deepcopy(raw)

            @staticmethod
            def execute(action, before, library, session, *, native, now_ns):
                executed.append(deepcopy(action))
                name, p = action['action'], action['parameters']
                deck = '1' if p.get('deck') == 'A' else '2'
                if name == 'LOAD_TRACK':
                    prepared = dj_brain.prepare(raw, tracks, session, now_ns())
                    title = prepared['library'][p['track_id']]['title']
                    raw['decks'][int(deck)-1].update(title=title, displayedBPM='124.00 0.0%', metadata='124.00 Abm',
                                                  elapsedSeconds=0., remainingSeconds=300.)
                elif name == 'PREPARE':
                    other = '2' if deck == '1' else '1'
                    raw['mixer']['crossfader_position'] = 0. if other == '1' else 1.
                    raw['mixer']['master_lit'] = {deck: False, other: True}
                    raw['mixer']['beat_sync_lit'][deck] = True
                    raw['mixer']['eq_neutral'][deck]['low'] = False
                    raw['mixer']['eq_position'][deck]['low'] = -.6
                elif name == 'PLAY':
                    raw['playingIndicators']['deck'+deck] = True
                    raw['mixer']['red_bar_aligned'] = True
                elif name == 'MIX':
                    raw['mixer']['crossfader_position'] = {'A': 0., 'center': .5, 'B': 1.}[p['target']]
                elif name == 'BASS':
                    incoming = '1' if p['target'] == 'A' else '2'
                    other = '2' if incoming == '1' else '1'
                    raw['mixer']['eq_neutral'][incoming]['low'] = True
                    raw['mixer']['eq_position'][incoming]['low'] = 0.
                    raw['mixer']['eq_neutral'][other]['low'] = False
                    raw['mixer']['eq_position'][other]['low'] = -.6
                elif name == 'STOP':
                    raw['playingIndicators']['deck'+deck] = False
                else:
                    raise AssertionError('Unexpected action '+name)
                return deepcopy(raw), {**session, 'pending_action': 'none', 'action_confirmed': True}

        def evaluator(prepared, api_key):
            action, title = next(plans)
            prepared_requests.append(deepcopy(prepared))
            choices = {'dj_action': action, 'gesture': 'beats4'}
            if 'next_track' in prepared['payload']['questions']:
                choices['next_track'] = next((k for k, v in prepared['payload']['state']['candidates'].items()
                                              if v['title'] == title), next(iter(prepared['candidate_map'])))
            answers = {}
            for qid, question in prepared['payload']['questions'].items():
                choice = choices[qid]
                self.assertIn(choice, question['criteria'])
                answers[qid] = {'type': 'choice', 'choice': choice, 'confidence': 1.,
                                'probabilities': {k: float(k == choice) for k in question['criteria']}}
            return {'model': 'fixture-provider', 'answers': answers, 'inference_performed': True,
                    'payload_id': prepared['payload_id'], 'context_id': prepared['context_id'], 'request_seconds': .01}

        with tempfile.TemporaryDirectory() as directory:
            runner = dj_set.DJSet('fixture-secret', native=native, controls=Controls, brain=dj_brain,
                                 read_library=lambda: tracks, evaluator=evaluator,
                                 output_root=directory, now_ns=lambda: 1_500_000_000, sleeper=lambda _: None)
            result = runner.run(max_iterations=17)
        self.assertEqual(result['status'], 'stopped', result.get('error'))
        self.assertEqual(result['verified_actions'], 16, result['events'][-6:])
        self.assertEqual(result['jev_answers'], 17)
        self.assertEqual([e['parameters']['target'] for e in executed if e['action'] == 'MIX'], ['center', 'B', 'center', 'A'])
        self.assertEqual(result['session']['recent_tracks'], ['Third', 'Second', 'First'])
        self.assertEqual(raw['decks'][0]['title'], 'First')
        self.assertTrue(raw['playingIndicators']['deck1'])
        self.assertFalse(raw['playingIndicators']['deck2'])
        self.assertNotIn('Third', [c['title'] for c in prepared_requests[9]['payload']['state']['candidates'].values()])


if __name__ == '__main__':
    unittest.main()
