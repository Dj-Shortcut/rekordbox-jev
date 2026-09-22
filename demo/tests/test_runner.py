import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.runner import Runner
from djjev.environment import LocalPreDispatch
from djjev import policy as dj_policy
from test_policy import snapshot as dj_snapshot, loaded, raw, library


async def until(predicate, timeout=.7):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), timeout)


class Environment:
    def __init__(self):
        self.observations = self.executions = self.active = self.max_active = 0
        self.title = 'A'
        self.stopped = False
        self.gate = None
        self.result = {'verified': True, 'dispatched': True}
        self.dispatch_observations = []

    async def observe(self):
        self.observations += 1
        await asyncio.sleep(.001)
        return {'valid': True, 'version': self.title, 'title': self.title, 'observation': self.observations}

    async def execute(self, decision, snapshot):
        assert not self.stopped
        self.executions += 1
        self.dispatch_observations.append(snapshot['observation'])
        self.active += 1; self.max_active = max(self.max_active, self.active)
        try:
            if self.gate:
                await self.gate.wait()
            return deepcopy(self.result)
        finally:
            self.active -= 1

    async def stop(self):
        self.stopped = True


class Policy:
    def prepare(self, snapshot, history, busy):
        return {'model': 'jev-latest', 'state': {**snapshot, 'busy': busy},
                'questions': {'action': {'type': 'choice', 'criteria': {'go': 'act'}}}}

    def resolve(self, request, response):
        return {'expected_titles': request['state']['title'], 'choice': response['answers']['action']}

    def applicable(self, decision, snapshot, history):
        return decision['expected_titles'] == snapshot['title']


class Client:
    def __init__(self, delay=.01, gate=None):
        self.delay, self.gate = delay, gate
        self.active = self.max_active = 0
        self.requests = []
        self.closed = False

    async def ask(self, request):
        self.requests.append(deepcopy(request))
        self.active += 1; self.max_active = max(self.max_active, self.active)
        try:
            if self.gate:
                await self.gate.wait()
            await asyncio.sleep(self.delay)
            return {'model': 'fixture', 'answers': {'action': 'go'}, 'request_seconds': self.delay}
        finally:
            self.active -= 1

    async def close(self):
        self.closed = True


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def create(self, env=None, client=None):
        env, client = env or Environment(), client or Client()
        events = []
        runner = Runner(env, Policy(), client, events.append,
                        tick_interval=.001, observe_interval=.002, decision_interval=.002)
        return runner, env, client, events

    async def test_delayed_provider_does_not_block_observations_or_ui_ticks(self):
        gate = asyncio.Event()
        runner, env, client, events = self.create(client=Client(gate=gate))
        task = asyncio.create_task(runner.run())
        await until(lambda: env.observations >= 12)
        self.assertEqual(env.executions, 0)
        self.assertEqual(len(client.requests), 1)
        self.assertGreaterEqual(sum(e['event'] == 'tick' for e in events), 12)
        self.assertEqual(client.max_active, 1)
        await runner.stop(); await task
        self.assertTrue(env.stopped)

    async def test_busy_loading_does_not_pause_loop_or_queue_answers(self):
        env = Environment(); env.gate = asyncio.Event()
        runner, env, client, events = self.create(env=env, client=Client(delay=.003))
        task = asyncio.create_task(runner.run())
        await until(lambda: env.executions == 1)
        first_count = env.observations
        await until(lambda: env.observations >= first_count+12)
        self.assertEqual(env.executions, 1)
        self.assertTrue(any(r['state']['busy'] for r in client.requests))
        self.assertTrue(any(e['event'] == 'ignored' for e in events))
        self.assertEqual(client.max_active, 1); self.assertEqual(env.max_active, 1)
        before_completion = env.observations
        env.gate.set()
        await until(lambda: env.executions >= 2)
        self.assertGreater(env.dispatch_observations[1], before_completion)
        await runner.stop(); await task

    async def test_late_answer_for_changed_track_is_discarded(self):
        gate = asyncio.Event()
        runner, env, client, events = self.create(client=Client(gate=gate))
        task = asyncio.create_task(runner.run())
        await until(lambda: bool(client.requests))
        env.title = 'B'
        await until(lambda: runner.latest['title'] == 'B')
        gate.set()
        await until(lambda: any(e['event'] == 'ignored' for e in events))
        self.assertEqual(env.executions, 0)
        await runner.stop(); await task

    async def test_stop_during_response_wait_stops_inputs_without_waiting_for_response(self):
        gate = asyncio.Event()
        runner, env, client, events = self.create(client=Client(gate=gate))
        task = asyncio.create_task(runner.run())
        await until(lambda: bool(client.requests))
        await asyncio.wait_for(runner.stop(), .05)
        await asyncio.wait_for(task, .05)
        self.assertTrue(env.stopped); self.assertTrue(client.closed)
        self.assertEqual(env.executions, 0)

    async def test_partial_unverified_execution_blocks_repetition_but_keeps_observing(self):
        env = Environment(); env.result = {'verified': False, 'dispatched': True, 'partial': True}
        runner, env, client, events = self.create(env=env)
        task = asyncio.create_task(runner.run())
        await until(lambda: runner.blocked)
        at_block = env.observations
        await until(lambda: env.observations > at_block+8)
        self.assertEqual(env.executions, 1)
        self.assertTrue(any(e['event'] == 'error' and e.get('reason') == 'execution_unconfirmed' for e in events))
        await asyncio.wait_for(runner.stop(), .05)
        await asyncio.wait_for(task, .05)
        self.assertTrue(env.stopped)

    async def test_explicit_native_zero_input_rejection_requires_new_observation_and_new_answer(self):
        class Rejection(RuntimeError):
            native_pre_dispatch = True
            commands_sent = False
            dispatched = False
            retryable = True
            code = 'observation_stale'

        class RejectedOnce(Environment):
            decisions = None
            rejected_at_observation = None
            async def execute(self, decision, snapshot):
                self.executions += 1
                self.dispatch_observations.append(snapshot['observation'])
                if self.decisions is None:
                    self.decisions = []
                self.decisions.append(deepcopy(decision))
                if self.executions == 1:
                    self.title = 'B'
                    self.rejected_at_observation = self.observations
                    raise Rejection('Frame too old; no inputs sent.')
                return {'verified': True, 'dispatched': True}

        runner, env, client, events = self.create(env=RejectedOnce())
        task = asyncio.create_task(runner.run())
        await until(lambda: runner.verified_actions == 1)
        self.assertFalse(runner.blocked)
        self.assertEqual(env.decisions[0]['expected_titles'], 'A')
        self.assertEqual(env.decisions[1]['expected_titles'], 'B')
        self.assertGreater(env.dispatch_observations[1], env.rejected_at_observation)
        self.assertGreaterEqual(len(client.requests), 2)
        self.assertEqual(len(runner.history), 1)
        deferred = [e for e in events if e['event']=='execution_deferred']
        self.assertEqual(len(deferred), 1)
        self.assertIs(deferred[0]['commands_sent'], False)
        self.assertIs(deferred[0]['blocked'], False)
        self.assertFalse(any(e['event']=='verified' and e['decision']['expected_titles']=='A' for e in events))
        await runner.stop(); await task

    async def test_partial_or_unknown_errors_never_qualify_for_native_safe_retry(self):
        cases = [
            {},
            {'retryable': True},
            {'native_pre_dispatch': True, 'retryable': True, 'commands_sent': False},
            {'native_pre_dispatch': True, 'retryable': True, 'commands_sent': True, 'dispatched': True},
            {'native_pre_dispatch': True, 'retryable': False, 'commands_sent': False, 'dispatched': False},
            {'native_pre_dispatch': True, 'retryable': True, 'commands_sent': 0, 'dispatched': False},
            {'local_pre_dispatch': 1, 'retryable': True, 'commands_sent': False, 'dispatched': False},
            {'local_pre_dispatch': True, 'retryable': True, 'commands_sent': True, 'dispatched': True},
        ]
        for flags in cases:
            with self.subTest(flags=flags):
                class Failed(Environment):
                    async def execute(self, decision, snapshot):
                        self.executions += 1
                        error = RuntimeError('Unknown or partially applied input.')
                        for key, value in flags.items():
                            setattr(error, key, value)
                        raise error
                runner, env, client, events = self.create(env=Failed())
                task = asyncio.create_task(runner.run())
                await until(lambda: runner.blocked)
                count = env.observations
                await until(lambda: env.observations > count+3)
                self.assertEqual(env.executions, 1)
                self.assertFalse(any(e['event']=='execution_deferred' for e in events))
                await asyncio.wait_for(runner.stop(), .05)
                await asyncio.wait_for(task, .05)
                self.assertTrue(env.stopped)

    async def test_local_no_input_guard_requires_fresh_observation_and_new_answer(self):
        class ChangedDuringPreflight(Environment):
            rejected_observation = None
            decisions = None
            async def execute(self, decision, snapshot):
                self.executions += 1
                self.dispatch_observations.append(snapshot['observation'])
                if self.decisions is None:self.decisions=[]
                self.decisions.append(deepcopy(decision))
                if self.executions==1:
                    self.title='B'
                    self.rejected_observation=self.observations
                    raise LocalPreDispatch('Alignment changed before input.','alignment_not_confirmed')
                return {'verified':True,'dispatched':True}
        runner,env,client,events=self.create(env=ChangedDuringPreflight())
        task=asyncio.create_task(runner.run())
        try:
            await until(lambda:runner.verified_actions>=1)
            self.assertFalse(runner.blocked)
            self.assertEqual(env.decisions[0]['expected_titles'],'A')
            self.assertEqual(env.decisions[1]['expected_titles'],'B')
            self.assertGreater(env.dispatch_observations[1],env.rejected_observation)
            self.assertGreaterEqual(len(client.requests),2)
            deferred=[e for e in events if e['event']=='execution_deferred']
            self.assertEqual(len(deferred),1)
            self.assertEqual(deferred[0]['reason'],'local_pre_dispatch')
            self.assertEqual(deferred[0]['code'],'alignment_not_confirmed')
            self.assertFalse(deferred[0]['commands_sent'])
            self.assertFalse(any(e['event']=='verified' and e['decision']['expected_titles']=='A' for e in events))
        finally:
            await runner.stop();await task

    async def test_verified_transition_survives_holds_and_invalidates_when_old_deck_is_reloaded(self):
        runner,_,_,_=self.create()
        frame=loaded(loaded(raw(),playing=True),'B',1)
        frame['mixer']['crossfader_position']=0.
        frame['mixer']['eq_position']['2']['low']=-.4
        frame['mixer']['eq_neutral']['2']['low']=False
        before=dj_snapshot(frame)
        async def completed(control,previous,current,**values):
            decision={'transport':control,'expected_titles':{n:d['title'] for n,d in previous['decks'].items()},
                      'crossfader':'hold','bass':'hold','duration_beats':0,**values}
            async def outcome():return {'verified':True,'dispatched':control!='hold','snapshot':current}
            runner._actuator_context={'decision':decision,'before_snapshot':previous,'started':runner.clock(),'request_id':1}
            runner._actuator_task=asyncio.create_task(outcome())
            await runner._actuator_task;runner._actuator_done()
        # A verified load is remembered independently of later HOLD events.
        await completed('load_B',before,before,track_id=library()[1]['id'])
        frame['playingIndicators']['deck2']=True;frame['mixer']['red_bar_aligned']=True
        running=dj_snapshot(frame)
        await completed('play_B',before,running)
        self.assertEqual(runner.transition['incoming'],'B')
        self.assertEqual(runner.transition['outgoing'],'A')
        center=deepcopy(running);center['mixer']['cross']=.5
        center['decks']['A']['remaining']=20.;center['decks']['B']['remaining']=380.
        await completed('mix',running,center,crossfader='center',bass='B',duration_beats=8)
        for _ in range(35):await completed('hold',center,center)
        self.assertEqual(len(runner.history),30)
        request=dj_policy.prepare(center,runner.policy_history(),False)
        self.assertEqual(request['state']['transition']['outgoing_remaining_seconds'],20.)
        self.assertEqual(request['state']['continuity']['seconds_until_silence_if_unchanged'],380.)
        self.assertFalse(request['state']['transition']['handoff_endpoint_reached'])
        self.assertEqual(request['state']['transition']['last_verified_mix']['crossfader'],'center')
        self.assertEqual(request['state']['continuity']['trailing_hold_decisions_in_retained_history'],30)
        self.assertIn(library()[1]['id'],request['state']['recent_tracks'])
        self.assertIn('play_B',[a['transport'] for a in request['state']['recent_meaningful_actions']])
        endpoint=deepcopy(center);endpoint['mixer']['cross']=1.
        endpoint['decks']['A']['eq_neutral']['low']=False;endpoint['decks']['A']['bass']=-.6
        await completed('mix',center,endpoint,crossfader='B',bass='B',duration_beats=4)
        stopped=deepcopy(endpoint);stopped['decks']['A']['playing']=False
        await completed('stop_A',endpoint,stopped)
        context=dj_policy.prepare(stopped,runner.policy_history(),False)['state']['transition']
        self.assertTrue(context['handoff_endpoint_reached'])
        self.assertFalse(context['outgoing_playing']);self.assertFalse(context['outgoing_eq_neutral'])
        reloaded=deepcopy(stopped)
        reloaded['decks']['A'].update(title=library()[2]['title'],track_id=library()[2]['id'])
        await completed('load_A',stopped,reloaded,track_id=library()[2]['id'])
        self.assertIsNone(runner.transition)
        self.assertIsNone(dj_policy.prepare(reloaded,runner.policy_history(),False)['state']['transition'])

    async def test_transition_is_not_invented_from_mix_alone_or_unknown_play_before_frame(self):
        for control,before in (('mix',None),('play_B',None)):
            runner,_,_,_=self.create()
            after=dj_snapshot(loaded(loaded(raw(),playing=True),'B',1,True))
            runner._remember_verified({'decision':{'transport':control},'before_snapshot':before},
                                      {'snapshot':after,'verified':True})
            self.assertIsNone(runner.transition)

    async def test_opening_play_and_changed_track_after_frame_do_not_anchor_a_handoff(self):
        frame=loaded(loaded(raw()),'B',1)
        frame['mixer']['crossfader_position']=1.
        before=dj_snapshot(frame)
        frame['playingIndicators']['deck2']=True
        after=dj_snapshot(frame)
        runner,_,_,_=self.create()
        runner._remember_verified({'decision':{'transport':'play_B'},'before_snapshot':before},{'snapshot':after})
        self.assertIsNone(runner.transition)
        before['decks']['A']['playing']=True;before['mixer']['cross']=0.
        after['decks']['A']['playing']=True
        after['decks']['B'].update(title='Three',track_id=library()[2]['id'])
        runner._remember_verified({'decision':{'transport':'play_B'},'before_snapshot':before},{'snapshot':after})
        self.assertIsNone(runner.transition)

    async def test_snapshot_version_is_preserved_and_sequence_is_runner_owned(self):
        runner, env, client, events = self.create(client=Client(delay=.1))
        task = asyncio.create_task(runner.run())
        await until(lambda: runner.sequence >= 4)
        self.assertEqual(runner.latest['version'], 'A')
        sequences = [e['snapshot_seq'] for e in events if e['event'] == 'snapshot']
        self.assertEqual(sequences, list(range(1, len(sequences)+1)))
        await runner.stop(); await task


if __name__ == '__main__':
    unittest.main()
