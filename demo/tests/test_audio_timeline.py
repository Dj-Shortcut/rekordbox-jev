"""Cache/context contract fixtures; never call Jev or real native controls."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import audio_timeline as audio, policy
from djjev.environment import Rekordbox
from test_policy import library, loaded, raw, snapshot, response
from test_environment import Native, decision


def fixture(track, path, row_count=12):
    grid = audio.grid_definition(track)
    rows = []
    for i in range(row_count):
        rows.append({'start_seconds':grid['start_seconds']+i*4*grid['bar_seconds'],
            'end_seconds':grid['start_seconds']+(i+1)*4*grid['bar_seconds'],
            'start_bar':i*4, 'bars':4., 'energy_dbfs':-18.,
            'low_energy_dbfs_estimate':-40. if i<2 else -20.,
            'low_fraction':.005 if i<2 else .6,
            'onsets_per_beat':.25 if i<2 else 1., 'flux':.05 if i<2 else .15})
    return audio.cache_document(track,path,rows[-1]['end_seconds'],
        {'name':'essentia','version':'synthetic-fixture','sample_rate':44100,
         'frame_size':4096,'hop_size':512,'bass_band_hz':[30,250]}, rows)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=self.root/'cache'
        self.tracks=library();self.docs=[]
        for track in self.tracks[:2]:
            path=self.root/track['file'];path.write_bytes(b'fixture-audio')
            doc=fixture(track,path);audio.write_cache(doc,self.cache);self.docs.append(doc)
        self.store=audio.TimelineStore(self.tracks,self.cache,self.root)

    def deck(self, index=0, position=None):
        doc=self.docs[index]
        position=doc['grid']['start_seconds']+1 if position is None else position
        return {'track_id':self.tracks[index]['id'],'elapsed':position,
                'remaining':doc['duration_seconds']-position, 'bpm':124.}

    def state(self, aligned=True):
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer'].update(crossfader_position=.5,red_bar_aligned=aligned)
        state=snapshot(frame)
        for index,name in enumerate(('A','B')):state['decks'][name].update(self.deck(index))
        state['audio_windows']=self.store.snapshot_context(state)
        return state

    def test_cache_validates_audio_grid_schema_checksum_and_safe_identity(self):
        track=self.tracks[0];path=self.root/track['file']
        self.assertEqual(audio.load_cached(track,path,self.cache),self.docs[0])
        edited=deepcopy(track);edited['beatgrid'][0]['position_seconds']+=.1
        self.assertIsNone(audio.load_cached(edited,path,self.cache))
        stamp=path.stat();path.write_bytes(b'changed-audio')
        os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        self.assertIsNone(audio.load_cached(track,path,self.cache))  # same size/time, different hash
        target=self.cache/(track['id']+'.json')
        for bad in ('{}','null','{"schema_version":NaN}','{'):
            target.write_text(bad)
            self.assertIsNone(audio.load_cached(track,path,self.cache))
        self.assertIsNone(audio.load_cached({**track,'file':'../escape.mp3'},path,self.cache))

    def test_window_boundary_and_lookahead_do_not_reuse_current_window(self):
        grid=self.docs[0]['grid'];boundary=grid['start_seconds']+8*grid['bar_seconds']
        before=self.store.lookup(self.deck(position=boundary-.001))
        after=self.store.lookup(self.deck(position=boundary))
        self.assertEqual(before['current_window']['start_bar'],4)
        self.assertEqual(after['current_window']['start_bar'],8)
        self.assertEqual([v['horizon_bars'] for v in before['lookahead']],[8,16,32])
        self.assertEqual(before['lookahead'][0]['low_energy_dbfs_estimate'],-20)
        self.assertEqual(before['changes'][0]['in_bars'],0.)
        self.assertIn('low_band_rising',before['changes'][0]['observations'])
        self.assertFalse(before['first_drop']['drop_confirmed'])
        self.assertIsNone(before['first_drop']['candidate'])  # Bass rise alone is not a drop.
        self.assertEqual(before['source_grid'],self.docs[0]['grid'])

    def test_end_coverage_and_invalid_position_are_explicit(self):
        end=self.docs[0]['duration_seconds']
        near=self.store.lookup(self.deck(position=end-.2))
        self.assertEqual(near['lookahead'],[])
        for position in (-1,None,end,end+1):
            deck=self.deck();deck['elapsed']=position
            self.assertEqual(self.store.lookup(deck)['status'],'position_outside_analysis')
        self.assertEqual(self.store.lookup({'track_id':'missing'})['status'],'missing_analysis')

    def test_stopped_incoming_at_zero_has_measured_preview_without_invented_current_audio(self):
        deck=self.deck(position=0)
        preview=self.store.lookup(deck)
        self.assertEqual(preview['status'],'available')
        self.assertEqual(preview['position_region'],'before_first_downbeat')
        self.assertIsNone(preview['current_window'])
        self.assertEqual(preview['lookahead'][0]['start_seconds'],self.docs[0]['grid']['start_seconds'])
        self.assertEqual(preview['lookahead'][0]['coverage_bars'],8)
        self.assertTrue(all(c['at_seconds']>self.docs[0]['grid']['start_seconds'] for c in preview['changes']))
        state=self.state();state['mixer']['cross']=0.
        state['decks']['B'].update(self.deck(1,position=0),playing=False)
        state['audio_windows']=self.store.snapshot_context(state)
        request=policy.prepare(state)
        self.assertEqual(request['state']['audio_context']['roles']['incoming'],'B')
        self.assertIsNone(request['state']['audio_context']['decks']['B']['current_window'])

    def test_live_lookup_reads_no_files_or_dsp_and_context_is_bounded(self):
        with (patch.object(Path,'open',side_effect=AssertionError('live IO')),
              patch.object(Path,'stat',side_effect=AssertionError('live IO')),
              patch.object(audio,'source_contour',side_effect=AssertionError('live contour aggregation')),
              patch.object(audio,'aggregate',wraps=audio.aggregate) as summary):
            state=self.state()
            request=policy.prepare(state)
        self.assertLess(len(json.dumps(request['state']['audio_context']).encode()),14000)
        self.assertTrue(all(len(call.args[0])<=8 for call in summary.call_args_list))
        self.assertNotIn('windows',request['state']['audio_context']['decks']['A'])

    def test_contour_preserves_order_power_average_partial_coverage_and_cache_identity(self):
        original=deepcopy(self.docs[0])
        rows=deepcopy(original['windows'])
        for i,row in enumerate(rows):
            energy=(-25.,-8.,-18.)[i//4]
            row.update(energy_dbfs=energy,low_energy_dbfs_estimate=energy-6)
        rows[-1]['bars']=1.
        rows[-1]['end_seconds']=rows[-1]['start_seconds']+original['grid']['bar_seconds']
        overview=audio.source_contour(rows)
        self.assertEqual(overview['segment_bars'],16)
        self.assertEqual([s['energy_dbfs'] for s in overview['segments']],[-25.,-8.,-18.])
        self.assertEqual([s['start_bar'] for s in overview['segments']],[0,16,32])
        self.assertEqual([s['coverage_bars'] for s in overview['segments']],[16,16,13])
        self.assertAlmostEqual(overview['segments'][-1]['end_seconds'],rows[-1]['end_seconds'],places=3)
        mixed=audio.source_contour([{**rows[0],'energy_dbfs':-10.,'bars':1.},
                                    {**rows[1],'energy_dbfs':-20.,'bars':3.}])
        self.assertAlmostEqual(mixed['segments'][0]['energy_dbfs'],10*math.log10(.0325),places=4)
        self.assertEqual(self.docs[0],original)
        self.assertEqual(audio.load_cached(self.tracks[0],self.root/self.tracks[0]['file'],self.cache),original)
        self.assertEqual(set(overview['segments'][0]),{'start_seconds','end_seconds','start_bar',
            'coverage_bars','energy_dbfs','low_energy_dbfs_estimate','onsets_per_beat','flux'})

    def test_contour_position_follows_source_seek_and_never_claims_playback_history(self):
        grid=self.docs[0]['grid'];boundary=grid['start_seconds']+16*grid['bar_seconds']
        before=self.store.lookup(self.deck(position=boundary-.001))['source_contour']
        at=self.store.lookup(self.deck(position=boundary))['source_contour']
        later=self.store.lookup(self.deck(position=boundary+17*grid['bar_seconds']))['source_contour']
        sought=self.store.lookup(self.deck(position=boundary))['source_contour']
        initial=self.store.lookup(self.deck(position=0))['source_contour']
        self.assertEqual([c['position_segment_index'] for c in (before,at,later,sought,initial)],[0,1,2,1,None])
        self.assertEqual(before['segments'],later['segments'])
        self.assertIn('not proof they were played or heard',later['basis'])
        self.assertNotIn('peak_passed',json.dumps(later))
        later['segments'][0]['energy_dbfs']=123
        self.assertNotEqual(self.store.lookup(self.deck())['source_contour']['segments'][0]['energy_dbfs'],123)

    def test_long_track_contour_widens_in_sixteen_bar_units_without_losing_tail(self):
        for count, expected_bars, expected_segments in ((64,16,16),(65,32,9),(129,48,11),(900,240,15)):
            with self.subTest(windows=count):
                doc=fixture(self.tracks[0],self.root/self.tracks[0]['file'],row_count=count)
                contour=audio.source_contour(doc['windows'])
                self.assertEqual(contour['segment_bars'],expected_bars)
                self.assertEqual(len(contour['segments']),expected_segments)
                self.assertEqual(sum(s['coverage_bars'] for s in contour['segments']),4*count)
                self.assertAlmostEqual(contour['segments'][-1]['end_seconds'],doc['duration_seconds'],places=3)
                for previous,current in zip(contour['segments'],contour['segments'][1:]):
                    self.assertEqual(previous['end_seconds'],current['start_seconds'])

    def test_two_full_contours_stay_bounded_and_widened_segment_lookup_includes_tail(self):
        for count in (64,65):
            with self.subTest(windows=count):
                for index,track in enumerate(self.tracks[:2]):
                    doc=fixture(track,self.root/track['file'],row_count=count)
                    audio.write_cache(doc,self.cache);self.docs[index]=doc
                self.store=audio.TimelineStore(self.tracks,self.cache,self.root)
                context=policy.prepare(self.state())['state']['audio_context']
                # Actual two-track request14 measured10,208bytes; two full
                # 16-segment fixtures10,288.14KB allows varying numeric text
                # and local changes while keeping the source contour bounded.
                self.assertLess(len(json.dumps(context).encode()),14000)
                for deck in context['decks'].values():
                    self.assertLessEqual(len(deck['source_contour']['segments']),16)
                end=self.docs[0]['duration_seconds']
                tail=self.store.lookup(self.deck(position=end-.1))['source_contour']
                self.assertEqual(tail['position_segment_index'],len(tail['segments'])-1)
                if count==65:
                    self.assertEqual(tail['segment_bars'],32)
                    self.assertEqual(tail['segments'][-1]['coverage_bars'],4)

    def test_pitched_source_position_is_not_scaled_twice_and_bad_clocks_fall_back(self):
        deck=self.deck(position=20)
        original=self.store.lookup(deck)
        deck['bpm']=127.
        pitched=self.store.lookup(deck)
        self.assertEqual(pitched['current_window'],original['current_window'])
        self.assertEqual(pitched['position_seconds'],20)
        self.assertAlmostEqual(pitched['tempo_ratio'],127/124,places=4)
        deck['remaining']+=4
        self.assertEqual(self.store.lookup(deck)['status'],'source_clock_mismatch')
        deck=self.deck();deck['bpm']=150
        self.assertEqual(self.store.lookup(deck)['status'],'unsupported_tempo_ratio')

    def test_grid_alignment_requires_constant_consistent_four_four(self):
        track=deepcopy(self.tracks[0]);track['beatgrid'][0].update(position_seconds=.2,bpm=120,beat_in_bar=3)
        track['beatgrid'].append({'position_seconds':3.2,'bpm':120,'beat_in_bar':1,'meter':'4/4'})
        grid=audio.grid_definition(track)
        self.assertAlmostEqual(grid['start_seconds'],1.2)
        self.assertEqual(grid['bar_seconds'],2)
        track['beatgrid'][1]['position_seconds']+=.5
        with self.assertRaises(ValueError):audio.grid_definition(track)
        for grid in ([],[{'bpm':None}],None):
            with self.assertRaises(ValueError):audio.grid_definition({**track,'beatgrid':grid})

    def test_measurement_labels_are_thresholded_observations_not_actions(self):
        row=self.docs[0]['windows'][0]
        quiet={**row,'energy_dbfs':-120,'low_energy_dbfs_estimate':-120,'flux':0,'onsets_per_beat':0}
        tiny={**quiet,'energy_dbfs':-100,'low_energy_dbfs_estimate':-105}
        self.assertIsNone(audio.change(quiet,tiny,2,0))
        self.assertIsNone(audio.change(row,row,2,0))
        falling=audio.change(self.docs[0]['windows'][2],row,2,0)
        self.assertEqual(set(falling['observations']),{'low_band_falling','onset_activity_falling'})
        self.assertAlmostEqual(audio.aggregate([{**row,'energy_dbfs':-10},{**row,'energy_dbfs':-20}])['energy_dbfs'],-12.5964)
        weighted=audio.aggregate([{**row,'energy_dbfs':-40,'low_fraction':1},
                                  {**row,'energy_dbfs':-10,'low_fraction':.01}])
        self.assertAlmostEqual(weighted['low_fraction'],.01099,places=4)

    def test_missing_analysis_preserves_questions_actions_and_decisions(self):
        state=self.state();state.pop('audio_windows')
        before=policy.prepare(state)
        empty=audio.TimelineStore(self.tracks,self.root/'missing',self.root)
        self.assertIsNone(empty.snapshot_context(state))
        after=policy.prepare(state)
        self.assertEqual(before,after)
        self.assertEqual(policy.resolve(after,response(after,transport='hold'))['transport'],'hold')

    def test_two_deck_roles_and_actions_are_never_decided_by_audio(self):
        state=self.state()
        plain=deepcopy(state);plain.pop('audio_windows')
        before=policy.prepare(plain);request=policy.prepare(state)
        self.assertEqual({k:q['criteria'] for k,q in before['questions'].items()},
                         {k:q['criteria'] for k,q in request['questions'].items()})
        self.assertIsNone(request['state']['audio_context']['roles']['incoming'])
        anchor={'source':'verified_silent_successor_start','incoming':'B','outgoing':'A',
                'identities':{n:{'title':d['title'],'track_id':d['track_id']} for n,d in state['decks'].items()}}
        request=policy.prepare(state,{'transition':anchor})
        self.assertEqual(request['state']['audio_context']['roles']['incoming'],'B')
        for choice in ('hold','mix'):
            chosen=policy.resolve(request,response(request,transport=choice,crossfader='B',bass='B',duration='beats8'))
            self.assertEqual(chosen['transport'],choice)
            self.assertTrue(policy.applicable(chosen,state,{'transition':anchor}))

    def test_audio_answer_expires_on_new_bar_seek_or_missing_evidence(self):
        state=self.state();request=policy.prepare(state)
        chosen=policy.resolve(request,response(request,transport='mix',crossfader='B',bass='B',duration='beats4'))
        for delta in (2,-1):
            changed=deepcopy(state);changed['decks']['A']['elapsed']+=delta
            changed['decks']['A']['remaining']-=delta
            changed['audio_windows']=self.store.snapshot_context(changed)
            self.assertFalse(policy.applicable(chosen,changed))
        state.pop('audio_windows')
        self.assertFalse(policy.applicable(chosen,state))

    def test_same_bar_backward_seek_hidden_by_forward_playback_rejects_answer(self):
        state=self.state();state['captured_ns']=time.monotonic_ns()-1_000_000_000
        for index,name in enumerate(('A','B')):
            state['decks'][name].update(self.deck(index,self.docs[index]['grid']['start_seconds']+.2))
        state['audio_windows']=self.store.snapshot_context(state)
        request=policy.prepare(state)
        chosen=policy.resolve(request,response(request,transport='mix',crossfader='B',bass='B',duration='beats4'))
        changed=deepcopy(state);changed['captured_ns']+=1_000_000_000
        for name in ('A','B'):
            moved=.4 if name=='A' else 1.
            changed['decks'][name]['elapsed']+=moved;changed['decks'][name]['remaining']-=moved
        changed['audio_windows']=self.store.snapshot_context(changed)
        self.assertEqual(audio.evidence_token(state['audio_windows']),audio.evidence_token(changed['audio_windows']))
        self.assertFalse(policy.applicable(chosen,changed))


class AudioSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_optional_evidence_does_not_invalidate_physics_or_allow_unaligned_input(self):
        class Broken:
            def snapshot_context(self,state):raise ValueError('malformed analysis')
        frame=loaded(loaded(raw(),playing=True),'B',1,True)
        frame['mixer']['red_bar_aligned']=False
        native=Native(frame);env=Rekordbox(native,library(),audio_store=Broken())
        state=env.snapshot(frame)
        self.assertTrue(state['valid']);self.assertNotIn('audio_windows',state)
        with self.assertRaises(RuntimeError):await env.execute(decision(bass='B',crossfader='B'),state)
        self.assertEqual(native.physical,[])


if __name__=='__main__':unittest.main()
