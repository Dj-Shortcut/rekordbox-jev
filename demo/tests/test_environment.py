"""Real environment adapter with fake native primitives; never touches macOS."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.environment import Rekordbox, NativePreDispatch, LocalPreDispatch, native_result
from djjev.runner import Runner
from djjev import policy
from test_policy import library, raw, loaded


class Native:
    def __init__(self, frame, mode=None):
        self.raw = deepcopy(frame)
        self.mode = mode
        self.calls = []
        self.stopped = False

    async def start(self): pass
    async def close(self): self.stopped = True

    def move(self, deck, delta):
        if self.mode == 'immobile': return
        key = str(deck)
        angle = max(-1., min(0., self.raw['mixer']['eq_position'][key]['low']+delta))
        self.raw['mixer']['eq_position'][key]['low'] = angle
        self.raw['mixer']['eq_neutral'][key]['low'] = angle == 0.

    async def call(self, role, name, **params):
        self.calls.append((role, name, deepcopy(params)))
        self.raw['sampledAtMonotonicNS'] = time.monotonic_ns()
        if name == 'observe': return deepcopy(self.raw)
        result = {'verified': True, 'dispatched': name != 'openFolder26'}
        if name == 'openFolder26':
            self.raw['browserHeading'] = '26'
        elif name == 'loadChosenTrack':
            if params.get('allowSilentReplacement'):
                assert params.get('replaceStopped') and params.get('expectedOtherTrack')
            index = next(i for i,t in enumerate(library()) if t['file'] == params['file'])
            loaded(self.raw, 'A' if params['deck']==1 else 'B', index)
        elif name == 'setPlayback':
            self.raw['playingIndicators']['deck'+str(params['deck'])] = params['playing']
        elif name == 'action':
            deck, action = params['action'][4], params['action'].split('.')[1]
            assert params['expectedTrack'] == self.raw['decks'][int(deck)-1]['title']
            if action == 'master': self.raw['mixer']['master_lit'][deck] = True
            elif action == 'sync': self.raw['mixer']['beat_sync_lit'][deck] = True
            elif action != 'start': raise AssertionError(action)
        elif name == 'launchAligned':
            self.raw['playingIndicators']['deck'+str(params['incoming'])] = True
            self.raw['mixer']['red_bar_aligned'] = True
        elif name == 'crossfader':
            self.raw['mixer']['crossfader_position'] = params['value']
        elif name == 'closeStoppedDeck':
            assert self.raw['playingIndicators']['deck'+str(params['deck'])] is False
            assert self.raw['playingIndicators']['deck'+str(3-params['deck'])] is True
            self.raw['mixer']['crossfader_position'] = 1. if params['deck']==1 else 0.
        elif name == 'openSilentDeck':
            assert all(value is False for value in self.raw['playingIndicators'].values())
            assert self.raw['faders']['deck'+str(params['deck'])] >= .9
            assert params['expectedTracks'] == {str(d['deck']):d['title'] for d in self.raw['decks']}
            self.raw['mixer']['crossfader_position'] = 0. if params['deck']==1 else 1.
        elif name == 'eq': self.move(params['deck'], -params['pixels']*.02)
        elif name == 'eqReset':
            if self.mode == 'reset_failure': result['verified'] = False
            else:
                for band in params['bands']:
                    self.raw['mixer']['eq_neutral'][str(params['deck'])][band] = True
                    self.raw['mixer']['eq_position'][str(params['deck'])][band] = 0.
        elif name == 'mixGesture':
            if 'bassPixels' in params:
                self.move(params['outgoing'], -params['bassPixels']*.02)
                self.move(params['incoming'], params['bassPixels']*.02)
                result['bassDirectionVerified'] = True
            if 'crossfader' in params:
                self.raw['mixer']['crossfader_position'] = params['crossfader']
                result['crossfaderVerified'] = True
        else: raise AssertionError(name)
        if self.mode == 'changed_track': self.raw['decks'][1]['title'] = 'Three'
        if self.mode != 'missing_after': result['after'] = deepcopy(self.raw)
        return result

    @property
    def physical(self):
        return [call for call in self.calls if call[0]=='control' and call[1]!='openFolder26']


def both():
    frame = loaded(loaded(raw(), playing=True), 'B', 1, True)
    frame['mixer']['red_bar_aligned'] = True
    return frame


def low(frame, deck, angle):
    frame['mixer']['eq_position'][str(deck)]['low'] = angle
    frame['mixer']['eq_neutral'][str(deck)]['low'] = angle == 0.


def decision(**values):
    mixing=any(values.get(k,'hold')!='hold' for k in ('crossfader','bass'))
    return {'expected_titles': {'A': 'One', 'B': 'Two'}, 'transport': 'mix' if mixing else 'hold',
            'crossfader': 'hold', 'bass': 'hold', 'duration_beats': 4, **values}


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_chosen_opening_play_opens_muted_route_with_both_decks_stopped(self):
        for selected in ('A','B'):
            with self.subTest(selected=selected):
                frame=loaded(loaded(raw(),ended=selected=='B'),'B',1,ended=selected=='A')
                frame['mixer']['crossfader_position']=1. if selected=='A' else 0.
                result,native=await self.run_decision(frame,decision(transport='play_'+selected))
                self.assertTrue(result['verified'])
                self.assertEqual(result['snapshot']['mixer']['cross'],0. if selected=='A' else 1.)
                self.assertTrue(result['snapshot']['decks'][selected]['playing'])
                self.assertFalse(result['snapshot']['decks']['B' if selected=='A' else 'A']['playing'])
                self.assertEqual([name for _,name,_ in native.physical],['openSilentDeck','setPlayback'])

    async def test_opening_play_rejects_fresh_other_playback_before_any_input(self):
        frame=loaded(loaded(raw(),ended=True),'B',1)
        frame['mixer']['crossfader_position']=0.
        native=Native(frame);env=Rekordbox(native,library());earlier=env.snapshot(frame)
        native.raw['playingIndicators']['deck1']=True
        with self.assertRaises(LocalPreDispatch):
            await env.execute(decision(transport='play_B'),earlier)
        self.assertEqual(native.physical,[])

    async def test_opening_route_readback_must_still_confirm_both_stopped_before_play(self):
        for defect in ('other_playing','wrong_endpoint','unverified'):
            with self.subTest(defect=defect):
                frame=loaded(loaded(raw(),ended=True),'B',1)
                frame['mixer']['crossfader_position']=0.
                native=Native(frame);original=native.call
                async def changed(role,name,**params):
                    result=await original(role,name,**params)
                    if name=='openSilentDeck':
                        if defect=='other_playing':result['after']['playingIndicators']['deck1']=True
                        elif defect=='wrong_endpoint':result['after']['mixer']['crossfader_position']=.5
                        else:result['verified']=False
                    return result
                native.call=changed;env=Rekordbox(native,library())
                with self.assertRaisesRegex(RuntimeError,'Openingsroute niet bevestigd'):
                    await env.execute(decision(transport='play_B'),env.snapshot(frame))
                self.assertEqual([name for _,name,_ in native.physical],['openSilentDeck'])

    async def test_opening_play_does_not_open_a_muted_channel_or_unready_track(self):
        for defect in ('channel','ended','eq'):
            with self.subTest(defect=defect):
                frame=loaded(loaded(raw(),ended=True),'B',1,ended=defect=='ended')
                frame['mixer']['crossfader_position']=0.
                if defect=='channel':frame['faders']['deck2']=0.
                if defect=='eq':low(frame,2,-.35)
                native=Native(frame);env=Rekordbox(native,library())
                with self.assertRaises(LocalPreDispatch):
                    await env.execute(decision(transport='play_B'),env.snapshot(frame))
                self.assertEqual(native.physical,[])

    async def test_failed_native_verification_is_traced_before_environment_raises(self):
        frame = both()
        frame['playingIndicators']['deck2'] = False
        frame['mixer']['crossfader_position'] = .5
        native = Native(frame)
        original = native.call
        async def failed_close(role, name, **params):
            result = await original(role, name, **params)
            if name == 'closeStoppedDeck':
                result.update(verified=False, pointerActions=1, elapsedMS=241.,
                    requestedCrossfader=0., measuredCrossfader=0.,
                    verification={'attempts': 3, 'elapsedMS': 180.,
                        'reasons': ['other_playback_unreadable'], 'renderWaitExhausted': True,
                        'targetPlaying': False, 'otherPlaying': None,
                        'actualTitles': {'1': 'One', '2': 'Two'},
                        'expectedTitles': {'1': 'One', '2': 'Two'},
                        'headers': {'Authorization': 'excluded-secret'}},
                    image='excluded-secret', tokens=['excluded-secret'])
                result['after']['tokens'] = ['excluded-secret']
            return result
        native.call = failed_close
        events = []
        env = Rekordbox(native, library(), native_trace=events.append)
        snapshot = env.snapshot(frame)
        with self.assertRaisesRegex(RuntimeError, 'Stilstaand deck niet bevestigd gesloten'):
            await env.execute(decision(transport='prepare_B'), snapshot)
        self.assertEqual([event['phase'] for event in events], ['started', 'returned'])
        start, returned = events
        self.assertEqual(start['command_id'], returned['command_id'])
        self.assertEqual(returned['decision_snapshot_version'], snapshot['version'])
        self.assertEqual(returned['command'], 'closeStoppedDeck')
        self.assertEqual(returned['parameters'], {'deck': 2})
        self.assertEqual(returned['expected_titles'], {'A': 'One', 'B': 'Two'})
        self.assertGreaterEqual(returned['seconds'], 0.)
        result = returned['result']
        self.assertIs(result['verified'], False)
        self.assertIs(result['dispatched'], True)
        self.assertEqual(result['verification']['reasons'], ['other_playback_unreadable'])
        self.assertEqual(result['after']['mixer']['cross'], 0.)
        self.assertTrue(result['after']['decks']['A']['playing'])
        self.assertNotIn('library', result['after'])
        self.assertNotIn('excluded-secret', json.dumps(events))
        self.assertEqual(len(native.physical), 1)

    async def test_broken_trace_callback_does_not_change_native_failure_or_retry(self):
        frame = both(); low(frame, 2, -.6)
        native = Native(frame, 'missing_after')
        def broken_trace(event):
            raise ValueError('display failed')
        env = Rekordbox(native, library(), native_trace=broken_trace)
        with self.assertRaisesRegex(RuntimeError, 'geen nieuwe waarneming'):
            await env.execute(decision(bass='B'), env.snapshot(frame))
        self.assertEqual(len(native.physical), 1)

    async def test_native_rejection_trace_keeps_explicit_zero_input_flags(self):
        frame = both(); frame['playingIndicators']['deck2'] = False
        native = Native(frame)
        async def rejected(role, name, **params):
            raise NativePreDispatch('stale', retryable=True)
        native.call = rejected
        events = []
        env = Rekordbox(native, library(), native_trace=events.append)
        with self.assertRaises(NativePreDispatch):
            await env.execute(decision(transport='prepare_B'), env.snapshot(frame))
        self.assertEqual([event['phase'] for event in events], ['started', 'error'])
        self.assertEqual(events[-1]['flags'], {'native_pre_dispatch': True,
            'commands_sent': False, 'dispatched': False, 'retryable': True})

    async def test_only_explicit_native_zero_input_rejection_is_retryable(self):
        reply = {'ok': False, 'error': 'stale', 'errorKind': 'pre_dispatch_guard',
                 'commandsSent': False, 'retryable': True}
        with self.assertRaises(NativePreDispatch) as caught:
            native_result(reply)
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.commands_sent)
        for changed in ({'commandsSent': True}, {'commandsSent': None}, {'retryable': 1}, {'errorKind': None}):
            with self.assertRaises(RuntimeError) as caught:
                native_result({**reply, **changed})
            self.assertNotIsInstance(caught.exception, NativePreDispatch)

    async def test_partial_bundle_cannot_claim_zero_inputs_or_retry(self):
        frame = both(); low(frame, 2, -.6)
        native = Native(frame)
        original = native.call
        async def reject_later(role, name, **params):
            if name == 'eqReset':
                raise NativePreDispatch('stale', retryable=True)
            return await original(role, name, **params)
        native.call = reject_later
        env = Rekordbox(native, library())
        with self.assertRaises(NativePreDispatch) as caught:
            await env.execute(decision(bass='B'), env.snapshot(frame))
        self.assertFalse(caught.exception.retryable)
        self.assertTrue(caught.exception.commands_sent)
        self.assertTrue(caught.exception.dispatched)
        self.assertTrue(native.physical)

    async def run_decision(self, frame, chosen, mode=None):
        native = Native(frame, mode)
        env = Rekordbox(native=native, library=library())
        result = await env.execute(chosen, env.snapshot(frame))
        return result, native

    async def test_one_model_answer_can_apply_bass_and_crossfader_and_confirm_both(self):
        frame = both(); low(frame, 2, -.6)
        result, native = await self.run_decision(frame, decision(bass='B', crossfader='B'))
        self.assertTrue(result['verified'])
        self.assertEqual(result['snapshot']['mixer']['cross'], 1.)
        self.assertTrue(result['snapshot']['decks']['B']['eq_neutral']['low'])
        self.assertAlmostEqual(result['snapshot']['decks']['A']['bass'], -.6, delta=.1)
        self.assertTrue(any('bassPixels' in p for _,name,p in native.physical if name=='mixGesture'))
        self.assertTrue(any('crossfader' in p for _,name,p in native.physical if name=='mixGesture'))
        self.assertFalse(any(name.startswith('dj') for _,name,_ in native.calls))

    async def test_hold_does_nothing_and_cannot_carry_a_mixer_gesture(self):
        frame=both();native=Native(frame);env=Rekordbox(native,library())
        result=await env.execute(decision(transport='hold'),env.snapshot(frame))
        self.assertTrue(result['verified']);self.assertFalse(result['dispatched'])
        self.assertEqual(native.calls,[])
        with self.assertRaisesRegex(ValueError,'MIX'):
            await env.execute(decision(transport='hold',crossfader='B'),env.snapshot(frame))
        self.assertEqual(native.calls,[])

    async def test_unaligned_beats_send_no_fader_or_bass_input(self):
        frame = both(); frame['mixer']['red_bar_aligned'] = False
        native = Native(frame); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(crossfader='B', bass='B'), env.snapshot(frame))
        self.assertEqual(native.physical, [])

    async def test_fresh_preflight_change_defers_without_any_control_call(self):
        for changed in ('alignment','title','invalid','bpm'):
            with self.subTest(changed=changed):
                frame=both();native=Native(frame);env=Rekordbox(native,library())
                earlier=env.snapshot(frame)
                if changed=='alignment':native.raw['mixer']['red_bar_aligned']=False
                elif changed=='title':loaded(native.raw,'B',2,True)
                elif changed=='invalid':native.raw['layoutCalibrated']=False
                else:native.raw['decks'][1]['displayedBPM']='unreadable'
                with self.assertRaises(LocalPreDispatch) as caught:
                    await env.execute(decision(crossfader='B'),earlier)
                self.assertTrue(Runner.explicitly_rejected_before_input(caught.exception))
                self.assertFalse(any(role=='control' for role,_,_ in native.calls))

    async def test_local_guard_after_any_control_attempt_is_not_retryable(self):
        frame=both();frame['playingIndicators']['deck2']=False
        frame['mixer']['eq_neutral']['2']['low']=False
        frame['mixer']['eq_position']['2']['low']=None
        native=Native(frame);original=native.call
        async def completed_no_input(role,name,**params):
            result=await original(role,name,**params)
            if name=='closeStoppedDeck':result['dispatched']=False
            return result
        native.call=completed_no_input;env=Rekordbox(native,library())
        with self.assertRaisesRegex(RuntimeError,'Bassknop niet leesbaar') as caught:
            await env.execute(decision(transport='prepare_B'),env.snapshot(frame))
        self.assertNotIsInstance(caught.exception,LocalPreDispatch)
        self.assertFalse(Runner.explicitly_rejected_before_input(caught.exception))
        self.assertEqual([name for role,name,_ in native.calls if role=='control'],['closeStoppedDeck'])

    async def test_observation_or_control_timeout_is_never_local_preflight_recovery(self):
        for failing_role in ('observer','control'):
            with self.subTest(failing_role=failing_role):
                frame=both();native=Native(frame);original=native.call
                async def timeout(role,name,**params):
                    if role==failing_role:raise asyncio.TimeoutError('in-flight status unknown')
                    return await original(role,name,**params)
                native.call=timeout;env=Rekordbox(native,library())
                with self.assertRaises(asyncio.TimeoutError) as caught:
                    await env.execute(decision(crossfader='B'),env.snapshot(frame))
                self.assertFalse(Runner.explicitly_rejected_before_input(caught.exception))

    async def test_stop_never_stops_audible_deck(self):
        frame = both(); native = Native(frame); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(transport='stop_A'), env.snapshot(frame))
        self.assertEqual(native.physical, [])

    async def test_load_replaces_stopped_closed_deck_and_keeps_other_playing(self):
        frame = both(); frame['playingIndicators']['deck2'] = False
        frame['mixer']['crossfader_position'] = 0.
        result, native = await self.run_decision(frame, decision(transport='load_B', track_id=library()[2]['id']))
        self.assertTrue(result['verified'])
        self.assertEqual(result['snapshot']['decks']['B']['title'], 'Three')
        self.assertTrue(result['snapshot']['decks']['A']['playing'])
        load_call = next(p for _,name,p in native.calls if name=='loadChosenTrack')
        self.assertTrue(load_call['replaceStopped'])
        self.assertEqual(load_call['expectedTrack'], 'Two')

    async def test_initial_empty_load_is_not_silent_replacement(self):
        frame = raw()
        result, native = await self.run_decision(frame, decision(transport='load_A',
            expected_titles={'A':'Not Loaded','B':'Not Loaded'}, track_id=library()[0]['id']))
        self.assertTrue(result['verified'])
        params = next(p for _,name,p in native.calls if name=='loadChosenTrack')
        self.assertFalse(params['replaceStopped'])
        self.assertFalse(params['allowSilentReplacement'])

    async def test_prepare_binds_shortcut_to_expected_deck_title(self):
        frame = both()
        frame['playingIndicators']['deck2'] = False
        frame['mixer']['crossfader_position'] = 0.
        frame['mixer']['beat_sync_lit']['2'] = False
        result, native = await self.run_decision(frame, decision(transport='prepare_B'))
        self.assertTrue(result['verified'])
        params = next(p for _,name,p in native.calls if name=='action')
        self.assertEqual(params['expectedTrack'], 'Two')

    async def test_preparation_only_closes_the_stopped_deck_without_requiring_beats(self):
        frame = both()
        frame['playingIndicators']['deck2'] = False
        frame['mixer']['red_bar_aligned'] = False
        frame['mixer']['crossfader_position'] = .5
        result, native = await self.run_decision(frame, decision(transport='prepare_B'))
        self.assertTrue(result['verified'])
        self.assertEqual(native.physical[0][1], 'closeStoppedDeck')
        self.assertEqual(result['snapshot']['mixer']['cross'], 0.)
        self.assertTrue(result['snapshot']['decks']['A']['playing'])
        self.assertFalse(result['snapshot']['decks']['B']['playing'])
        self.assertFalse(any(name in ('crossfader','mixGesture') for _,name,_ in native.physical))

    async def test_preparation_repairs_only_bad_non_bass_bands_and_preserves_reduced_bass(self):
        for bad in (['trim'],['high'],['mid'],['trim','high','mid']):
            with self.subTest(bands=bad):
                frame=both();frame['playingIndicators']['deck2']=False
                frame['mixer']['crossfader_position']=0.;low(frame,2,-.35)
                for band in bad:
                    frame['mixer']['eq_neutral']['2'][band]=False
                    frame['mixer']['eq_position']['2'][band]=.6
                observed_before=Rekordbox(Native(frame),library()).snapshot(frame)
                self.assertNotIn('play_B',policy.prepare(observed_before,[],False)['questions']['transport']['criteria'])
                result,native=await self.run_decision(frame,decision(transport='prepare_B'))
                self.assertTrue(result['verified'])
                self.assertEqual(result['snapshot']['decks']['B']['bass'],-.35)
                self.assertEqual([name for _,name,_ in native.physical],['eqReset'])
                self.assertEqual(native.physical[0][2]['bands'],bad)
                self.assertIn('play_B',policy.prepare(result['snapshot'],[],False)['questions']['transport']['criteria'])

    async def test_preparation_repairs_non_bass_then_normally_reduces_initial_neutral_bass(self):
        frame=both();frame['playingIndicators']['deck2']=False
        frame['mixer']['crossfader_position']=0.
        frame['mixer']['eq_neutral']['2']['trim']=False
        result,native=await self.run_decision(frame,decision(transport='prepare_B'))
        self.assertEqual([name for _,name,_ in native.physical],['eqReset','eq'])
        self.assertEqual(native.physical[0][2]['bands'],['trim'])
        self.assertTrue(result['snapshot']['decks']['B']['eq_neutral']['trim'])
        self.assertLess(result['snapshot']['decks']['B']['bass'],-.05)

    async def test_bad_non_bass_bands_prevent_play_or_align_before_any_input(self):
        for control in ('play_B','align_B'):
            for band in ('trim','high','mid'):
                with self.subTest(control=control,band=band):
                    frame=both();frame['playingIndicators']['deck2']=control=='align_B'
                    frame['mixer']['crossfader_position']=0.;low(frame,2,-.35)
                    frame['mixer']['eq_neutral']['2'][band]=False
                    native=Native(frame);env=Rekordbox(native,library())
                    with self.assertRaises(LocalPreDispatch):
                        await env.execute(decision(transport=control),env.snapshot(frame))
                    self.assertEqual(native.calls,[])

    async def test_unknown_non_bass_observation_defers_and_failed_reset_does_not_continue(self):
        frame=both();frame['playingIndicators']['deck2']=False
        frame['mixer']['crossfader_position']=0.
        frame['mixer']['eq_neutral']['2']['trim']=None
        native=Native(frame);env=Rekordbox(native,library())
        with self.assertRaises(LocalPreDispatch):
            await env.execute(decision(transport='prepare_B'),env.snapshot(frame))
        self.assertEqual(native.calls,[])
        frame['mixer']['eq_neutral']['2']['trim']=False
        native=Native(frame,'reset_failure');env=Rekordbox(native,library())
        with self.assertRaisesRegex(RuntimeError,'Neutrale EQ'):
            await env.execute(decision(transport='prepare_B'),env.snapshot(frame))
        self.assertEqual([name for _,name,_ in native.physical],['eqReset'])

    async def test_muted_channel_does_not_authorize_preparation_or_launch(self):
        for control in ('prepare_B','play_B','align_B'):
            for cross in (0.,.5):
                with self.subTest(control=control,cross=cross):
                    frame=both();frame['mixer']['crossfader_position']=cross
                    frame['faders']['deck2']=0.
                    frame['playingIndicators']['deck2']=control=='align_B'
                    native=Native(frame);env=Rekordbox(native,library())
                    with self.assertRaises(RuntimeError):
                        await env.execute(decision(transport=control),env.snapshot(frame))
                    self.assertEqual(native.physical,[])

    async def test_realignment_checks_launch_contract_before_stopping_silent_deck(self):
        for field,value in (('bpm',None),('bpm',125.),('cue_offset',None),('sync',False)):
            with self.subTest(field=field):
                frame=both();frame['mixer']['crossfader_position']=0.
                native=Native(frame);env=Rekordbox(native,library())
                observed=env.snapshot(frame);observed['decks']['B'][field]=value
                with self.assertRaises(RuntimeError):
                    await env.execute(decision(transport='align_B'),observed)
                self.assertEqual(native.physical,[])
                self.assertTrue(native.raw['playingIndicators']['deck2'])

    async def test_delayed_eq_render_only_reobserves_without_repeating_input(self):
        for changed_track in (False, True):
            with self.subTest(changed_track=changed_track):
                class DelayedNative(Native):
                    pending = None
                    reads = 0
                    async def call(self, role, name, **params):
                        if name == 'eq':
                            previous = deepcopy(self.raw)
                            reply = await super().call(role, name, **params)
                            self.pending = deepcopy(self.raw)
                            self.raw = previous
                            reply['after'] = deepcopy(previous)
                            return reply
                        if name == 'observe' and self.pending:
                            self.reads += 1
                            if self.reads == 2:
                                self.raw = self.pending
                                self.pending = None
                                if changed_track:
                                    loaded(self.raw, 'B', 2)
                        return await super().call(role, name, **params)
                frame = both()
                frame['playingIndicators']['deck2'] = False
                frame['mixer']['crossfader_position'] = 0.
                native = DelayedNative(frame)
                env = Rekordbox(native, library())
                if changed_track:
                    with self.assertRaisesRegex(RuntimeError, 'Track veranderde'):
                        await env.execute(decision(transport='prepare_B'), env.snapshot(frame))
                else:
                    result = await env.execute(decision(transport='prepare_B'), env.snapshot(frame))
                    self.assertTrue(result['verified'])
                    self.assertLess(result['snapshot']['decks']['B']['bass'], -.4)
                self.assertEqual([name for _,name,_ in native.physical], ['eq'])
                self.assertEqual(native.reads, 2)

    async def test_stopped_environment_dispatches_no_inputs(self):
        frame = both(); native = Native(frame); env = Rekordbox(native, library())
        await env.stop()
        with self.assertRaises(asyncio.CancelledError):
            await env.execute(decision(crossfader='B'), env.snapshot(frame))
        self.assertEqual(native.physical, [])

    async def test_missing_after_frame_prevents_repeated_paired_gesture(self):
        frame = both(); low(frame, 2, -.6)
        native = Native(frame, 'missing_after'); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(bass='B'), env.snapshot(frame))
        self.assertLessEqual(len(native.physical), 1)

    async def test_immobile_pair_is_not_repeated_twenty_times(self):
        frame = both(); low(frame, 2, -.6)
        native = Native(frame, 'immobile'); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(bass='B'), env.snapshot(frame))
        self.assertLessEqual(len(native.physical), 1)

    async def test_final_neutral_reset_must_be_verified(self):
        frame = both(); low(frame, 1, -.03); low(frame, 2, -.6)
        native = Native(frame, 'reset_failure'); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(bass='A'), env.snapshot(frame))
        self.assertEqual(len(native.physical), 1)

    async def test_changed_track_after_crossfader_input_is_not_claimed_verified(self):
        frame = both(); native = Native(frame, 'changed_track'); env = Rekordbox(native, library())
        with self.assertRaises(RuntimeError):
            await env.execute(decision(crossfader='B'), env.snapshot(frame))
        self.assertEqual(len(native.physical), 1)

    async def test_gesture_subchecks_cannot_override_failed_aggregate_verification(self):
        for selection in ({'crossfader':'B'}, {'bass':'B'}):
            with self.subTest(selection=selection):
                frame=both(); low(frame,2,-.6)
                native=Native(frame); original=native.call
                async def failed_aggregate(role,name,**params):
                    result=await original(role,name,**params)
                    if name=='mixGesture':
                        result['verified']=False
                        result['after']['mixer']['red_bar_aligned']=False
                    return result
                native.call=failed_aggregate; env=Rekordbox(native,library())
                with self.assertRaisesRegex(RuntimeError,'volledige mixbeweging'):
                    await env.execute(decision(**selection),env.snapshot(frame))
                self.assertEqual(len(native.physical),1)

    async def test_final_fresh_frame_must_still_be_aligned_and_both_playing(self):
        for changed in ('alignment','playback'):
            with self.subTest(changed=changed):
                frame=both(); native=Native(frame); original=native.call
                moved=False
                async def changed_after_gesture(role,name,**params):
                    nonlocal moved
                    if name=='observe' and moved:
                        if changed=='alignment':native.raw['mixer']['red_bar_aligned']=False
                        else:native.raw['playingIndicators']['deck2']=False
                    result=await original(role,name,**params)
                    if name=='mixGesture':moved=True
                    return result
                native.call=changed_after_gesture; env=Rekordbox(native,library())
                with self.assertRaisesRegex(RuntimeError,'na de mixbeweging'):
                    await env.execute(decision(crossfader='B'),env.snapshot(frame))
                self.assertEqual(len(native.physical),1)

    async def test_verified_title_after_load_does_not_prove_target_stayed_stopped(self):
        frame=both(); frame['playingIndicators']['deck2']=False
        frame['mixer']['crossfader_position']=0.
        native=Native(frame); original=native.call
        async def unexpected_autoplay(role,name,**params):
            result=await original(role,name,**params)
            if name=='loadChosenTrack':
                result['after']['playingIndicators']['deck'+str(params['deck'])]=True
            return result
        native.call=unexpected_autoplay; env=Rekordbox(native,library())
        with self.assertRaisesRegex(RuntimeError,'niet bevestigd geladen'):
            await env.execute(decision(transport='load_B',track_id=library()[2]['id']),env.snapshot(frame))
        self.assertEqual(len(native.physical),1)


if __name__ == '__main__': unittest.main()
