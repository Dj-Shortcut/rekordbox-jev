"""Independent musical choices reach distinct physical bands; no real device or API."""
import unittest
from unittest.mock import patch
from test_environment import Native, both, decision
from test_policy import snapshot, response, library
from djjev import policy
from djjev.environment import Rekordbox, LocalPreDispatch


class CreativeEQTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_choices_move_the_selected_bands_and_leave_trim_and_fader(self):
        frame=both(); state=snapshot(frame); req=policy.prepare(state)
        chosen=policy.resolve(req,response(req,transport='mix',bass='B_gentle',mid='A_cut',high='B_soft'))
        self.assertTrue(policy.applicable(chosen,state))
        native=Native(frame); env=Rekordbox(native,library())
        result=await env.execute(chosen,env.snapshot(frame))
        self.assertTrue(result['verified'])
        final=result['snapshot']
        self.assertAlmostEqual(final['decks']['A']['bass'],-.24,delta=.07)
        self.assertAlmostEqual(final['decks']['A']['eq_position']['mid'],-.32,delta=.07)
        self.assertAlmostEqual(final['decks']['B']['eq_position']['high'],-.16,delta=.07)
        self.assertEqual(final['mixer']['cross'],state['mixer']['cross'])
        self.assertTrue(all(d['eq_neutral']['trim'] for d in final['decks'].values()))
        self.assertEqual({p['band'] for _,name,p in native.physical if name=='eq'}, {'low','mid','high'})
        req=policy.prepare(final)
        for band, choice in (('bass','B_gentle'),('mid','A_cut'),('high','B_soft')):
            self.assertNotIn(choice,req['questions'][band]['criteria'])
        repeated=await env.execute(chosen,final)
        self.assertTrue(repeated['verified']);self.assertFalse(repeated['dispatched'])

    async def test_tone_restore_recovers_neutral_without_reducing_other_bands(self):
        frame=both()
        frame['mixer']['eq_position']['1']['mid']=-.32
        frame['mixer']['eq_neutral']['1']['mid']=False
        native=Native(frame);env=Rekordbox(native,library())
        result=await env.execute(decision(mid='neutral'),env.snapshot(frame))
        self.assertTrue(result['verified'])
        self.assertTrue(all(d['eq_neutral']['mid'] for d in result['snapshot']['decks'].values()))
        self.assertTrue(all(p.get('band','mid')=='mid' for _,name,p in native.physical))
        self.assertEqual([p['bands'] for _,name,p in native.physical if name=='eqReset'],[['mid']])

    async def test_unknown_or_lost_alignment_blocks_tone_before_input(self):
        for defect in ('unknown','alignment'):
            frame=both()
            if defect=='unknown':
                frame['mixer']['eq_position']['1']['mid']=None
                frame['mixer']['eq_neutral']['1']['mid']=None
            else:frame['mixer']['red_bar_aligned']=False
            native=Native(frame);env=Rekordbox(native,library());state=env.snapshot(frame)
            req=policy.prepare(state)
            self.assertEqual(list(req['questions'].get('mid',{'criteria':{'hold':None}})['criteria']),['hold'])
            with self.assertRaises(LocalPreDispatch):
                await env.execute(decision(mid='A_cut'),state)
            self.assertEqual(native.physical,[])

    async def test_tone_answers_are_visible_but_ignored_unless_mix_is_selected(self):
        frame=both();state=snapshot(frame);req=policy.prepare(state)
        chosen=policy.resolve(req,response(req,transport='hold',mid='A_cut',high='B_soft'))
        self.assertEqual(chosen['answers']['mid']['choice'],'A_cut')
        self.assertEqual((chosen['mid'],chosen['high']),('hold','hold'))
        self.assertTrue(policy.applicable(chosen,state))
        native=Native(frame);env=Rekordbox(native,library())
        result=await env.execute(chosen,env.snapshot(frame))
        self.assertFalse(result['dispatched']);self.assertEqual(native.physical,[])

    async def test_unresponsive_tone_control_is_not_reported_successful(self):
        frame=both();native=Native(frame,mode='immobile');env=Rekordbox(native,library())
        with self.assertRaisesRegex(RuntimeError,'EQ-beweging niet bevestigd'):
            await env.execute(decision(high='A_soft'),env.snapshot(frame))
        self.assertEqual(len(native.physical),1)
