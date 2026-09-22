import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import contextual_session as session
from test_contextual_live import raw_empty, library, reply


def tracks():
    result = library()
    result.append({**result[0], 'file': '3.mp3', 'title': 'Track 3'})
    return result


class Native:
    def __init__(self, fail_load=False, fail_play=False):
        self.raw = raw_empty()
        self.raw['mixer']['eq_neutral'] = {str(d): dict.fromkeys(('low', 'mid', 'high', 'trim'), True) for d in (1, 2)}
        self.calls = []
        self.fail_load, self.fail_play = fail_load, fail_play

    def loaded(self, deck, title):
        self.raw['decks'][deck-1].update(title=title, displayedBPM='124.00', metadata='Test 124.00 Abm -04:00.0 00:00.0')

    def __call__(self, payload):
        p = copy.deepcopy(payload)
        self.calls.append(p)
        command = p['command']
        if command == 'status':
            result = {'contextualTransport': {'version': 1}}
        elif command == 'activate':
            result = {'activationRequested': True}
        elif command == 'observe':
            result = self.raw
        elif command == 'openFolder26':
            self.raw['browserHeading'] = '26'
            result = {'verified': True, 'after': self.raw}
        elif command == 'loadChosenTrack':
            title = next(t['title'] for t in tracks() if t['file'] == p['file'])
            self.loaded(p['deck'], title)
            result = {'verified': not self.fail_load, 'dispatched': True, 'after': self.raw}
        elif command == 'setPlayback':
            assert p['openingOnly'] is True and p['playing'] is True
            self.raw['playingIndicators'][f"deck{p['deck']}"] = not self.fail_play
            result = {'verified': not self.fail_play, 'dispatched': True, 'after': self.raw}
        else:
            raise AssertionError('Unexpected command '+command)
        return {'ok': True, 'result': copy.deepcopy(result)}


class ContextualSessionTests(unittest.TestCase):
    def run_trial(self, native, evaluator=None):
        def recorder(prepared, *, api_key, evaluator):
            return evaluator(prepared, api_key=api_key)
        def answer(prepared, **kwargs):
            result = reply(prepared)
            result['request_seconds'] = .12
            return result
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', io.StringIO()):
            runner = session.ContextualSession('test-secret', native=native, read_library=tracks,
                evaluator=evaluator or answer, recorder=recorder, output_root=directory,
                now_ns=lambda: 1_500_000_000,
                observer=lambda native: native({'command': 'observe'}))
            result = runner.run_start_trial()
            for path in Path(directory).rglob('*.json'):
                self.assertNotIn('test-secret', path.read_text())
            return result

    def test_real_decision_loop_chooses_loads_starts_then_asks_again(self):
        native = Native()
        result = self.run_trial(native)
        self.assertEqual(result['status'], 'opening_playing_next_chosen')
        self.assertFalse(result['full_mix_test'])
        self.assertEqual(result['deck_commands_attempted'], 2)
        answers = [x for x in result['events'] if x['event'] == 'jev_answer']
        self.assertEqual([x['question'] for x in answers], ['opening_track', 'next_track'])
        self.assertEqual([p['command'] for p in native.calls if p['command'] in ('loadChosenTrack', 'setPlayback')],
                         ['loadChosenTrack', 'setPlayback'])
        self.assertTrue(native.raw['playingIndicators']['deck1'])
        self.assertNotEqual(result['next_track']['title'], native.raw['decks'][0]['title'])

    def test_state_changes_while_jev_answers_prevent_execution(self):
        native = Native()
        def answer(prepared, **kwargs):
            native.loaded(1, 'Track 2')
            return reply(prepared)
        result = self.run_trial(native, answer)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 0)
        self.assertIn('veranderde', result['error'])

    def test_load_without_confirmation_never_starts_or_repeats(self):
        native = Native(fail_load=True)
        result = self.run_trial(native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 1)
        self.assertEqual(sum(p['command'] == 'loadChosenTrack' for p in native.calls), 1)
        self.assertFalse(any(p['command'] == 'setPlayback' for p in native.calls))

    def test_failed_play_confirmation_never_repeats_toggle(self):
        native = Native(fail_play=True)
        result = self.run_trial(native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(sum(p['command'] == 'setPlayback' for p in native.calls), 1)

    def test_existing_loaded_track_is_not_overwritten(self):
        native = Native(); native.loaded(1, 'Track 2')
        result = self.run_trial(native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 0)

    def test_closed_route_or_non_neutral_eq_prevents_start(self):
        for mode in ('route', 'eq'):
            with self.subTest(mode=mode):
                native = Native()
                if mode == 'route':
                    native.raw['faders']['deck1'] = .4
                else:
                    native.raw['mixer']['eq_neutral']['1']['low'] = False
                result = self.run_trial(native)
                self.assertEqual(result['status'], 'blocked')
                self.assertFalse(any(p['command'] == 'setPlayback' for p in native.calls))

    def test_model_failure_is_not_replaced_with_local_track_choice(self):
        def error(*args, **kwargs):
            raise RuntimeError('timeout test-secret')
        result = self.run_trial(Native(), error)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 0)
        self.assertNotIn('test-secret', result['error'])


class ContinuousContextualSessionTests(unittest.TestCase):
    def run_loop(self, native, cycles, *, evaluator=None, executor=None, configure=None):
        sleeps, requests = [], []
        def answer(prepared, **kwargs):
            requests.append(prepared['decision']['question_id'])
            return evaluator(prepared, **kwargs) if evaluator else reply(prepared)
        def recorder(prepared, *, api_key, evaluator):
            return evaluator(prepared, api_key=api_key)
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', io.StringIO()):
            runner = session.ContextualSession('test-secret', native=native, read_library=tracks,
                evaluator=answer, recorder=recorder, output_root=directory,
                now_ns=lambda: 1_500_000_000, sleeper=sleeps.append,
                controls_executor=executor, observer=lambda command: command({'command': 'observe'}))
            if configure:
                configure(runner)
            result = runner.run_loop(max_cycles=cycles)
            cards = [json.loads(p.read_text()) for p in runner.widget_events.glob('*.json')]
            for path in Path(directory).rglob('*.json'):
                self.assertNotIn('test-secret', path.read_text())
            return result, sleeps, requests, cards

    def test_continues_beyond_opener_and_next_selection_into_second_load(self):
        native = Native()
        result, sleeps, questions, _ = self.run_loop(native, 5)
        self.assertEqual(result['status'], 'loop_stopped')
        self.assertEqual(result['reason'], 'test_cycle_limit')
        self.assertEqual(questions, ['opening_track', 'next_track'])
        loads = [p for p in native.calls if p['command'] == 'loadChosenTrack']
        self.assertEqual(len(loads), 2)
        self.assertIs(loads[0]['openingOnly'], True)
        self.assertIs(loads[1]['openingOnly'], False)
        self.assertEqual(loads[1]['expectedTrack'], 'Not Loaded')
        self.assertEqual(result['session']['outgoing_deck'], 'A')
        self.assertEqual(result['session']['incoming_deck'], 'B')
        self.assertEqual(result['session']['selected'], 'none')
        self.assertEqual(result['verified_deck_actions'], 3)
        self.assertEqual(sleeps, [])
        self.assertFalse(result['full_mix_test'])

    def test_existing_loaded_track_starts_without_replacement_then_loop_continues(self):
        native = Native(); native.loaded(2, 'Track 2')
        result, _, questions, _ = self.run_loop(native, 3)
        self.assertEqual(result['status'], 'loop_stopped')
        physical = [p for p in native.calls if p['command'] in ('setPlayback', 'loadChosenTrack')]
        self.assertEqual([p['command'] for p in physical], ['setPlayback', 'loadChosenTrack'])
        self.assertEqual([p['deck'] for p in physical], [2, 1])
        self.assertEqual(native.raw['decks'][1]['title'], 'Track 2')
        self.assertEqual(questions, ['next_track'])
        self.assertEqual(result['session']['outgoing_deck'], 'B')

    def test_existing_playing_deck_is_preserved_while_empty_deck_loads(self):
        native = Native(); native.loaded(1, 'Track 2')
        native.raw['playingIndicators']['deck1'] = True
        result, _, questions, _ = self.run_loop(native, 2)
        self.assertEqual(result['status'], 'loop_stopped')
        self.assertEqual(questions, ['next_track'])
        self.assertFalse(any(p['command'] == 'setPlayback' for p in native.calls))
        self.assertEqual(native.raw['decks'][0]['title'], 'Track 2')
        self.assertTrue(native.raw['playingIndicators']['deck1'])

    def test_repeated_unknown_observation_is_bounded_and_publishes_actual_reason(self):
        native = Native(); native.raw['decks'][0]['title'] = ''
        result, _, questions, cards = self.run_loop(native, 1)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 0)
        self.assertEqual(sum(p['command'] == 'observe' for p in native.calls), 3)
        self.assertEqual(sum(e['event'] == 'observation_retry' for e in result['events']), 3)
        self.assertIn('unknown', result['error'])
        self.assertEqual(questions, [])
        blocked = [card for card in cards if card['status'] == 'error']
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]['error'], result['error'])
        self.assertEqual(blocked[0]['request']['payload']['questions'], {})

    def test_hold_waits_then_asks_again_without_physical_action_or_overlap(self):
        native = Native(); native.loaded(1, 'Track 1'); native.loaded(2, 'Track 2')
        native.raw['playingIndicators']['deck1'] = True
        native.raw['faders']['deck2'] = 0
        in_request = False
        def hold(prepared, **kwargs):
            nonlocal in_request
            self.assertFalse(in_request)
            in_request = True
            result = reply(prepared)
            qid = prepared['decision']['question_id']
            self.assertIn('HOLD', prepared['payload']['questions'][qid]['criteria'])
            result['answers'][qid]['choice'] = 'HOLD'
            in_request = False
            return result
        result, sleeps, questions, _ = self.run_loop(native, 2, evaluator=hold)
        self.assertEqual(result['status'], 'loop_stopped')
        self.assertEqual(questions, ['launch', 'launch'])
        self.assertEqual(sleeps, [.2, .2])
        self.assertEqual(result['deck_commands_attempted'], 0)
        self.assertGreaterEqual(sum(p['command'] == 'observe' for p in native.calls), 4)

    def test_delegate_uses_after_frame_and_partial_failure_is_not_retried(self):
        native = Native(); native.loaded(1, 'Track 1'); native.loaded(2, 'Track 2')
        native.raw['playingIndicators']['deck1'] = True
        native.raw['faders']['deck2'] = 0
        delegated = []
        def executor(action, raw, adapted, bookkeeping, *, native, now_ns):
            delegated.append(action['action'])
            self.assertEqual(bookkeeping['pending_action'], 'none')
            self.assertTrue(bookkeeping['action_confirmed'])
            raise RuntimeError('Partial control failed test-secret')
        result, _, questions, cards = self.run_loop(native, 3, executor=executor)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(delegated, ['LAUNCH_INCOMING'])
        self.assertEqual(questions, ['launch'])
        self.assertEqual(result['session']['pending_action'], 'LAUNCH_INCOMING')
        self.assertFalse(result['session']['action_confirmed'])
        self.assertIn('[verborgen]', result['error'])
        self.assertTrue(any(card['status'] == 'error' for card in cards))

    def test_verified_delegate_continues_using_its_returned_state(self):
        native = Native(); native.loaded(1, 'Track 1'); native.loaded(2, 'Track 2')
        native.raw['playingIndicators']['deck1'] = True
        native.raw['faders']['deck2'] = 0
        delegated = []
        def executor(action, raw, adapted, bookkeeping, *, native, now_ns):
            delegated.append(action['action'])
            after = copy.deepcopy(raw)
            after['playingIndicators']['deck2'] = True
            after['mixer']['red_bar_aligned'] = False
            # This returned state deliberately differs from the fixture's stored
            # state. The next local decision must consume this after-frame.
            bookkeeping.update(pending_action='none', action_confirmed=True)
            return after, bookkeeping
        result, _, questions, _ = self.run_loop(native, 2, executor=executor)
        self.assertEqual(result['status'], 'loop_stopped')
        self.assertEqual(delegated, ['LAUNCH_INCOMING', 'ALIGN_BEATS'])
        self.assertEqual(questions, ['launch'])
        self.assertEqual(result['verified_deck_actions'], 2)

    def test_main_entrypoint_calls_continuous_loop_without_cycle_cap(self):
        with patch.object(session, 'ContextualSession') as factory:
            session.run('test-secret')
            factory.return_value.run_loop.assert_called_once_with()
            factory.return_value.run_start_trial.assert_not_called()

    def test_api_failure_has_no_local_fallback_and_no_repeated_load(self):
        native = Native()
        def error(*args, **kwargs):
            raise RuntimeError('Jev timeout test-secret')
        result, _, questions, _ = self.run_loop(native, 4, evaluator=error)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['deck_commands_attempted'], 0)
        self.assertEqual(questions, ['opening_track'])


if __name__ == '__main__':
    unittest.main()
