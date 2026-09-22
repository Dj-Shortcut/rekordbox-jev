"""Actual control-layer tests with injected native results; no UI or network."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dj_brain
import dj_controls as controls
from test_dj_brain import NOW, TRACKS, frame, load, bass, answer


def library():
    return [{**t, 'beatgrid': [{'position_seconds': .1, 'bpm': t['original_bpm'], 'beat_in_bar': 3, 'meter': '4/4'}]}
            for t in TRACKS]


class Native:
    def __init__(self, raw, *, missing_after=False, wrong_track=False, paired_sign=True, immobile=False):
        self.raw, self.calls = deepcopy(raw), []
        self.missing_after, self.wrong_track = missing_after, wrong_track
        self.paired_sign, self.immobile = paired_sign, immobile

    def __call__(self, p):
        self.calls.append(deepcopy(p))
        cmd = p['command']
        if cmd == 'observe':
            return deepcopy(self.raw)
        result = {'dispatched': True, 'verified': True}
        if cmd == 'openFolder26':
            self.raw['browserHeading'] = '26'
        elif cmd == 'loadChosenTrack':
            i = next(i for i, t in enumerate(TRACKS) if t['file'] == p['file'])
            load(self.raw, 'A' if p['deck'] == 1 else 'B', i, playing=False)
        elif cmd == 'setPlayback':
            self.raw['playingIndicators'][f'deck{p["deck"]}'] = p['playing']
        elif cmd == 'action':
            deck = p['action'][4]
            what = p['action'].split('.')[1]
            if what == 'master':
                self.raw['mixer']['master_lit'] = {str(n): str(n) == deck for n in (1, 2)}
            elif what == 'sync':
                self.raw['mixer']['beat_sync_lit'][deck] = True
            elif what != 'start':
                raise AssertionError('Unexpected shortcut '+what)
        elif cmd == 'launchAligned':
            self.raw['playingIndicators'][f'deck{p["incoming"]}'] = True
            self.raw['mixer']['red_bar_aligned'] = True
        elif cmd == 'crossfader':
            self.raw['mixer']['crossfader_position'] = p['value']
        elif cmd == 'eqReset':
            for band in p['bands']:
                self.raw['mixer']['eq_neutral'][str(p['deck'])][band] = True
                self.raw['mixer']['eq_position'][str(p['deck'])][band] = 0.
        elif cmd == 'eq':
            self.move(p['deck'], -p['pixels']*.02)
        elif cmd == 'mixGesture':
            if 'crossfader' in p:
                self.raw['mixer']['crossfader_position'] = p['crossfader']
                result['crossfaderVerified'] = True
            if 'bassPixels' in p:
                direction = 1 if self.paired_sign else -1
                self.move(p['outgoing'], -p['bassPixels']*.02*direction)
                self.move(p['incoming'], p['bassPixels']*.02*direction)
                result['bassDirectionVerified'] = self.paired_sign
        else:
            raise AssertionError('Unexpected native command '+cmd)
        if self.wrong_track:
            self.raw['decks'][1]['title'] = 'Three'
        if not self.missing_after:
            result['after'] = deepcopy(self.raw)
        return result

    def move(self, deck, delta):
        if self.immobile:
            return
        position = max(-1., min(0., self.raw['mixer']['eq_position'][str(deck)]['low']+delta))
        self.raw['mixer']['eq_position'][str(deck)]['low'] = position
        self.raw['mixer']['eq_neutral'][str(deck)]['low'] = position == 0.


class DJControlsTests(unittest.TestCase):
    def both(self):
        raw = load(load(frame(), 'A', playing=True), 'B', 1, playing=True)
        raw['browserHeading'] = '26'
        raw['mixer']['red_bar_aligned'] = True
        return raw

    def chosen(self, raw, choice, track=None, gesture='beats4'):
        prepared = dj_brain.prepare(raw, library(), {}, NOW)
        candidate = next((k for k, t in prepared.get('payload', {}).get('state', {}).get('candidates', {}).items()
                          if t['title'] == track), None)
        return dj_brain.resolve(prepared, answer(prepared, choice, candidate, gesture))

    def run_control(self, action, raw, native, tracks=None):
        return controls.execute(action, raw, tracks or library(), {}, native=native, now_ns=lambda: NOW)

    def test_replaces_only_stopped_inaudible_deck_and_verifies_loaded_identity(self):
        raw = self.both(); raw['playingIndicators']['deck2'] = False
        raw['mixer']['crossfader_position'] = 0.
        action = self.chosen(raw, 'load_B', track='Three')
        native = Native(raw)
        after, session = self.run_control(action, raw, native)
        self.assertEqual(after['decks'][1]['title'], 'Three')
        self.assertTrue(session['action_confirmed'])
        self.assertEqual([p['command'] for p in native.calls], ['loadChosenTrack'])
        self.assertTrue(native.calls[0]['replaceStopped'])
        self.assertEqual(native.calls[0]['expectedTrack'], 'Two')
        for change in ('audible', 'playing'):
            unsafe = deepcopy(raw)
            if change == 'audible':
                unsafe['mixer']['crossfader_position'] = .5
            else:
                unsafe['playingIndicators']['deck2'] = True
            native = Native(unsafe)
            with self.assertRaises(controls.ControlBlocked):
                self.run_control(action, unsafe, native)
            self.assertEqual(native.calls, [])

    def test_wrong_loaded_readback_is_not_confirmed_or_retried(self):
        raw = load(frame(), 'A', playing=True); raw['browserHeading'] = '26'
        action = self.chosen(raw, 'load_B', track='Two')
        native = Native(raw, wrong_track=True)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(action, raw, native)
        self.assertEqual(len(native.calls), 1)

    def test_red_markers_false_prevent_all_fader_and_bass_dispatch(self):
        raw = self.both()
        for option in ('mix_B', 'bass_B'):
            action = self.chosen(raw, option)
            unaligned = deepcopy(raw); unaligned['mixer']['red_bar_aligned'] = False
            native = Native(unaligned)
            with self.assertRaises(controls.ControlBlocked) as failed:
                self.run_control(action, unaligned, native)
            self.assertTrue(failed.exception.retryable)
            self.assertFalse(failed.exception.dispatched)
            self.assertEqual(native.calls, [])

    def test_expected_library_identity_change_prevents_dispatch(self):
        raw = self.both(); action = self.chosen(raw, 'mix_B')
        changed = deepcopy(raw); changed['decks'][1]['title'] = 'Three'
        native = Native(changed)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(action, changed, native)
        self.assertEqual(native.calls, [])

    def test_incoming_launch_uses_exported_downbeat_and_keeps_faders_untouched(self):
        raw = load(load(frame(), 'A', playing=True, bpm=123), 'B', 1, bpm=123)
        raw['mixer']['crossfader_position'] = 0.
        bass(raw, 'B', -.6)
        action = self.chosen(raw, 'play_B')
        native = Native(raw)
        after, session = self.run_control(action, raw, native)
        self.assertEqual([p['command'] for p in native.calls], ['action', 'launchAligned'])
        self.assertEqual(native.calls[0]['action'], 'deck2.start')
        self.assertAlmostEqual(native.calls[1]['cueOffsetSeconds'], (.1+2*60/124)*124/123)
        self.assertEqual(native.calls[1]['expectedTracks'], {'1': 'One', '2': 'Two'})
        self.assertTrue(after['playingIndicators']['deck1'])
        self.assertTrue(after['playingIndicators']['deck2'])
        self.assertTrue(session['action_confirmed'])
        tracks = library(); tracks[1].pop('beatgrid')
        native = Native(raw)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(action, raw, native, tracks)
        self.assertEqual(native.calls, [])

    def test_stop_cannot_stop_an_audible_deck(self):
        raw = self.both()
        action = {'action': 'STOP', 'parameters': {'deck': 'A'}}
        native = Native(raw)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(action, raw, native)
        self.assertEqual(native.calls, [])
        raw['mixer']['crossfader_position'] = 1.
        native = Native(raw)
        after, _ = self.run_control(action, raw, native)
        self.assertFalse(after['playingIndicators']['deck1'])
        self.assertTrue(after['playingIndicators']['deck2'])
        self.assertEqual(native.calls[0]['playing'], False)

    def test_bass_pair_moves_complementarily_and_finishes_at_neutral_without_boost(self):
        raw = self.both(); bass(raw, 'B', -.6)
        action = self.chosen(raw, 'bass_B')
        native = Native(raw)
        after, session = self.run_control(action, raw, native)
        self.assertTrue(session['action_confirmed'])
        self.assertTrue(after['mixer']['eq_neutral']['2']['low'])
        self.assertAlmostEqual(after['mixer']['eq_position']['1']['low'], -.6, delta=.1)
        pairs = [p for p in native.calls if p['command'] == 'mixGesture']
        self.assertGreater(len(pairs), 0)
        self.assertTrue(all(p['outgoing'] == 1 and p['incoming'] == 2 and 0 < p['bassPixels'] <= 5 for p in pairs))

    def test_wrong_bass_direction_stops_without_repeating_input(self):
        raw = self.both(); bass(raw, 'B', -.6)
        native = Native(raw, paired_sign=False)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(self.chosen(raw, 'bass_B'), raw, native)
        self.assertEqual(len(native.calls), 1)

    def test_bass_pair_requires_new_after_frame_before_another_input(self):
        raw = self.both(); bass(raw, 'B', -.6)
        native = Native(raw, missing_after=True)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(self.chosen(raw, 'bass_B'), raw, native)
        self.assertLessEqual(len(native.calls), 1, 'Never replay a relative paired gesture without actual readback')

    def test_paired_saturation_stops_after_first_immobile_gesture(self):
        raw = self.both(); bass(raw, 'B', -.6)
        native = Native(raw, immobile=True)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(self.chosen(raw, 'bass_B'), raw, native)
        self.assertLessEqual(len(native.calls), 1, 'An immobile paired control must not be replayed twenty times')

    def test_changed_tracks_in_native_mix_readback_cannot_be_claimed_verified(self):
        raw = self.both(); native = Native(raw, wrong_track=True)
        with self.assertRaises(controls.ControlBlocked):
            self.run_control(self.chosen(raw, 'mix_B'), raw, native)
        self.assertEqual(len(native.calls), 1)

    def test_stale_after_frame_retains_dispatched_flag_and_cannot_trigger_automatic_retry(self):
        raw = self.both(); hardware = Native(raw)
        def native(payload):
            result = hardware(payload)
            result['after']['sampledAtMonotonicNS'] = NOW-10_000_000_000
            return result
        with self.assertRaises(controls.ControlBlocked) as failure:
            self.run_control(self.chosen(raw, 'mix_B'), raw, native)
        self.assertTrue(failure.exception.dispatched)
        self.assertFalse(failure.exception.retryable)
        self.assertEqual(len(hardware.calls), 1)

    def test_alignment_loss_after_a_bass_input_is_not_labeled_undispatched(self):
        raw = self.both(); bass(raw, 'B', -.6)
        hardware = Native(raw)
        def native(payload):
            result = hardware(payload)
            result['after']['mixer']['red_bar_aligned'] = False
            return result
        with self.assertRaises(controls.ControlBlocked) as failure:
            self.run_control(self.chosen(raw, 'bass_B'), raw, native)
        self.assertTrue(failure.exception.dispatched)
        self.assertFalse(failure.exception.retryable)
        self.assertEqual(len(hardware.calls), 1)


if __name__ == '__main__':
    unittest.main()
