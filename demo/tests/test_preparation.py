"""Early staging versus late entry. Fixtures are not live Jev/Rekordbox proof."""
import asyncio
from copy import deepcopy
import time
import unittest

from test_policy import library, loaded, raw, response, snapshot
import test_entry_timing as entry_fixtures
from test_set_contract import StrictNativeFixture
from djjev import policy
from djjev.environment import Rekordbox
from djjev.runner import Runner


class PreparationTests(unittest.TestCase):
    def test_choose_and_load_immediately_for_short_and_long_tracks(self):
        for duration in (180,420):
            for elapsed in (5,30,duration-60):
                with self.subTest(duration=duration,elapsed=elapsed):
                    state=snapshot(loaded(raw(),playing=True))
                    state['decks']['A'].update(elapsed=elapsed,remaining=duration-elapsed)
                    request=policy.prepare(state)
                    self.assertEqual(set(request['questions']['transport']['criteria']),{'load_B'})
                    self.assertTrue(request['state']['preparation']['required_now'])
                    self.assertFalse(request['state']['preparation']['starts_playback'])
                    selected=next(iter(request['questions']['next_track']['criteria']))
                    decision=policy.resolve(request,response(request,transport='load_B',next_track=selected))
                    self.assertEqual(decision['track_id'],selected)
                    self.assertTrue(policy.applicable(decision,state))

    def test_loaded_successor_gets_prepared_before_waiting_or_replacement(self):
        state=snapshot(loaded(loaded(raw(),playing=True),'B',1))
        request=policy.prepare(state)
        self.assertEqual(set(request['questions']['transport']['criteria']),{'prepare_B'})
        self.assertNotIn('next_track',request['questions'])
        self.assertFalse(state['decks']['B']['playing'])
        self.assertTrue(policy.applicable(policy.resolve(request,response(request,transport='prepare_B')),state))
        # Never reinterpret an invalid model HOLD as preparation.
        with self.assertRaises(ValueError):policy.resolve(request,response(request,transport='hold'))

    def test_prepared_seven_minute_track_waits_for_late_kick_return(self):
        state=entry_fixtures.EntryTimingTests().state(elapsed=20)
        request=policy.prepare(state)
        self.assertTrue(request['state']['preparation']['ready'])
        self.assertEqual(set(request['questions']['transport']['criteria']),{'hold','play_B'})
        self.assertEqual(request['state']['musical_timing']['entry_preference']['candidate']['seconds_until_start'],340)
        self.assertTrue(policy.applicable(policy.resolve(request,response(request,transport='hold')),state))
        self.assertFalse(policy.applicable(entry_fixtures.EntryTimingTests().decision(request),state))
        # Losing readiness invalidates the old HOLD, even with unchanged titles.
        changed=deepcopy(state);changed['decks']['B']['sync']=False
        self.assertFalse(policy.applicable(policy.resolve(request,response(request,transport='hold')),changed))

    def test_early_preparation_does_not_wait_for_kick_analysis(self):
        state=entry_fixtures.EntryTimingTests().state(elapsed=20)
        state['decks']['B'].update(sync=False,bass=0.)
        state['decks']['B']['eq_neutral']['low']=True
        request=policy.prepare(state)
        self.assertEqual(set(request['questions']),{'transport'})
        self.assertEqual(set(request['questions']['transport']['criteria']),{'prepare_B'})

    def test_previous_track_is_replaced_even_if_it_is_still_compatible(self):
        state=entry_fixtures.EntryTimingTests().state(elapsed=20)
        state['decks']['B']['bass']=0.
        state['decks']['B']['eq_neutral']['low']=True
        history={'transition':{'source':'verified_silent_successor_start','incoming':'A','outgoing':'B',
            'identities':{n:{'title':d['title'],'track_id':d['track_id']} for n,d in state['decks'].items()}}}
        request=policy.prepare(state,history)
        self.assertEqual(set(request['questions']['transport']['criteria']),{'load_B'})
        self.assertNotIn(state['decks']['B']['track_id'],request['questions']['next_track']['criteria'])

    def test_no_eligible_successor_does_not_invent_a_load_or_start(self):
        state=snapshot(loaded(raw(),playing=True))
        state['library']=[state['library'][0]]
        request=policy.prepare(state)
        self.assertEqual(set(request['questions']['transport']['criteria']),{'hold'})
        self.assertEqual(request['state']['preparation']['status'],'unavailable')
        self.assertFalse(request['state']['preparation']['ready'])


class PreparationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_runner_and_environment_stage_then_wait_without_starting_successor(self):
        native=StrictNativeFixture()
        native.loaded[1]=native.library[1]
        native.positions[1]=5.
        native.raw['mixer']['crossfader_position']=0.
        native._render()
        events=[]

        class WaitAfterReady:
            """Explicit fixture provider: chooses legal staging, then HOLD."""
            async def ask(self,request):
                await asyncio.sleep(.001)
                options=request['questions']['transport']['criteria']
                selection='hold' if 'hold' in options else next(iter(options))
                return response(request,transport=selection)
            async def close(self):pass

        env=Rekordbox(native,native.library)
        runner=Runner(env,policy,WaitAfterReady(),events.append,
                      tick_interval=.001,observe_interval=.002,decision_interval=.002)
        task=asyncio.create_task(runner.run())
        try:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                verified=[e for e in events if e['event']=='verified']
                if len([e for e in verified if e['decision']['transport']=='hold'])>=3:break
                await asyncio.sleep(.005)
            else:self.fail('Successor never reached the ready waiting state')
        finally:
            runner.request_stop()
            await task
        actions=[e['decision']['transport'] for e in events if e['event']=='verified']
        self.assertEqual(actions[:2],['load_B','prepare_B'])
        self.assertTrue(all(a=='hold' for a in actions[2:]))
        self.assertFalse(runner.blocked)
        self.assertFalse(native.playing(2))
        self.assertTrue(native.playing(1))
        self.assertLess(native.positions[1],30.)
        self.assertTrue(native.closed(2))
        self.assertEqual(sum(name=='loadChosenTrack' for name,_ in native.calls),1)
        self.assertFalse(any(name in ('setPlayback','launchAligned','mixGesture') for name,_ in native.calls))


if __name__=='__main__':unittest.main()
