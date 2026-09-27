"""Pacing evidence and safety choices; no API, credentials or Rekordbox input."""
import asyncio
from copy import deepcopy
import time
import unittest
from unittest import mock

from test_policy import library, loaded, raw, snapshot, response
from djjev import policy
from djjev.musical_timing import grid_hint
from djjev.runner import Runner


def use_late_monotonic_clock(test):
    """Anchor overlap fixtures in the past without needing a long host uptime.

    The fixtures stamp frames with the real monotonic clock and place the mix
    start up to 20 s earlier; a freshly booted runner can be younger than that.
    """
    real = time.monotonic_ns
    patcher = mock.patch('time.monotonic_ns', lambda: real() + 3_600_000_000_000)
    patcher.start()
    test.addCleanup(patcher.stop)


class MusicalTimingTests(unittest.TestCase):
    def setUp(self):
        use_late_monotonic_clock(self)
        # Runner creates asyncio primitives; Python 3.9 binds them to the
        # current loop at construction, so give synchronous tests one.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.addCleanup(self.loop.close)
        self.addCleanup(asyncio.set_event_loop, None)

    def prepared(self, *, elapsed=60, remaining=240, both=False, cross=0.):
        frame=loaded(loaded(raw(),playing=True),'B',1,both)
        frame['mixer'].update(crossfader_position=cross,red_bar_aligned=both)
        frame['mixer']['eq_position']['2']['low']=-.35
        frame['mixer']['eq_neutral']['2']['low']=False
        state=snapshot(frame)
        state['decks']['A'].update(elapsed=elapsed,remaining=remaining)
        return state

    def anchor(self, state):
        return {'source':'verified_silent_successor_start','incoming':'B','outgoing':'A',
                'identities':{n:{'title':d['title'],'track_id':d['track_id']}
                              for n,d in state['decks'].items()}}

    def test_prepared_early_successor_has_wait_context_but_jev_keeps_the_choice(self):
        state=self.prepared()
        request=policy.prepare(state)
        timing=request['state']['musical_timing']
        self.assertEqual(timing['lead_track_progress_fraction'],.2)
        self.assertEqual(timing['lead_track_duration_seconds'],300)
        self.assertAlmostEqual(timing['preferred_overlap_seconds'],64*4*60/124)
        self.assertAlmostEqual(timing['seconds_until_preferred_launch_window'],165)
        self.assertFalse(timing['ending_needs_priority'])
        self.assertIsNone(timing['confirmed_audible_overlap_seconds'])
        # Style instructions do not secretly choose or remove a real Jev action.
        for transport in ('hold','play_B'):
            decision=policy.resolve(request,response(request,transport=transport))
            self.assertTrue(policy.applicable(decision,state))
            self.assertEqual(decision['transport'],transport)

    def test_late_window_and_urgent_end_do_not_wait_for_preferred_mix_length(self):
        for remaining,urgent in ((75,False),(25,True)):
            state=self.prepared(elapsed=300-remaining,remaining=remaining)
            request=policy.prepare(state)
            timing=request['state']['musical_timing']
            self.assertEqual(timing['seconds_until_preferred_launch_window'],0)
            self.assertEqual(timing['ending_needs_priority'],urgent)
            self.assertIn('play_B',request['questions']['transport']['criteria'])
        state=self.prepared(elapsed=297,remaining=3,both=True,cross=.5)
        request=policy.prepare(state,{'transition':self.anchor(state)})
        decision=policy.resolve(request,response(request,transport='mix',crossfader='B',bass='hold',duration='beats2'))
        self.assertTrue(policy.applicable(decision,state,{'transition':self.anchor(state)}))

    def test_overlap_uses_time_not_hold_count_and_preserves_final_eq_cleanup(self):
        state=self.prepared(both=True,cross=.5)
        anchor=self.anchor(state)
        anchor['audible_mix_started_ns']=state['captured_ns']-20_000_000_000
        history={'transition':anchor,'actions':[{'verified':True,'decision':{'transport':'hold'}}]*100}
        timing=policy.prepare(state,history)['state']['musical_timing']
        self.assertEqual(timing['confirmed_audible_overlap_seconds'],20.)
        self.assertAlmostEqual(timing['seconds_to_preferred_overlap'],64*4*60/124-20)
        state['mixer']['cross']=1.
        request=policy.prepare(state,history)
        self.assertIn('reset_B',request['questions']['transport']['criteria'])
        self.assertEqual(request['state']['musical_timing']['lead_deck'],'B')

    def test_healthy_late_overlap_is_not_automatically_an_emergency(self):
        state=self.prepared(elapsed=260,remaining=40,both=True,cross=.5)
        anchor=self.anchor(state)
        anchor['audible_mix_started_ns']=state['captured_ns']-20_000_000_000
        timing=policy.prepare(state,{'transition':anchor})['state']['musical_timing']
        self.assertFalse(timing['ending_needs_priority'])
        self.assertEqual(timing['overlap_time_available_before_finish_seconds'],28)
        self.assertGreater(timing['seconds_to_preferred_overlap'],28)
        state['decks']['A']['remaining']=10
        self.assertTrue(policy.prepare(state,{'transition':anchor})['state']['musical_timing']['ending_needs_priority'])

    def test_live_stalled_center_finishes_then_stops_and_cleans_up(self):
        state=self.prepared(elapsed=282,remaining=25,both=True,cross=.5)
        history={'transition':self.anchor(state)}
        request=policy.prepare(state,history)
        self.assertTrue(request['state']['handoff_completion_required'])
        for key,choices in {'transport':{'mix'},'crossfader':{'B'},'bass':{'hold'},'mid':{'hold'},'high':{'hold'}}.items():
            self.assertEqual(set(request['questions'][key]['criteria']),choices)
        self.assertEqual(set(request['questions']['duration']['criteria']),{'beats2','beats4'})
        state['mixer']['cross']=1.
        request=policy.prepare(state,history)
        self.assertEqual(set(request['questions']['transport']['criteria']),{'reset_B'})
        state['decks']['B']['bass']=0.
        state['decks']['B']['eq_neutral']['low']=True
        request=policy.prepare(state,history)
        self.assertEqual(set(request['questions']['transport']['criteria']),{'stop_A'})

    def test_completion_margin_never_starts_a_silent_successor_or_invents_direction(self):
        for both,known in ((False,True),(True,False)):
            state=self.prepared(elapsed=282,remaining=25,both=both,cross=.5 if both else 0.)
            history={'transition':self.anchor(state)} if known else None
            request=policy.prepare(state,history)
            self.assertFalse(request['state']['handoff_completion_required'])

    def test_muted_start_does_not_start_overlap_timer_and_mix_timer_survives_holds(self):
        runner=Runner(None,None,None,[])
        before=self.prepared()
        playing=deepcopy(before);playing['decks']['B']['playing']=True
        def remember(control, old, new):
            runner._remember_verified({'decision':{'transport':control},'before_snapshot':old},
                                      {'snapshot':new})
        remember('play_B',before,playing)
        self.assertNotIn('audible_mix_started_ns',runner.transition)
        center=deepcopy(playing);center['mixer']['cross']=.5
        center['captured_ns']=playing['captured_ns']+1_000_000_000
        remember('mix',playing,center)
        since=runner.transition['audible_mix_started_ns']
        self.assertEqual(since,center['captured_ns'])
        later=deepcopy(center);later['captured_ns']+=20_000_000_000
        remember('hold',center,later)
        remember('mix',center,later)
        self.assertEqual(runner.transition['audible_mix_started_ns'],since)
        changed=deepcopy(later);changed['decks']['B']['track_id']='different'
        runner._invalidate_transition(changed)
        self.assertIsNone(runner.transition)

    def test_pause_route_closure_seek_and_realign_invalidate_overlap_not_direction(self):
        for interruption in ('pause','close','seek','align'):
            runner=Runner(None,None,None,[])
            state=self.prepared(both=True,cross=.5)
            runner.transition=self.anchor(state)
            runner.transition['audible_mix_started_ns']=state['captured_ns']-10_000_000_000
            runner._invalidate_transition(state)
            changed=deepcopy(state);changed['captured_ns']+=500_000_000
            if interruption=='pause':changed['decks']['B']['playing']=False
            if interruption=='close':changed['mixer']['cross']=0.
            if interruption=='seek':changed['decks']['B']['elapsed']=0.
            if interruption=='align':
                runner._remember_verified({'decision':{'transport':'align_B'},'before_snapshot':state},
                                          {'snapshot':changed})
            else:runner._invalidate_transition(changed)
            self.assertNotIn('audible_mix_started_ns',runner.transition,interruption)
            self.assertEqual(runner.transition['incoming'],'B')

    def test_grid_group_is_based_on_original_downbeat_and_explicitly_estimated(self):
        track=library()[0]
        track.update(bpm=120,beatgrid=[{'position_seconds':.2,'bpm':120,'beat_in_bar':3,'meter':'4/4'}])
        # First complete downbeat is at 1.2 s; 16 bars later is at 33.2 s.
        hint=grid_hint({'bpm':120,'elapsed':32.2},track)
        self.assertEqual(hint['bars_since_first_downbeat'],15.5)
        self.assertEqual(hint['bars_until_next_16_bar_group'],.5)
        self.assertTrue(hint['near_16_bar_group'])
        self.assertIn('not an observed phrase',hint['basis'])

    def test_unknown_or_changed_tempo_grid_does_not_invent_phrase_evidence(self):
        track=library()[0]
        for deck in ({'bpm':125,'elapsed':60},{'bpm':124,'elapsed':None}):
            self.assertIsNone(grid_hint(deck,track))
        track['beatgrid'].append({'position_seconds':40,'bpm':125,'beat_in_bar':1,'meter':'4/4'})
        self.assertIsNone(grid_hint({'bpm':124,'elapsed':60},track))
        state=self.prepared();state['decks']['A'].update(bpm=None,elapsed=None,remaining=None)
        timing=policy.prepare(state)['state']['musical_timing']
        for key in ('grid_hint','seconds_until_preferred_launch_window','ending_needs_priority',
                    'lead_track_progress_fraction','confirmed_audible_overlap_seconds'):
            self.assertIsNone(timing[key])

    def test_misalignment_still_blocks_mix_and_gesture_bounds_are_unchanged(self):
        state=self.prepared(both=True,cross=.5);state['mixer']['aligned']=False
        self.assertNotIn('mix',policy.prepare(state)['questions']['transport']['criteria'])
        self.assertEqual(set(policy.DURATIONS.values()),{2,4,8,16})
        empty=policy.prepare(snapshot())
        self.assertNotIn('duration',empty['questions'])
        self.assertIn('load_A',empty['questions']['transport']['criteria'])

    def test_track_duration_changes_soft_overlap_preference_not_jev_choices(self):
        for duration,preferred,shorter in ((180,32,16),(240,32,16),(300,64,32),(420,64,32)):
            with self.subTest(duration=duration):
                state=self.prepared(elapsed=15,remaining=duration-15)
                request=policy.prepare(state);timing=request['state']['musical_timing']
                self.assertEqual(timing['preferred_overlap_bars'],preferred)
                self.assertEqual(timing['shorter_overlap_bars'],shorter)
                self.assertAlmostEqual(timing['preferred_overlap_seconds'],preferred*240/124)
                self.assertGreater(timing['seconds_until_preferred_launch_window'],0)
                for choice in ('hold','play_B'):
                    decision=policy.resolve(request,response(request,transport=choice))
                    self.assertEqual(decision['transport'],choice)
                    self.assertTrue(policy.applicable(decision,state))

    def test_opening_development_and_long_blends_are_in_the_actual_transport_question(self):
        state=self.prepared(elapsed=14.5,remaining=357.8)
        request=policy.prepare(state)
        question=request['questions']['transport']
        self.assertIn('especially the opening track',question['instructions'])
        self.assertIn('Repeated HOLD',question['instructions'])
        self.assertIn('64 bars',question['instructions'])
        self.assertIn('readiness alone is not a reason',question['criteria']['play_B'])
        self.assertFalse(request['state']['musical_timing']['ending_needs_priority'])
        self.assertNotIn('peak_passed',request['state'])

    def test_audio_guidance_cannot_demote_development_to_a_fallback(self):
        state=self.prepared(elapsed=14.5,remaining=357.8)
        state['audio_windows']={name:{'status':'available','track_id':deck['track_id'],
            'analysis_id':'fixture','position_seconds':deck['elapsed'],'position_bar':1.,'tempo_ratio':1.}
            for name,deck in state['decks'].items()}
        request=policy.prepare(state)
        self.assertIn('not to override it',request['state']['goal'])
        self.assertIn('No RMS maximum',request['state']['goal'])
        self.assertIn('user intent',request['questions']['transport']['instructions'])
        self.assertNotIn('ahead of generic',request['questions']['transport']['instructions'])
        self.assertNotIn('late-window/center-first preferences',request['state']['goal'])

    def test_live_4_to_the_floor_early_launch_remains_before_style_window(self):
        state=self.prepared(elapsed=145.2,remaining=155.2)
        state['decks']['A']['bpm']=123
        timing=policy.prepare(state)['state']['musical_timing']
        self.assertAlmostEqual(timing['launch_window_remaining_seconds'],75.1)
        self.assertAlmostEqual(timing['seconds_until_preferred_launch_window'],80.1)
        self.assertFalse(timing['ending_needs_priority'])
        state['decks']['A'].update(elapsed=225.3,remaining=75.1)
        self.assertAlmostEqual(policy.prepare(state)['state']['musical_timing']['seconds_until_preferred_launch_window'],0)

    def test_grid_boundary_does_not_open_early_launch_window(self):
        state=self.prepared(elapsed=.1+64*240/124,remaining=300-(.1+64*240/124))
        request=policy.prepare(state);timing=request['state']['musical_timing']
        self.assertTrue(timing['grid_hint']['near_16_bar_group'])
        self.assertGreater(timing['seconds_until_preferred_launch_window'],90)
        self.assertIn('shorten the overlap rather than starting earlier',request['questions']['transport']['instructions'])

    def test_later_entry_preserves_continuity_and_unknown_duration(self):
        for duration in (180,300,420,900):
            state=self.prepared(elapsed=duration*.5,remaining=duration*.5)
            timing=policy.prepare(state)['state']['musical_timing']
            self.assertGreater(timing['seconds_until_preferred_launch_window'],0)
            self.assertLessEqual(timing['launch_window_remaining_seconds'],duration*.25)
        state=self.prepared(elapsed=295,remaining=5)
        request=policy.prepare(state)
        self.assertTrue(request['state']['musical_timing']['ending_needs_priority'])
        self.assertIn('play_B',request['questions']['transport']['criteria'])
        state=self.prepared();state['decks']['A']['elapsed']=None
        timing=policy.prepare(state)['state']['musical_timing']
        self.assertIsNone(timing['lead_track_progress_fraction'])
        self.assertIsNotNone(timing['seconds_until_preferred_launch_window'])

    def test_shorter_track_on_either_side_sets_shorter_overlap(self):
        for outgoing, incoming, bars in ((420,180,32),(180,420,32),(420,360,64)):
            state=self.prepared(elapsed=60,remaining=outgoing-60)
            state['decks']['B'].update(elapsed=0,remaining=incoming)
            timing=policy.prepare(state)['state']['musical_timing']
            self.assertEqual(timing['incoming_track_duration_seconds'],incoming)
            self.assertEqual(timing['preferred_overlap_bars'],bars)

    def test_incoming_clock_also_limits_blend_and_urgency(self):
        state=self.prepared(elapsed=240,remaining=60,both=True,cross=.5)
        state['decks']['B'].update(elapsed=160,remaining=20)
        anchor=self.anchor(state)
        anchor['audible_mix_started_ns']=state['captured_ns']-20_000_000_000
        timing=policy.prepare(state,{'transition':anchor})['state']['musical_timing']
        self.assertEqual(timing['overlap_time_available_before_finish_seconds'],8)
        self.assertEqual(timing['suggested_remaining_overlap_seconds'],8)
        self.assertFalse(timing['ending_needs_priority'])
        state['decks']['B']['remaining']=10
        timing=policy.prepare(state,{'transition':anchor})['state']['musical_timing']
        self.assertTrue(timing['ending_needs_priority'])
        self.assertEqual(timing['suggested_remaining_overlap_seconds'],0)

    def test_stopped_successor_clock_does_not_create_an_urgent_deadline(self):
        state=self.prepared(elapsed=10,remaining=410)
        state['decks']['B'].update(elapsed=170,remaining=10,playing=False)
        timing=policy.prepare(state)['state']['musical_timing']
        self.assertFalse(timing['ending_needs_priority'])
        state=self.prepared(both=True,cross=1)
        timing=policy.prepare(state,{'transition':self.anchor(state)})['state']['musical_timing']
        self.assertIsNone(timing['incoming_deck'], 'The outgoing deck after handoff is not a new successor')


if __name__=='__main__':unittest.main()
