"""No API/native calls: independent decision questions and real-state boundaries."""
import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import policy, state


def library():
    return [{'id':state.track_id(name+'.mp3'),'file':name+'.mp3','title':name,'artist':'Artist',
             'folder':'26','bpm':124,'key':key,'duration':300,
             'beatgrid':[{'position_seconds':.1,'bpm':124,'beat_in_bar':1,'meter':'4/4'}]}
            for name,key in [('One','Am'),('Two','Em'),('Three','C'),('Four','Bbm')]]


def raw():
    return {'layoutCalibrated':True,'sampledAtMonotonicNS':time.monotonic_ns(),
        'browserHeading':'26','decks':[{'deck':n,'title':'Not Loaded','metadata':'','displayedBPM':''} for n in (1,2)],
        'playingIndicators':{'deck1':False,'deck2':False},'faders':{'deck1':1,'deck2':1},
        'mixer':{'crossfader_position':.5,'deck_assignments':{'1':'left','2':'right'},'red_bar_aligned':False,
            'eq_neutral':{str(n):{b:True for b in state.BANDS} for n in (1,2)},
            'eq_position':{str(n):{b:0 for b in state.BANDS} for n in (1,2)},
            'beat_sync_lit':{'1':False,'2':True},'master_lit':{'1':True,'2':False}}}


def loaded(frame, deck='A', index=0, playing=False, ended=False):
    n=1 if deck=='A' else 2; track=library()[index]
    frame['decks'][n-1].update(title=track['title'],displayedBPM='124.00 0.0%',
        metadata=f"Artist 124.00 {track['key']} -{'00:00.0 05:00.0' if ended else '04:00.0 01:00.0'}")
    frame['playingIndicators']['deck'+str(n)]=playing
    return frame


def snapshot(frame=None, tracks=None):
    return state.normalize(raw() if frame is None else frame,tracks or library(),1)


def response(request, **selected):
    answers={}
    for name,q in request['questions'].items():
        choice=selected.get(name,next(iter(q['criteria'])))
        answers[name]={'type':'choice','choice':choice,'confidence':1,
            'probabilities':{k:int(k==choice) for k in q['criteria']}}
    return {'model':'jev-test','answers':answers}


class PolicyTests(unittest.TestCase):
    def test_silent_start_asks_actions_and_track_not_mix_duration(self):
        request=policy.prepare(snapshot(),[],False)
        self.assertEqual(set(request),{'model','state','questions'})
        self.assertEqual(set(request['questions']),{'transport','next_track'})
        self.assertEqual(set(request['questions']['transport']['criteria']),{'hold','load_A','load_B'})
        chosen=library()[2]['id']
        decision=policy.resolve(request,response(request,transport='load_B',next_track=chosen))
        self.assertEqual(decision['track_id'],chosen)
        self.assertEqual(decision['transport'],'load_B')
        self.assertEqual(decision['crossfader'],'hold')
        self.assertEqual(decision['duration_beats'],0)

    def test_playing_and_recent_tracks_excluded_and_key_tempo_are_used(self):
        s=snapshot(loaded(raw(),playing=True))
        request=policy.prepare(s,[{'verified':True,'decision':{'transport':'load_B','track_id':library()[1]['id']},'time':1}],False)
        self.assertEqual(set(request['questions']['next_track']['criteria']),{library()[2]['id']})
        self.assertNotIn('answers',str(request['state']['last_actions']))
        self.assertNotIn('play_A',request['questions']['transport']['criteria'])

    def test_two_ended_decks_can_load_open_stopped_deck_but_not_play_end(self):
        frame=loaded(loaded(raw(),ended=True),'B',1,ended=True)
        frame['mixer']['crossfader_position']=1
        request=policy.prepare(snapshot(frame),[],False)
        self.assertIn('load_B',request['questions']['transport']['criteria'])
        self.assertNotIn('play_B',request['questions']['transport']['criteria'])
        self.assertNotIn('play_A',request['questions']['transport']['criteria'])

    def test_lit_play_indicator_at_confirmed_end_offers_stop_without_inventing_stopped_state(self):
        frame=loaded(raw(),playing=True,ended=True)
        frame['decks'][0]['metadata']='Artist 124.00 Am -00:00.0 05:07.3'
        s=snapshot(frame)
        self.assertIs(s['decks']['A']['playing'],True)
        self.assertEqual(s['decks']['A']['elapsed'],307.3)
        self.assertEqual(s['decks']['A']['remaining'],0)
        request=policy.prepare(s,[],False)
        self.assertIn('stop_A',request['questions']['transport']['criteria'])
        self.assertIn('load_B',request['questions']['transport']['criteria'])
        self.assertNotIn('crossfader',request['questions'])
        # No compatibility reference to an ended track: Bbm remains eligible.
        self.assertIn(library()[3]['id'],request['questions']['next_track']['criteria'])
        decision=policy.resolve(request,response(request,transport='stop_A'))
        self.assertTrue(policy.applicable(decision,s,[]))
        self.assertIs(request['state']['decks']['A']['playing'],True)

    def test_incoming_is_never_prepared_or_launched_against_ended_playing_indicator(self):
        frame=loaded(loaded(raw(),playing=True,ended=True),'B',1)
        request=policy.prepare(snapshot(frame),[],False)
        self.assertIn('stop_A',request['questions']['transport']['criteria'])
        self.assertNotIn('prepare_B',request['questions']['transport']['criteria'])
        self.assertNotIn('play_B',request['questions']['transport']['criteria'])
        frame['playingIndicators']['deck1']=False
        request=policy.prepare(snapshot(frame),[],False)
        self.assertIn('play_B',request['questions']['transport']['criteria'])

    def test_nonzero_remaining_time_does_not_authorize_stopping_sole_audible_deck(self):
        frame=loaded(raw(),playing=True)
        frame['decks'][0]['metadata']='Artist 124.00 Am -00:00.2 05:07.1'
        request=policy.prepare(snapshot(frame),[],False)
        self.assertNotIn('stop_A',request['questions']['transport']['criteria'])
        frame=loaded(loaded(raw(),playing=True,ended=True),'B',1,True,True)
        frame['mixer']['red_bar_aligned']=True
        request=policy.prepare(snapshot(frame),[],False)
        self.assertTrue({'stop_A','stop_B'} <= set(request['questions']['transport']['criteria']))
        self.assertNotIn('crossfader',request['questions'])
        self.assertNotIn('bass',request['questions'])

    def test_ready_loaded_track_can_be_played_as_actual_jev_choice(self):
        s=snapshot(loaded(raw()))
        request=policy.prepare(s,[],False)
        decision=policy.resolve(request,response(request,transport='play_A'))
        self.assertTrue(policy.applicable(decision,s,[]))
        self.assertEqual(decision['transport'],'play_A')

    def test_ended_A_open_and_fresh_B_muted_stopped_can_open_set_without_waiting(self):
        frame=loaded(loaded(raw(),ended=True),'B',1)
        frame['mixer']['crossfader_position']=0.
        s=snapshot(frame);request=policy.prepare(s,[],False)
        options=request['questions']['transport']['criteria']
        self.assertIn('play_B',options)
        self.assertIn('hold',options)
        self.assertNotIn('play_A',options)
        self.assertNotIn('duration',request['questions'])
        self.assertEqual(request['state']['continuity']['prepared_silent_decks'],[])
        chosen=policy.resolve(request,response(request,transport='play_B'))
        self.assertTrue(policy.applicable(chosen,s,[]))
        self.assertEqual(chosen['transport'],'play_B')
        for field,value in (('channel',0.),('remaining',0.)):
            changed=copy.deepcopy(s);changed['decks']['B'][field]=value
            self.assertNotIn('play_B',policy.prepare(changed,[],False)['questions']['transport']['criteria'])
        s['decks']['B']['eq_neutral']['low']=False
        self.assertNotIn('play_B',policy.prepare(s,[],False)['questions']['transport']['criteria'])

    def test_both_playing_aligned_ask_fader_and_bass_independently_and_keep_both_answers(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer']['red_bar_aligned']=True
        s=snapshot(frame); request=policy.prepare(s,[],False)
        self.assertEqual(set(request['questions']),{'transport','crossfader','bass','duration'})
        decision=policy.resolve(request,response(request,transport='mix',crossfader='B',bass='B',duration='beats8'))
        self.assertEqual(decision['transport'],'mix')
        self.assertEqual((decision['crossfader'],decision['bass'],decision['duration_beats']),('B','B',8))
        self.assertTrue(policy.applicable(decision,s,[]))
        self.assertTrue(request['state']['continuity']['mixer_questions_available'])
        self.assertIn('Mixer questions are available now',request['questions']['transport']['instructions'])

    def test_prepared_successor_context_explains_deadline_and_real_choices_without_forcing_one(self):
        frame=loaded(loaded(raw(),playing=True),'B',1)
        frame['mixer']['crossfader_position']=0.
        frame['mixer']['eq_position']['2']['low']=-.35
        frame['mixer']['eq_neutral']['2']['low']=False
        history=[{'verified':True,'decision':{'transport':'hold','crossfader':'hold','bass':'hold'}} for _ in range(30)]
        menus=[]
        for remaining in (200.,4.3,.2):
            s=snapshot(frame);s['decks']['A']['remaining']=remaining
            request=policy.prepare(s,history,False);context=request['state']['continuity']
            self.assertEqual(context['audible_decks'],['A'])
            self.assertEqual(context['seconds_until_silence_if_unchanged'],remaining)
            self.assertEqual(context['prepared_silent_decks'],['B'])
            self.assertFalse(context['mixer_questions_available'])
            self.assertEqual(context['trailing_hold_decisions_in_retained_history'],30)
            self.assertEqual(request['state']['routes']['B'],{'muted':True,'playing_through_open_route':False,'ended':False})
            question=request['questions']['transport'];menus.append(set(question['criteria']))
            self.assertEqual(menus[-1],{'hold','load_B','reset_B','play_B'})
            self.assertNotIn('Mixer questions are available now',question['instructions'])
            self.assertNotIn('If mixing now, choose HOLD',question['instructions'])
            self.assertIn('seconds',request['state']['time_units'])
            self.assertIn('stays muted',question['criteria']['play_B'])
            self.assertIn('does not start a mix',question['criteria']['hold'])
            # Both waiting and starting remain genuine selectable Jev answers.
            for selected in ('hold','play_B'):
                choice=policy.resolve(request,response(request,transport=selected))
                self.assertEqual(choice['transport'],selected)
                self.assertTrue(policy.applicable(choice,s,history))
        self.assertTrue(all(menu==menus[0] for menu in menus))

    def test_continuity_countdown_uses_only_open_playing_routes_and_preserves_unknown_time(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer']['crossfader_position']=0.
        s=snapshot(frame);s['decks']['A']['remaining']=4.3
        self.assertEqual(policy.prepare(s,[],False)['state']['continuity']['seconds_until_silence_if_unchanged'],4.3)
        s['mixer']['cross']=.5
        self.assertEqual(policy.prepare(s,[],False)['state']['continuity']['seconds_until_silence_if_unchanged'],240.)
        s['decks']['B']['remaining']=None
        self.assertIsNone(policy.prepare(s,[],False)['state']['continuity']['seconds_until_silence_if_unchanged'])
        for deck in s['decks'].values():deck['remaining']=0.
        request=policy.prepare(s,[],False)
        self.assertEqual(request['state']['continuity']['seconds_until_silence_if_unchanged'],0.)
        self.assertTrue(all(d['playing'] for d in request['state']['decks'].values()))

    def test_hold_streak_counts_only_completed_consecutive_full_holds(self):
        def completed(**values):return {'verified':True,'decision':{'transport':'hold','crossfader':'hold','bass':'hold',**values}}
        history=[completed(),completed(bass='B'),completed(),{'verified':False,'decision':{'transport':'load_A'}},completed()]
        self.assertEqual(policy.prepare(snapshot(),history,False)['state']['continuity']['trailing_hold_decisions_in_retained_history'],2)

    def test_red_markers_or_bpm_mismatch_forbid_mix_questions(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer']['crossfader_position']=0
        for aligned,bpm in ((False,'124.00'),(True,'125.00'),(None,'124.00')):
            frame['mixer']['red_bar_aligned']=aligned
            frame['decks'][1]['displayedBPM']=bpm
            request=policy.prepare(snapshot(frame),[],False)
            self.assertNotIn('crossfader',request['questions'])
            self.assertNotIn('bass',request['questions'])
            self.assertNotIn('duration',request['questions'])
            self.assertNotIn('mix',request['questions']['transport']['criteria'])
            if bpm=='124.00':
                self.assertIn('align_B',request['questions']['transport']['criteria'])
            else:
                self.assertNotIn('align_B',request['questions']['transport']['criteria'])
                self.assertIn('stop_B',request['questions']['transport']['criteria'])

    def test_channel_mute_is_not_a_prepared_crossfader_route_or_playable_successor(self):
        for cross in (.5,0.):
            for playing in (False,True):
                with self.subTest(cross=cross,playing=playing):
                    frame=loaded(loaded(raw(),playing=True),'B',1,playing)
                    frame['mixer']['crossfader_position']=cross
                    frame['faders']['deck2']=0.
                    frame['mixer']['eq_position']['2']['low']=-.6
                    frame['mixer']['eq_neutral']['2']['low']=False
                    options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
                    self.assertFalse({'prepare_B','play_B','align_B'} & set(options))
                    self.assertIn('stop_B' if playing else 'load_B',options)

    def test_realign_is_not_offered_without_an_executable_launch(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer']['crossfader_position']=0.
        for field,value in (('bpm',None),('bpm',125.),('cue_offset',None),('sync',False)):
            with self.subTest(field=field):
                s=snapshot(frame);s['decks']['B'][field]=value
                options=policy.prepare(s,[],False)['questions']['transport']['criteria']
                self.assertNotIn('align_B',options)
                self.assertIn('stop_B',options)
        s=snapshot(frame);s['decks']['A']['master']=False
        self.assertNotIn('align_B',policy.prepare(s,[],False)['questions']['transport']['criteria'])

    def test_non_neutral_incoming_trim_high_mid_keep_preparation_available_before_play(self):
        for band in ('trim','high','mid'):
            for neutral in (False,None):
                with self.subTest(band=band,neutral=neutral):
                    frame=loaded(loaded(raw(),playing=True),'B',1)
                    frame['mixer']['crossfader_position']=0.
                    frame['mixer']['eq_neutral']['2']['low']=False
                    frame['mixer']['eq_position']['2']['low']=-.35
                    frame['mixer']['eq_neutral']['2'][band]=neutral
                    options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
                    self.assertNotIn('play_B',options)
                    self.assertIn('prepare_B',options)
                    frame['playingIndicators']['deck2']=True
                    options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
                    self.assertNotIn('align_B',options)
                    self.assertIn('stop_B',options)

    def test_ended_closed_successor_can_be_replaced_without_repeated_preparation(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,ended=True)
        frame['mixer']['crossfader_position']=0.
        options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
        self.assertIn('load_B',options)
        self.assertNotIn('prepare_B',options)
        self.assertNotIn('play_B',options)
        # An open, occupied stopped deck must first be closed before replacement.
        frame['mixer']['crossfader_position']=.5
        options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
        self.assertIn('prepare_B',options)
        self.assertNotIn('load_B',options)

    def test_incompatible_loaded_successor_requires_replacement_but_can_be_closed_first(self):
        frame=loaded(loaded(raw(),playing=True),'B',3)
        frame['mixer']['crossfader_position']=0.
        request=policy.prepare(snapshot(frame),[],False)
        options=request['questions']['transport']['criteria']
        self.assertIn('load_B',options)
        self.assertNotIn('prepare_B',options)
        self.assertNotIn('play_B',options)
        self.assertFalse(request['state']['continuity']['successor_compatible']['B'])
        frame['mixer']['eq_position']['2']['low']=-.35
        frame['mixer']['eq_neutral']['2']['low']=False
        self.assertNotIn('play_B',policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria'])
        frame['mixer']['crossfader_position']=.5
        options=policy.prepare(snapshot(frame),[],False)['questions']['transport']['criteria']
        self.assertIn('prepare_B',options)
        self.assertIn('incompatible',options['prepare_B'])
        self.assertNotIn('load_B',options)
        frame['playingIndicators']['deck2']=True
        frame['mixer'].update(crossfader_position=0.,red_bar_aligned=True)
        request=policy.prepare(snapshot(frame),[],False)
        self.assertNotIn('mix',request['questions']['transport']['criteria'])
        self.assertNotIn('crossfader',request['questions'])
        self.assertIn('stop_B',request['questions']['transport']['criteria'])

    def test_loaded_successor_tempo_range_uses_original_tempo_even_after_sync(self):
        frame=loaded(loaded(raw(),playing=True),'B',1)
        frame['mixer']['crossfader_position']=0.
        tracks=library();tracks[1]['bpm']=140.
        request=policy.prepare(snapshot(frame,tracks),[],False)
        self.assertFalse(request['state']['continuity']['successor_compatible']['B'])
        self.assertNotIn('prepare_B',request['questions']['transport']['criteria'])
        self.assertIn('load_B',request['questions']['transport']['criteria'])

    def test_non_mix_branches_ignore_speculative_mixer_answers_and_preserve_raw_choices(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer'].update(red_bar_aligned=True,crossfader_position=1)
        s=snapshot(frame);request=policy.prepare(s,[],False)
        for chosen in ('hold','stop_A','mix'):
            raw_answer=response(request,transport=chosen,crossfader='A',bass='B',duration='beats16')
            decision=policy.resolve(request,raw_answer)
            self.assertEqual(decision['answers'],raw_answer['answers'])
            self.assertEqual(decision['transport'],chosen)
            if chosen=='mix':
                self.assertEqual((decision['crossfader'],decision['bass'],decision['duration_beats']),('A','B',16))
            else:
                self.assertEqual((decision['crossfader'],decision['bass'],decision['duration_beats']),('hold','hold',0))
            self.assertTrue(policy.applicable(decision,s,[]))
        # Applicability rejects a fabricated HOLD branch carrying mixer input.
        decision['transport']='hold'
        self.assertFalse(policy.applicable(decision,s,[]))
        for question in ('crossfader','bass','duration'):
            self.assertIn('Assume transport MIX',request['questions'][question]['instructions'])

    def test_completed_handoff_keeps_cleanup_choices_without_forcing_them(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer'].update(red_bar_aligned=True,crossfader_position=1.)
        frame['mixer']['eq_position']['1']['low']=-.6
        frame['mixer']['eq_neutral']['1']['low']=False
        request=policy.prepare(snapshot(frame),[],False)
        options=request['questions']['transport']['criteria']
        self.assertTrue({'hold','mix','stop_A','reset_A'}<=set(options))
        self.assertIn('stop the muted previous track now',options['stop_A'])
        self.assertIn('reset it now before waiting',options['reset_A'])

    def test_incoming_cut_remains_explicit_cleanup_after_fader_handoff_and_outgoing_stop(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer'].update(red_bar_aligned=True,crossfader_position=1.)
        frame['mixer']['eq_position']['2']['low']=-.35
        frame['mixer']['eq_neutral']['2']['low']=False
        s=snapshot(frame)
        anchor={'source':'verified_silent_successor_start','incoming':'B','outgoing':'A',
                'identities':{n:{'title':d['title'],'track_id':d['track_id']} for n,d in s['decks'].items()}}
        history={'transition':anchor}
        for outgoing_playing in (True,False):
            s['decks']['A']['playing']=outgoing_playing
            request=policy.prepare(s,history,False);context=request['state']['transition']
            self.assertTrue(context['handoff_endpoint_reached'])
            self.assertFalse(context['incoming_eq_neutral'])
            self.assertEqual(context['incoming_non_neutral_bands'],['low'])
            self.assertEqual(request['state']['continuity']['audible_non_neutral_bands'],{'B':['low']})
            self.assertIn('current audible deck B',request['questions']['transport']['criteria']['reset_B'])
            chosen=policy.resolve(request,response(request,transport='reset_B'))
            self.assertEqual(chosen['transport'],'reset_B')
            self.assertTrue(policy.applicable(chosen,s,history))
            # The prompt supplies the rule; code does not force the reset choice.
            chosen=policy.resolve(request,response(request,transport='hold'))
            self.assertEqual(chosen['transport'],'hold')
        s['decks']['B']['eq_neutral']['low']=True;s['decks']['B']['bass']=0.
        request=policy.prepare(s,history,False)
        self.assertTrue(request['state']['transition']['incoming_eq_neutral'])
        self.assertEqual(request['state']['continuity']['audible_non_neutral_bands'],{'B':[]})
        self.assertNotIn('reset_B',request['questions']['transport']['criteria'])
        self.assertIn('hold',request['questions']['transport']['criteria'])

    def test_busy_has_no_irrelevant_track_or_mix_duration_question(self):
        request=policy.prepare(snapshot(),[],True)
        self.assertTrue(request['state']['busy'])
        self.assertEqual(request['questions'],{})

    def test_full_candidate_coverage_and_no_arbitrary_shortlist(self):
        tracks=[{**library()[0],'id':f't{i}','title':f'Track {i}','file':f'{i}.mp3'} for i in range(127)]
        request=policy.prepare(snapshot(tracks=tracks),[],False)
        self.assertEqual(len(request['questions']['next_track']['criteria']),127)

    def test_typed_validation_rejects_foreign_question_choice_nan_or_bad_confidence(self):
        request=policy.prepare(snapshot(),[],False)
        for field,value in [('choice','not-offered'),('confidence',True),('confidence',float('nan'))]:
            r=response(request);r['answers']['transport'][field]=value
            with self.assertRaises(ValueError):policy.resolve(request,r)
        r=response(request);r['answers']['foreign']={}
        with self.assertRaises(ValueError):policy.resolve(request,r)

    def test_answer_applicability_rechecks_identity_end_and_current_alignment(self):
        frame=loaded(raw());s=snapshot(frame)
        request=policy.prepare(s,[],False);decision=policy.resolve(request,response(request,transport='play_A'))
        ended=snapshot(loaded(raw(),ended=True))
        self.assertFalse(policy.applicable(decision,ended,[]))
        changed=snapshot(loaded(raw(),index=1))
        self.assertFalse(policy.applicable(decision,changed,[]))
        s['captured_ns']=time.monotonic_ns()-4_000_000_000
        self.assertFalse(policy.applicable(decision,s,[]))
        with self.assertRaises(ValueError):policy.prepare(s,[],False)

    def test_applicable_rejects_load_choice_whose_track_is_no_longer_a_candidate(self):
        s=snapshot();request=policy.prepare(s,[],False)
        chosen=library()[2]['id']
        decision=policy.resolve(request,response(request,transport='load_A',next_track=chosen))
        self.assertTrue(policy.applicable(decision,s,[]))
        # Same snapshot and expected titles, but a verified action elsewhere has
        # since recorded that track as recent: it is still load_A, just not it.
        later_history={'recent_tracks':[chosen]}
        self.assertIn('load_A',policy.prepare(s,later_history,False)['questions']['transport']['criteria'])
        self.assertFalse(policy.applicable(decision,s,later_history))

    def test_applicable_returns_false_instead_of_raising_on_malformed_decision(self):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer'].update(red_bar_aligned=True,crossfader_position=.5)
        s=snapshot(frame)
        request=policy.prepare(s,[],False)
        decision=policy.resolve(request,response(request,transport='mix',crossfader='A',bass='B',duration='beats8'))
        del decision['duration_beats']
        self.assertFalse(policy.applicable(decision,s,[]))

    def test_normalization_distinguishes_empty_unknown_and_eq_neutral(self):
        s=snapshot();self.assertTrue(s['valid']);self.assertIsNone(s['decks']['A']['track_id'])
        frame=raw();frame['decks'][0]['title']=''
        self.assertFalse(snapshot(frame)['valid'])
        frame=loaded(raw());frame['mixer']['eq_position']['1']['low']=-.03
        s=snapshot(frame);self.assertEqual(s['decks']['A']['bass'],0)
        self.assertEqual(s['decks']['A']['elapsed'],60)
        self.assertEqual(s['decks']['A']['remaining'],240)
        frame['mixer']['eq_neutral']['1']['low']=False
        self.assertEqual(snapshot(frame)['decks']['A']['bass'],-.03)

    def test_displayed_bpm_accepts_percent_pitch_before_or_after_tempo(self):
        for displayed in ('123.00', '0.0% 123.00', '123.00 0.0%',
                          '+1.5% 123.00', '123.00 -1.5%', '−1,5 % 123,00',
                          '  0.0%\n123.00  ', '123.00% 123.00'):
            with self.subTest(displayed=displayed):
                frame=loaded(raw());frame['decks'][0]['displayedBPM']=displayed
                self.assertEqual(snapshot(frame)['decks']['A']['bpm'],123)

    def test_displayed_bpm_does_not_use_pitch_or_ambiguous_numbers_as_tempo(self):
        for displayed in ('0.0%', '123.00%', '123.00 124.00', '123.00 0.0',
                          '0.0% 123.00 +1.0%', '123.00 BPM', '00', '', None):
            with self.subTest(displayed=displayed):
                frame=loaded(raw());frame['decks'][0]['displayedBPM']=displayed
                self.assertIsNone(snapshot(frame)['decks']['A']['bpm'])
        self.assertEqual(state.displayed_bpm('123.00% 124.00'),124)

    def test_raw_bpm_text_is_preserved_for_both_successful_and_failed_reads(self):
        for displayed, expected in (('0.0% 123.00',123),('123.00 ??',None),('',None),(None,None)):
            with self.subTest(displayed=displayed):
                frame=loaded(raw());frame['decks'][0]['displayedBPM']=displayed
                deck=snapshot(frame)['decks']['A']
                self.assertEqual(deck['bpm_text'],displayed)
                self.assertEqual(deck['bpm'],expected)

    def test_grid_is_downbeat_math_not_a_phrase_and_key_is_not_invented(self):
        tracks=library();tracks[0]['beatgrid'][0]['beat_in_bar']=3
        s=snapshot(loaded(raw()),tracks)
        self.assertAlmostEqual(s['decks']['A']['cue_offset'],.1+2*60/124)
        self.assertNotIn('phrase',s['decks']['A'])
        frame=loaded(raw());frame['decks'][0]['metadata']='Artist'
        s=snapshot(frame);self.assertIsNone(s['decks']['A']['key'])
        self.assertIsNone(s['decks']['A']['elapsed'])

    def test_read_library_scopes_real_files_and_ids_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'26';root.mkdir();(root/'valid.mp3').write_bytes(b'test')
            other=Path(tmp)/'elsewhere.mp3';other.write_bytes(b'test')
            export=Path(tmp)/'rekordbox.xml'
            export.write_text(f'''<DJ_PLAYLISTS><COLLECTION>
              <TRACK Name="Valid" Location="{(root/'valid.mp3').as_uri()}" AverageBpm="124" Tonality="Am" TotalTime="300"><TEMPO Inizio="0.1" Bpm="124" Battito="1" Metro="4/4"/></TRACK>
              <TRACK Name="Other" Location="{other.as_uri()}"/>
              <TRACK Name="Missing" Location="{(root/'missing.mp3').as_uri()}"/>
            </COLLECTION></DJ_PLAYLISTS>''')
            tracks=state.read_library(export,root)
            self.assertEqual(len(tracks),1)
            self.assertEqual(tracks[0]['id'],state.track_id('valid.mp3'))
            self.assertEqual(tracks[0]['folder'],'26')


if __name__=='__main__':unittest.main()
