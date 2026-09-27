"""Reproduce the partial live mixer reply, without sending physical input."""
import asyncio
from copy import deepcopy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from djjev.recovery import MixReadbackIncomplete, mixer_state, reconcileable_mix, control_state, same_control_state
from djjev.environment import Rekordbox
from djjev.runner import Runner
from test_environment import both, low, Native, decision
from test_policy import library, snapshot
from test_runner import until

TITLES={'A':'One','B':'Two'}

def incomplete(after):
    return {'verified':False,'dispatched':True,'commandsSent':True,
        'stepsCompleted':2,'stepsRequested':2,'after':deepcopy(after),
        'verification':{'aligned':True,'playing':{'1':True,'2':True},
            'actualTitles':{'1':'One','2':'Two'},'expectedTitles':{'1':'One','2':'Two'},
            'reasons':['crossfader_not_at_target','bass_direction_not_confirmed']}}

class PartialNative(Native):
    async def call(self,role,name,**params):
        if name=='mixGesture':
            self.calls.append((role,name,deepcopy(params)))
            low(self.raw,1,-.04)  # The failed live gesture changed only one control.
            return incomplete(self.raw)
        return await super().call(role,name,**params)

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_empty_deck_recovery_does_not_require_nonexistent_beat_alignment(self):
        state = snapshot()
        state['folder'] = '26'
        state['mixer']['aligned'] = None
        state['decks']['B'].update(title='Not Loaded.',track_id=None,playing=False,bpm=None)
        key = control_state(state)
        self.assertIsNotNone(key)
        self.assertTrue(same_control_state(key,control_state(deepcopy(state))))
        self.assertIsNone(mixer_state(state,{'A':state['decks']['A']['title'],'B':'Not Loaded.'}))
        state['folder'] = ''
        self.assertIsNone(control_state(state))
        state['folder'] = '26'
        state['decks']['A']['playing'] = state['decks']['B']['playing'] = True
        self.assertIsNone(control_state(state))

    async def test_partial_mix_exits_bundle_without_more_controls(self):
        frame=both();low(frame,2,-.3)
        native=PartialNative(frame);env=Rekordbox(native,library())
        with self.assertRaises(MixReadbackIncomplete) as caught:
            await env.execute(decision(bass='B_gentle',crossfader='center'),env.snapshot(frame))
        self.assertEqual([name for _,name,_ in native.physical],['mixGesture'])
        self.assertAlmostEqual(caught.exception.snapshot['decks']['A']['bass'],-.04)

    async def test_single_eq_unexpected_neutral_readback_reconciles_without_replay(self):
        frame=both();low(frame,1,-.0866)
        native=Native(frame);original=native.call
        async def snap_neutral(role,name,**params):
            result=await original(role,name,**params)
            if name=='eq':
                low(native.raw,params['deck'],0.)
                result.update(commandsSent=True,after=deepcopy(native.raw))
            return result
        native.call=snap_neutral;env=Rekordbox(native,library())
        with self.assertRaises(MixReadbackIncomplete) as caught:
            await env.execute(decision(bass='B'),env.snapshot(frame))
        self.assertEqual([name for _,name,_ in native.physical],['eq'])
        self.assertEqual(caught.exception.snapshot['decks']['A']['bass'],0.)

    async def test_long_eq_bundle_yields_to_completion_margin_after_one_returned_stroke(self):
        frame=both();frame['decks'][0]['metadata']='Artist 124.00 Am -00:35.0 04:25.0'
        native=Native(frame);original=native.call
        async def clock_advance(role,name,**params):
            result=await original(role,name,**params)
            if name=='eq':
                native.raw['decks'][0]['metadata']='Artist 124.00 Am -00:29.0 04:31.0'
                result['after']=deepcopy(native.raw)
            return result
        native.call=clock_advance;env=Rekordbox(native,library())
        with self.assertRaises(MixReadbackIncomplete) as caught:
            await env.execute(decision(bass='B',completion_outgoing='A'),env.snapshot(frame))
        self.assertEqual(caught.exception.reasons,['completion_margin_reached'])
        self.assertEqual([name for _,name,_ in native.physical],['eq'])

    def test_only_complete_readable_native_replies_allow_reconciliation(self):
        frame=both();state=snapshot(frame);reply=incomplete(frame)
        self.assertTrue(reconcileable_mix(reply,state,TITLES))
        for field,value in [('verified',True),('dispatched',1),('commandsSent',None),
                            ('stepsCompleted',1),('stepsRequested',True)]:
            changed=deepcopy(reply);changed[field]=value
            self.assertFalse(reconcileable_mix(changed,state,TITLES),(field,value))
        for field,value in [('aligned',None),('playing',{'1':True,'2':False}),
                            ('actualTitles',{'1':'Different','2':'Two'}),
                            ('reasons',['red_markers_misaligned']),('reasons',[])]:
            changed=deepcopy(reply);changed['verification'][field]=value
            self.assertFalse(reconcileable_mix(changed,state,TITLES),(field,value))
        for mutate in [lambda s:s['decks']['A'].update(playing=False),
                       lambda s:s['mixer'].update(cross=None),
                       lambda s:s['mixer'].update(aligned=False),
                       lambda s:s['decks']['B']['eq_position'].update(low=None),
                       lambda s:s['decks']['B']['eq_position'].update(low=.2)]:
            changed=deepcopy(state);mutate(changed)
            self.assertIsNone(mixer_state(changed,TITLES))

    async def exercise(self, *, forever=False, change_after=False, stop_during=False):
        class Env:
            def __init__(self):
                self.frame=snapshot(both());self.reads=0;self.executions=[];self.failed_at=None
            async def observe(self):
                self.reads+=1;await asyncio.sleep(.001)
                value=deepcopy(self.frame);value['version']=self.reads
                if change_after and self.failed_at is not None:value['decks']['A']['title']='Different'
                return value
            async def execute(self,d,s):
                self.executions.append((deepcopy(d),self.reads))
                if len(self.executions)==1 or forever:
                    self.frame['mixer']['cross']=.2
                    self.failed_at=self.reads
                    raise MixReadbackIncomplete(self.frame,['crossfader_not_at_target'])
                return {'verified':True,'dispatched':True,'snapshot':deepcopy(self.frame)}
            async def stop(self):pass
        class Policy:
            def prepare(self,s,h,b):return {'state':s,'questions':{'transport':{}}}
            def resolve(self,q,r):return decision(transport='mix',crossfader='center',snapshot_version=q['state']['version'])
            def applicable(self,d,s,h):return True
        class Client:
            def __init__(self):self.requests=[]
            async def ask(self,q):self.requests.append(deepcopy(q));await asyncio.sleep(.002);return {}
            async def close(self):pass
        env=Env();client=Client();events=[]
        runner=Runner(env,Policy(),client,events.append,tick_interval=.001,observe_interval=.001,decision_interval=.001)
        task=asyncio.create_task(runner.run())
        try:
            if stop_during:
                await until(lambda:runner._recovery is not None)
            elif forever or change_after:
                await until(lambda:runner.blocked)
            else:
                await until(lambda:runner.verified_actions>=1)
        finally:
            await runner.stop();await task
        return runner,env,client,events

    async def test_new_answer_after_two_new_readbacks_not_replayed_input(self):
        runner,env,client,events=await self.exercise()
        self.assertFalse(runner.blocked)
        self.assertGreaterEqual(env.executions[1][1]-env.executions[0][1],2)
        self.assertGreater(env.executions[1][0]['snapshot_version'],env.executions[0][0]['snapshot_version'])
        self.assertTrue(any(q['state']['mixer']['cross']==.2 for q in client.requests))
        self.assertEqual(sum(e['event']=='execution_reconciled' for e in events),1)
        self.assertFalse(any(e['event']=='verified' and e.get('request_id')==1 for e in events))

    async def test_repeated_nonresponse_is_bounded(self):
        runner,env,_,events=await self.exercise(forever=True)
        self.assertTrue(runner.blocked);self.assertEqual(len(env.executions),3)
        self.assertEqual(sum(e['event']=='execution_reconciled' for e in events),2)

    async def test_changed_tracks_and_stop_cancel_recovery(self):
        runner,env,_,_=await self.exercise(change_after=True)
        self.assertTrue(runner.blocked);self.assertEqual(len(env.executions),1)
        _,env,_,_=await self.exercise(stop_during=True)
        self.assertEqual(len(env.executions),1)
