"""Execute real session methods against simulated decks, never Rekordbox.

The simulator models transport, relative knobs, crossfader position and track
ends. It does not model actual audio loudness, image recognition or API quality.
"""
import copy
import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dj_session
from mix_safety import MixBlocked


class SimulatedDecks:
    def __init__(self, fault=None):
        self.now = 1000.0
        self.fault = fault
        self.fault_input_index = None
        self.live_fades = 0
        self.started = False
        self.crossfader = .5
        self.inputs = []
        self.questions = []
        self.selections = 0
        self.tracks = [{'file': name + '.mp3', 'title': name, 'artist': 'Fixture',
                        'key': 'Abm', 'original_bpm': 120.0, 'music_scope': '26',
                        'duration_seconds': 130.0,
                        'beatgrid': [{'position_seconds': 0.0, 'beat_in_bar': 1, 'bpm': 120.0}]}
                       for name in ('First', 'Second', 'Third', 'Unused')]
        self.decks = {1: self.track_state(self.tracks[0]), 2: self.track_state(self.tracks[3])}
        self.master = 1

    @staticmethod
    def track_state(track):
        return {'track': copy.deepcopy(track), 'elapsed': 0.0, 'playing': False,
                'fader': .4, 'synced': False,
                'eq': {band: 0.0 for band in ('trim', 'high', 'mid', 'low')}}

    def clock(self):
        return self.now

    def audible(self, deck):
        weight = 1-self.crossfader if deck == 1 else self.crossfader
        return self.decks[deck]['playing'] and self.decks[deck]['fader'] > 0 and weight > .001

    def advance(self, seconds):
        assert seconds >= 0
        if self.started:
            playing_times = [max(0, d['track']['duration_seconds']-d['elapsed'])
                             for number, d in self.decks.items() if self.audible(number)]
            assert playing_times and max(playing_times) >= seconds, 'Simulated audible decks ran out of music'
        for deck in self.decks.values():
            if deck['playing']:
                deck['elapsed'] += seconds
                if deck['elapsed'] >= deck['track']['duration_seconds']:
                    deck['playing'] = False
        self.now += seconds

    def sleep(self, seconds):
        if self.fault == 'late' and self.live_fades and self.fault_input_index is None:
            self.fault_input_index = len(self.inputs)
            seconds += 2.0
        self.advance(seconds)

    def observe(self):
        self.advance(.02)
        stale = unaligned = False
        if self.fault in ('stale', 'unaligned') and self.live_fades:
            if self.fault_input_index is None:
                self.fault_input_index = len(self.inputs)
            stale = self.fault == 'stale'
            unaligned = self.fault == 'unaligned'
        return {
            'sampledAtMonotonicNS': int((self.now-(2 if stale else 0))*1e9),
            'layoutCalibrated': True, 'folder': '26',
            'decks': [{'deck': number, 'title': d['track']['title'], 'library': copy.deepcopy(d['track']),
                       'bpm': 120.0, 'fader': d['fader'], 'playingIndicator': d['playing'],
                       'elapsed_seconds': d['elapsed'],
                       'remaining_seconds': max(0, d['track']['duration_seconds']-d['elapsed'])}
                      for number, d in self.decks.items()],
            'mixer': {'crossfader_position': self.crossfader,
                      'deck_assignments': {'1': 'left', '2': 'right'},
                      'red_bar_aligned': not unaligned,
                      'red_bar_alignment_error_px': 24.0 if unaligned else .5,
                      'master_lit': {str(n): n == self.master for n in self.decks},
                      'beat_sync_lit': {str(n): d['synced'] for n, d in self.decks.items()},
                      'eq_neutral': {str(n): {band: math.isclose(value, 0, abs_tol=1e-9)
                                             for band, value in d['eq'].items()}
                                     for n, d in self.decks.items()}}}

    def log(self, command, **fields):
        row = {'command': command, 'at': self.now, **fields}
        self.inputs.append(row)
        return row

    def dispatch(self, payload):
        command = payload['command']
        if command == 'status':
            return {'protocolVersion': 3, 'accessibility': True, 'screenRecording': True}
        if command == 'activate':
            return {'activationRequested': True}
        row = self.log(command, **{k: v for k, v in payload.items() if k != 'command'})
        if command == 'action':
            name, action = payload['action'].split('.')
            number = int(name[-1])
            deck = self.decks[number]
            assert payload['expectedTrack'] == deck['track']['title']
            if action == 'start':
                assert not deck['playing'], 'Seeking a playing deck'
                deck['elapsed'] = 0.0
            elif action == 'master':
                self.master = number
            elif action == 'sync':
                deck['synced'] = not deck['synced']
            else:
                raise AssertionError('Unexpected native action: ' + action)
        elif command == 'fader':
            self.decks[payload['deck']]['fader'] = payload['value']
        elif command == 'crossfader':
            if all(d['playing'] for d in self.decks.values()):
                self.live_fades += 1
                row['during_overlap'] = True
            self.crossfader = payload['value']
        elif command == 'eqReset':
            eq = self.decks[payload['deck']]['eq']
            for band in payload.get('bands', eq):
                eq[band] = 0.0
        elif command == 'eq':
            self.decks[payload['deck']]['eq'][payload['band']] -= payload['pixels']
        elif command == 'eqPair':
            outgoing, incoming = payload['outgoing'], payload['incoming']
            assert {outgoing, incoming} == {1, 2}
            assert all(d['playing'] for d in self.decks.values())
            assert 0 < payload['pixels'] <= 5
            row['bass_before'] = [self.decks[n]['eq']['low'] for n in (outgoing, incoming)]
            self.decks[outgoing]['eq']['low'] -= payload['pixels']
            self.decks[incoming]['eq']['low'] += payload['pixels']
            row['bass_after'] = [self.decks[n]['eq']['low'] for n in (outgoing, incoming)]
        elif command == 'launchAligned':
            outgoing, incoming = payload['outgoing'], payload['incoming']
            assert self.decks[outgoing]['playing'] and not self.decks[incoming]['playing']
            assert self.crossfader == outgoing-1, 'Incoming deck audible before its aligned start'
            assert self.decks[incoming]['synced'], 'Incoming deck not synced'
            assert payload['expectedTracks'] == {str(n): d['track']['title'] for n, d in self.decks.items()}
            self.advance(2.0-self.now % 2.0)
            self.decks[incoming]['playing'] = True
        else:
            raise AssertionError('Unexpected native command: ' + command)
        self.advance(.08)
        return {'dispatched': True, 'pairMS': 80.0, 'latenessMS': 0.0}

    def playback(self, deck, play):
        if not play and self.decks[deck]['playing']:
            assert not self.audible(deck), 'Outgoing deck paused before becoming inaudible'
        self.log('playback', deck=deck, play=play)
        self.decks[deck]['playing'] = play
        if play:
            self.started = True
        return self.observe()

    def load(self, deck, filename, *, search=False):
        assert not self.decks[deck]['playing'] and not self.audible(deck)
        assert self.crossfader == 2-deck, 'Loading into the audible side'
        track = next(t for t in self.tracks if t['file'] == filename)
        fader = self.decks[deck]['fader']
        self.decks[deck] = self.track_state(track)
        self.decks[deck]['fader'] = fader
        self.log('load', deck=deck, file=filename)
        self.advance(.5)
        return self.observe()

    def ask(self, state, questions, label):
        assert not all(d['playing'] for d in self.decks.values()), 'Network decision during active overlap'
        self.questions.append(copy.deepcopy(questions))
        if 'track' in questions:
            self.selections += 1
            if self.selections == 3:
                # The real run loop reached preparation after TWO transitions.
                raise KeyboardInterrupt('End of bounded offline simulation')
            filename = ('Second.mp3', 'Third.mp3')[self.selections-1]
            choice = next(key for key, track in state['candidates'].items() if track['file'] == filename)
            answers = {'track': {'choice': choice}}
        else:
            assert '16' in questions['length']['criteria']
            answers = {'length': {'choice': '16'}}
        self.advance(.3)
        return {'response': {'answers': answers}, 'request_seconds': .3}


class SessionExecution(unittest.TestCase):
    def run_session(self, simulator):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/mixing_guidance.json').write_text('{}')
            stack.enter_context(patch.object(dj_session, 'ROOT', root))
            stack.enter_context(patch('dj_session.music_context.library', return_value=simulator.tracks))
            stack.enter_context(patch('dj_session.controller.playback', side_effect=simulator.playback))
            stack.enter_context(patch('dj_session.controller.load', side_effect=simulator.load))
            stack.enter_context(patch('dj_session.playground_live.ask', side_effect=simulator.ask))
            stack.enter_context(patch('mix_safety.time.monotonic', side_effect=simulator.clock))
            stack.enter_context(patch('controller.request', side_effect=AssertionError('Real native access forbidden')))
            stack.enter_context(patch('playground_live.urlopen', side_effect=AssertionError('Network access forbidden')))
            stack.enter_context(redirect_stdout(io.StringIO()))
            session = dj_session.Session(observe=simulator.observe, dispatch=simulator.dispatch,
                                         sleep=simulator.sleep, clock=simulator.clock)
            error = None
            try:
                session.run()
            except (MixBlocked, KeyboardInterrupt) as caught:
                error = caught
            evidence = json.loads((session.path / 'session.json').read_text())
            return session, error, evidence

    def test_two_real_transitions_load_third_track_reset_eq_and_continue(self):
        sim = SimulatedDecks()
        session, error, evidence = self.run_session(sim)
        self.assertIsInstance(error, KeyboardInterrupt)
        completed = [row for row in evidence if row['event'] == 'transition_completed']
        self.assertEqual([(row['active'], row['title']) for row in completed], [(2, 'Second'), (1, 'Third')])
        self.assertEqual([(row['deck'], row['file']) for row in sim.inputs if row['command'] == 'load'],
                         [(2, 'Second.mp3'), (1, 'Third.mp3')])
        self.assertEqual([(row['outgoing'], row['incoming']) for row in sim.inputs if row['command'] == 'launchAligned'],
                         [(1, 2), (2, 1)])
        self.assertEqual(sim.selections, 3)
        self.assertEqual(session.played, {'First.mp3', 'Second.mp3', 'Third.mp3'})
        self.assertTrue(sim.decks[1]['playing'])
        self.assertFalse(sim.decks[2]['playing'])
        self.assertEqual(sim.crossfader, 0.0)
        self.assertTrue(all(value == 0 for d in sim.decks.values() for value in d['eq'].values()))
        launches = [(index, row) for index, row in enumerate(sim.inputs) if row['command'] == 'launchAligned']
        for begin, launch in launches:
            end = next(index for index in range(begin+1, len(sim.inputs))
                       if sim.inputs[index]['command'] == 'playback'
                       and sim.inputs[index]['deck'] == launch['outgoing']
                       and sim.inputs[index]['play'] is False)
            positions = [float(launch['outgoing']-1)] + [row['value'] for row in sim.inputs[begin:end]
                                                        if row['command'] == 'crossfader']
            self.assertGreater(len(positions), 2)
            self.assertEqual(positions[-1], float(launch['incoming']-1))
            deltas = [b-a for a, b in zip(positions, positions[1:])]
            self.assertTrue(all(delta > 0 for delta in deltas) if launch['incoming'] == 2
                            else all(delta < 0 for delta in deltas))
        bass = [row for row in sim.inputs if row['command'] == 'eqPair']
        self.assertEqual({(row['outgoing'], row['incoming']) for row in bass}, {(1, 2), (2, 1)})
        for row in bass:
            self.assertAlmostEqual(sum(row['bass_before']), sum(row['bass_after']))
            self.assertLess(row['bass_after'][0], row['bass_before'][0])
            self.assertGreater(row['bass_after'][1], row['bass_before'][1])

    def test_stale_or_unaligned_frame_mid_transition_prevents_all_following_input(self):
        for fault in ('stale', 'unaligned'):
            with self.subTest(fault=fault):
                sim = SimulatedDecks(fault=fault)
                _, error, evidence = self.run_session(sim)
                self.assertIsInstance(error, MixBlocked)
                self.assertIsNotNone(sim.fault_input_index)
                self.assertEqual(len(sim.inputs), sim.fault_input_index)
                self.assertEqual(evidence[-1]['event'], 'session_blocked')
                self.assertFalse(any(row['event'] == 'transition_completed' for row in evidence))

    def test_late_clock_does_not_dispatch_catch_up_or_claim_completion(self):
        sim = SimulatedDecks(fault='late')
        _, error, evidence = self.run_session(sim)
        self.assertIsInstance(error, MixBlocked)
        self.assertIn('deadline', str(error))
        self.assertIsNotNone(sim.fault_input_index)
        self.assertEqual(len(sim.inputs), sim.fault_input_index)
        self.assertEqual(evidence[-1]['event'], 'session_blocked')
        self.assertFalse(any(row['event'] == 'transition_completed' for row in evidence))


if __name__ == '__main__':
    unittest.main()
