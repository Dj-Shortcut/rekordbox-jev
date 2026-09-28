"""User's xxx____xxxxxx_____xxxxxx preference; no API or physical inputs."""
from copy import deepcopy
import unittest

import test_musical_timing as timing_fixtures
from test_policy import response
from djjev import policy
from djjev.entry_timing import activity_sections


def rows(pattern, width=8.):
    return [{'start_seconds':i*width, 'end_seconds':(i+1)*width,
             'start_bar':i*4, 'bars':4., 'energy_dbfs':-12 if x else -25,
             'low_energy_dbfs_estimate':-16 if x else -38, 'low_fraction':.4 if x else .01,
             'onsets_per_beat':1. if x else .1, 'flux':.1} for i,x in enumerate(pattern)]


class EntryTimingTests(unittest.TestCase):
    def setUp(self):
        timing_fixtures.use_late_monotonic_clock(self)

    def state(self, elapsed=320, duration=420, start=360, end=408):
        state=timing_fixtures.MusicalTimingTests().prepared(elapsed=elapsed,remaining=duration-elapsed)
        structure=activity_sections(rows([1]*8+[0]*4+[1]*12+[0]*4+[1]*8))
        structure['last_return'].update(start_seconds=start,end_seconds=end)
        state['audio_windows']={'A':{'status':'available','track_id':state['decks']['A']['track_id'],
            'analysis_id':'fixture','position_seconds':elapsed,'position_bar':elapsed/2,
            'tempo_ratio':1.,'entry_structure':structure},
            'B':{'status':'missing_analysis','track_id':state['decks']['B']['track_id']}}
        return state

    def decision(self, request, **choices):
        return policy.resolve(request,response(request,transport='play_B',kick_pattern='plausible',
            last_section='supported',post_peak='past',entry_fit='suitable',**choices))

    def test_ended_play_indicator_does_not_expire_a_stop_decision(self):
        state=self.state(elapsed=420)
        req=policy.prepare(state)
        decision=policy.resolve(req,response(req,transport='stop_A'))
        after=deepcopy(state)
        after['captured_ns']+=1_000_000_000
        with __import__('unittest.mock',fromlist=['patch']).patch('djjev.policy.bridge_ns',return_value=after['captured_ns']):
            self.assertTrue(policy.applicable(decision,after))
            after['decks']['A']['elapsed']-=2
            self.assertFalse(policy.applicable(decision,after))

    def test_final_group_selected_across_whole_track_not_first_return(self):
        result=activity_sections(rows([1]*8+[0]*4+[1]*12+[0]*4+[1]*8))
        self.assertEqual(len(result['groups']),3)
        self.assertEqual(result['last_return']['start_seconds'],224)
        self.assertIn('Not confirmed kicks',result['basis'])

    def test_short_last_burst_is_not_a_sustained_section(self):
        result=activity_sections(rows([1]*8+[0]*4+[1]*8+[0]*4+[1]*2))
        self.assertEqual(result['last_return']['start_seconds'],96)
        self.assertEqual(len(result['groups']),2)

    def test_no_return_from_continuous_track_silence_or_attackless_bass(self):
        for pattern in ([1]*30,[0]*30):
            self.assertIsNone(activity_sections(rows(pattern))['last_return'])
        bass=rows([1]*8+[0]*4+[1]*8)
        for r in bass:r['onsets_per_beat']=0
        self.assertIsNone(activity_sections(bass)['last_return'])

    def test_partial_tail_cannot_make_a_sixteen_bar_section(self):
        sample=rows([1]*8+[0]*4+[1]*4);sample[-1]['bars']=1.
        self.assertIsNone(activity_sections(sample)['last_return'])

    def test_only_an_onset_drop_without_bass_drop_is_not_enough(self):
        sample=rows([1]*8+[0]*4+[1]*8)
        for r in sample:r['low_energy_dbfs_estimate']=-16
        self.assertIsNone(activity_sections(sample)['last_return'])

    def test_seven_minute_track_waits_after_old_seventy_five_percent_window(self):
        state=self.state();req=policy.prepare(state);timing=req['state']['musical_timing']
        self.assertEqual(timing['seconds_until_fallback_launch_window'],0)
        self.assertEqual(timing['seconds_until_preferred_launch_window'],40)
        self.assertFalse(timing['ending_needs_priority'])
        self.assertFalse(policy.applicable(self.decision(req),state))
        self.assertIn('play_B',req['questions']['transport']['criteria'])
        self.assertTrue(policy.applicable(policy.resolve(req,response(req,transport='hold')),state))

    def test_at_entry_allow_start_and_shorten_to_section_budget(self):
        state=self.state(elapsed=360);req=policy.prepare(state)
        c=req['state']['musical_timing']['entry_preference']['candidate']
        self.assertEqual(c['position'],'within')
        self.assertEqual(c['suggested_overlap_seconds'],10.)
        self.assertTrue(c['requires_shorter_mix'])
        self.assertTrue(policy.applicable(self.decision(req),state))

    def test_incoming_three_minutes_keeps_short_preference(self):
        state=self.state();state['decks']['B'].update(elapsed=0,remaining=180)
        req=policy.prepare(state);timing=req['state']['musical_timing']
        self.assertEqual(timing['preferred_overlap_bars'],32)
        self.assertEqual(timing['entry_preference']['candidate']['suggested_overlap_seconds'],10.)

    def test_too_late_candidate_does_not_trap_the_track(self):
        state=self.state(elapsed=410,start=416,end=420);req=policy.prepare(state)
        timing=req['state']['musical_timing']
        self.assertFalse(timing['entry_preference']['candidate']['enough_time_for_short_mix'])
        self.assertTrue(timing['ending_needs_priority'])
        decision=policy.resolve(req,response(req,transport='play_B',post_peak='ahead',entry_fit='protect'))
        self.assertTrue(policy.applicable(decision,state))

    def test_conflicting_assessments_are_visible_and_not_silently_rewritten(self):
        state=self.state(elapsed=360);req=policy.prepare(state)
        for choices in ({'post_peak':'ahead'},{'entry_fit':'protect'}):
            d=policy.resolve(req,response(req,transport='play_B',**choices))
            self.assertEqual(d['transport'],'play_B')
            self.assertFalse(policy.applicable(d,state))
            self.assertEqual(d['answers'][next(iter(choices))]['choice'],next(iter(choices.values())))

    def test_unknown_assessment_uses_fallback_but_does_not_launch_early(self):
        for elapsed,allowed in ((100,False),(320,True)):
            state=self.state(elapsed=elapsed);req=policy.prepare(state)
            d=policy.resolve(req,response(req,transport='play_B',last_section='unknown',post_peak='unknown',entry_fit='unknown'))
            self.assertEqual(policy.applicable(d,state),allowed)

    def test_running_overlap_respects_the_end_of_the_candidate_section(self):
        state=self.state(elapsed=360)
        state['decks']['B']['playing']=True;state['mixer'].update(cross=.5,aligned=True)
        anchor=timing_fixtures.MusicalTimingTests().anchor(state)
        anchor['audible_mix_started_ns']=state['captured_ns']-5_000_000_000
        timing=policy.prepare(state,{'transition':anchor})['state']['musical_timing']
        self.assertEqual(timing['overlap_time_available_before_finish_seconds'],18)
        self.assertEqual(timing['suggested_remaining_overlap_seconds'],18)

    def test_pitch_converts_wait_and_budget_once_not_source_position(self):
        state=self.state();state['audio_windows']['A']['tempo_ratio']=1.04
        state['decks']['A']['bpm']*=1.04
        timing=policy.prepare(state)['state']['musical_timing'];c=timing['entry_preference']['candidate']
        self.assertEqual(c['start_seconds'],360)
        self.assertAlmostEqual(c['seconds_until_start'],40/1.04,places=3)
        self.assertAlmostEqual(c['overlap_budget_seconds'],48/1.04-30-8,places=3)

    def test_small_incoming_clock_limits_entry_budget(self):
        state=self.state();state['decks']['B'].update(elapsed=280,remaining=20)
        c=policy.prepare(state)['state']['musical_timing']['entry_preference']['candidate']
        self.assertEqual(c['overlap_budget_seconds'],0)

    def test_smalltown_boy_return_cannot_defer_entry_into_completion_margin(self):
        # Recorded request 490: final return at 169.485, end 200.21, track
        # length about 204.7. Old planning advertised ~19 s of usable blend.
        state=self.state(elapsed=160,duration=204.7,start=169.485,end=200.21)
        request=policy.prepare(state)
        timing=request['state']['musical_timing']
        candidate=timing['entry_preference']['candidate']
        self.assertEqual(candidate['overlap_budget_seconds'],0)
        self.assertFalse(candidate['enough_time_for_short_mix'])
        self.assertEqual(timing['seconds_until_preferred_launch_window'],0)
        self.assertTrue(timing['ending_needs_priority'])
        self.assertTrue(policy.applicable(self.decision(request),state))
        # This correction must not force an early start or invent an action.
        earlier=self.state(elapsed=100,duration=204.7,start=169.485,end=200.21)
        req=policy.prepare(earlier)
        self.assertFalse(req['state']['musical_timing']['ending_needs_priority'])
        self.assertFalse(policy.applicable(self.decision(req),earlier))
        self.assertIn('hold',req['questions']['transport']['criteria'])

    def test_recorded_two_faced_opener_has_blend_room_before_completion(self):
        # 2026-09-28: Two Faced (107.6 s, 122 BPM) had no detected return.
        # The old 26.9 s fallback started B with 24.8 s left and forced the
        # very first MIX straight to the endpoint, with incoming bass cut.
        state=self.state(elapsed=61.8,duration=107.6)
        for deck in state['decks'].values():deck['bpm']=122.
        state['audio_windows']['A']['entry_structure']={
            'status':'no_clear_return','last_return':None,'groups':[{
                'start_seconds':.281,'end_seconds':102.576,'start_bar':0,'bars':52.}]}
        req=policy.prepare(state);timing=req['state']['musical_timing']
        self.assertTrue(timing['fallback_budget_overrides_percentage'])
        self.assertAlmostEqual(timing['launch_window_remaining_seconds'],30+8+16*60/122)
        self.assertEqual(timing['seconds_until_preferred_launch_window'],0)
        self.assertTrue(timing['ending_needs_priority'])
        selected=policy.resolve(req,response(req,transport='play_B',kick_pattern='unclear',
            last_section='unknown',post_peak='unknown',entry_fit='protect',arrangement_fit='alternative'))
        self.assertTrue(policy.applicable(selected,state))
        # Even consuming the full eight-second launch reserve leaves the
        # center blend available, rather than forcing immediate completion.
        playing=deepcopy(state)
        playing['decks']['A'].update(elapsed=69.8,remaining=37.8)
        playing['audio_windows']['A']['position_seconds']=69.8
        playing['decks']['B'].update(playing=True,elapsed=8.,remaining=196.6)
        playing['mixer']['aligned']=True
        history={'transition':timing_fixtures.MusicalTimingTests().anchor(playing)}
        mix=policy.prepare(playing,history)
        self.assertFalse(mix['state']['handoff_completion_required'])
        self.assertIn('center',mix['questions']['crossfader']['criteria'])
        self.assertAlmostEqual(mix['state']['musical_timing']['overlap_time_available_before_finish_seconds'],7.8)

    def test_user_44_second_reference_is_not_vetoed_by_percentage_or_missing_return(self):
        # User listening reference, NOT a claim that our detector finds this
        # phrase. Exercise the choice gate: a supported alternative may pass;
        # unknown suitability must not become an automatic early launch.
        state=self.state(elapsed=44,duration=107.6)
        for deck in state['decks'].values():deck['bpm']=122.
        state['audio_windows']['A']['entry_structure']={
            'status':'no_clear_return','last_return':None,'groups':[{
                'start_seconds':.281,'end_seconds':102.576,'start_bar':0,'bars':52.}]}
        req=policy.prepare(state)
        self.assertGreater(req['state']['musical_timing']['seconds_until_fallback_launch_window'],0)
        self.assertFalse(req['state']['musical_timing']['ending_needs_priority'])
        choices=dict(transport='play_B',kick_pattern='unclear',last_section='unknown',
                     post_peak='unknown',arrangement_fit='alternative')
        alternate=policy.resolve(req,response(req,entry_fit='alternative',**choices))
        self.assertTrue(policy.applicable(alternate,state))
        self.assertIsNone(alternate.get('entry_target'))
        for assessment in ('unknown','protect','unsuitable'):
            decision=policy.resolve(req,response(req,entry_fit=assessment,**choices))
            self.assertFalse(policy.applicable(decision,state))

    def test_alternative_cannot_invent_evidence_or_override_an_exact_return_target(self):
        state=self.state(elapsed=100);req=policy.prepare(state)
        choices=dict(transport='play_B',entry_fit='alternative',arrangement_fit='alternative')
        d=policy.resolve(req,response(req,**choices))
        from djjev.entry_timing import launch_consistent
        timing=req['state']['musical_timing']
        self.assertFalse(launch_consistent({**d,'entry_target':{'id':'x2'}},timing))
        state['audio_windows']['A'].pop('entry_structure')
        req=policy.prepare(state)
        self.assertFalse(policy.applicable(policy.resolve(req,response(req,**choices)),state))

    def test_short_fallback_reserves_wall_time_at_faster_tempo_and_caps_at_file_length(self):
        for duration,ratio in ((107.6,1.06),(30.,1.)):
            state=self.state(elapsed=10,duration=duration)
            state['audio_windows']['A']['entry_structure']={'status':'no_clear_return','groups':[],'last_return':None}
            state['audio_windows']['A']['tempo_ratio']=ratio
            for deck in state['decks'].values():deck['bpm']=122*ratio
            timing=policy.prepare(state)['state']['musical_timing']
            minimum=(30+8+16*60/(122*ratio))*ratio
            self.assertAlmostEqual(timing['launch_window_remaining_seconds'],min(duration,minimum))

    def test_running_successor_does_not_pay_launch_reserve_twice(self):
        state=self.state(elapsed=360)
        state['decks']['B']['playing']=True
        state['mixer'].update(cross=.5,aligned=True)
        history={'transition':timing_fixtures.MusicalTimingTests().anchor(state)}
        candidate=policy.prepare(state,history)['state']['musical_timing']['entry_preference']['candidate']
        self.assertEqual(candidate['launch_alignment_reserve_seconds'],0)
        self.assertEqual(candidate['overlap_budget_seconds'],18)

    def test_after_candidate_and_missing_evidence_are_explicit(self):
        state=self.state(elapsed=410);req=policy.prepare(state)
        self.assertEqual(req['state']['musical_timing']['entry_preference']['status'],'candidate_passed')
        state['audio_windows']['A'].pop('entry_structure')
        req=policy.prepare(state)
        self.assertEqual(req['state']['musical_timing']['entry_preference']['status'],'missing_evidence')
        self.assertIsNone(req['state']['musical_timing']['entry_preference']['candidate'])

    def test_busy_or_overlap_does_not_ask_launch_assessments_again(self):
        state=self.state();self.assertEqual(policy.prepare(state,busy=True)['questions'],{})
        state['decks']['B']['playing']=True;state['mixer'].update(cross=.5,aligned=True)
        self.assertNotIn('last_section',policy.prepare(state)['questions'])

    def test_all_four_questions_are_real_independent_choices_with_uncertainty(self):
        req=policy.prepare(self.state())
        for key,unknown in (('kick_pattern','unclear'),('last_section','unknown'),('post_peak','unknown'),('entry_fit','unknown')):
            question=req['questions'][key]
            self.assertEqual(question['type'],'choice');self.assertIn(unknown,question['criteria'])
            self.assertIn('other answers in this call are unknown',question['instructions'])
        self.assertFalse(req['state']['musical_timing']['entry_preference']['climax_confirmed'])
        self.assertFalse(req['state']['musical_timing']['entry_preference']['phrase_confirmed'])


if __name__ == '__main__':unittest.main()
