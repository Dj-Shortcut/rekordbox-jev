"""Pacing evidence and safety choices; no API, credentials or Rekordbox input."""
from copy import deepcopy
import unittest

from test_policy import library, loaded, raw, snapshot, response
from djjev import policy
from djjev.musical_timing import grid_hint
from djjev.runner import Runner


class MusicalTimingTests(unittest.TestCase):
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
        self.assertAlmostEqual(timing['preferred_overlap_seconds'],32*4*60/124)
        self.assertAlmostEqual(timing['seconds_until_preferred_launch_window'],240-48*4*60/124)
        self.assertFalse(timing['ending_needs_priority'])
        self.assertIsNone(timing['confirmed_audible_overlap_seconds'])
        # Style instructions do not secretly choose or remove a real Jev action.
        for transport in ('hold','play_B'):
            decision=policy.resolve(request,response(request,transport=transport))
            self.assertTrue(policy.applicable(decision,state))
            self.assertEqual(decision['transport'],transport)

    def test_late_window_and_urgent_end_do_not_wait_for_preferred_mix_length(self):
        for remaining,urgent in ((85,False),(25,True)):
            state=self.prepared(elapsed=300-remaining,remaining=remaining)
            request=policy.prepare(state)
            timing=request['state']['musical_timing']
            self.assertEqual(timing['seconds_until_preferred_launch_window'],0)
            self.assertEqual(timing['ending_needs_priority'],urgent)
            self.assertIn('play_B',request['questions']['transport']['criteria'])
        state=self.prepared(elapsed=297,remaining=3,both=True,cross=.5)
        request=policy.prepare(state,{'transition':self.anchor(state)})
        decision=policy.resolve(request,response(request,transport='mix',crossfader='B',bass='B',duration='beats2'))
        self.assertTrue(policy.applicable(decision,state,{'transition':self.anchor(state)}))

    def test_overlap_uses_time_not_hold_count_and_preserves_final_eq_cleanup(self):
        state=self.prepared(both=True,cross=.5)
        anchor=self.anchor(state)
        anchor['audible_mix_started_ns']=state['captured_ns']-20_000_000_000
        history={'transition':anchor,'actions':[{'verified':True,'decision':{'transport':'hold'}}]*100}
        timing=policy.prepare(state,history)['state']['musical_timing']
        self.assertEqual(timing['confirmed_audible_overlap_seconds'],20.)
        self.assertAlmostEqual(timing['seconds_to_preferred_overlap'],32*4*60/124-20)
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


if __name__=='__main__':unittest.main()
