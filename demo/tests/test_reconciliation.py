"""Regression checks for uncertain input; no Rekordbox or provider calls."""
import asyncio
from copy import deepcopy
import unittest

from test_runner import Environment, Policy, Client, until
from test_policy import snapshot, loaded, raw
from djjev.runner import Runner


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self):
        """Create a runner with observable fake boundaries."""
        self.events = []
        return Runner(Environment(), Policy(), Client(), self.events.append,
                      tick_interval=.001, observe_interval=.002, decision_interval=.002)

    async def complete(self, runner, control='prepare_B', before=None, result=None, error=None):
        """Finish exactly one action, without starting the autonomous loop."""
        context = {'request_id': 1, 'started': runner.clock(), 'before_snapshot': before or snapshot(),
                   'decision': {'transport': control, 'expected_titles': {'A': 'One', 'B': 'Two'}}}
        future = asyncio.get_running_loop().create_future()
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result if result is not None else {'verified': False, 'dispatched': True})
        runner._actuator_context, runner._actuator_task = context, future
        runner._actuator_done()

    async def observe(self, runner, frame=None, error=None):
        """Deliver an independent observation begun after action completion."""
        future = asyncio.get_running_loop().create_future()
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result((runner.clock(), frame if frame is not None else snapshot()))
        runner._observe_task = future
        runner._observe_done()

    async def test_changing_controls_never_count_as_stable_identities(self):
        """Same tracks cannot hide moving faders, EQ, transport or sync/master."""
        changes = [lambda s: s['mixer'].update(cross=.8),
                   lambda s: s['mixer'].update(aligned=True),
                   lambda s: s['decks']['A'].update(playing=True),
                   lambda s: s['decks']['A'].update(channel=.2),
                   lambda s: s['decks']['A']['eq_position'].update(low=-.5),
                   lambda s: s['decks']['A'].update(sync=True),
                   lambda s: s['decks']['A'].update(master=False),
                   lambda s: s['decks']['A'].update(bpm=128)]
        for change in changes:
            with self.subTest(change=change):
                runner = self.make_runner()
                await self.complete(runner)
                first = snapshot(); second = deepcopy(first); change(second)
                for i in range(8):
                    await self.observe(runner, first if i % 2 == 0 else second)
                self.assertTrue(runner.blocked)
                self.assertFalse(any(e['event'] == 'execution_reconciled' for e in self.events))

    async def test_jitter_and_advancing_clocks_allow_stable_controls(self):
        """Small visual jitter and running clocks do not prevent recovery."""
        runner = self.make_runner(); await self.complete(runner)
        first = snapshot(); second = deepcopy(first)
        second['mixer']['cross'] += .01
        second['decks']['A'].update(elapsed=2, remaining=298)
        second['effects']['cooldown_seconds'] = 42
        await self.observe(runner, first); await self.observe(runner, second)
        self.assertIsNone(runner._reconciliation)
        self.assertFalse(runner.blocked)

    async def test_invalid_or_failed_read_breaks_streak_without_using_valid_read_budget(self):
        """Neither invalid frames nor exceptions bridge two valid samples."""
        for invalid in ({'valid': False}, {'valid': True}, RuntimeError('observer lost')):
            runner = self.make_runner(); await self.complete(runner)
            await self.observe(runner)
            for _ in range(7):
                if isinstance(invalid, Exception):
                    await self.observe(runner, error=invalid)
                else:
                    await self.observe(runner, invalid)
            self.assertEqual(runner._reconciliation['reads'], 1)
            await self.observe(runner)
            self.assertIsNotNone(runner._reconciliation)
            self.assertFalse(runner.blocked)
            await self.observe(runner)
            self.assertIsNone(runner._reconciliation)

    async def test_unverified_and_unknown_mix_results_block_without_generic_recovery(self):
        """Only the dedicated guarded mix exception may permit mixer recovery."""
        for result, error in ((None, RuntimeError('no after frame')),
                              ({'verified': False, 'dispatched': True}, None),
                              ('malformed reply', None)):
            runner = self.make_runner()
            await self.complete(runner, 'mix', result=result, error=error)
            self.assertTrue(runner.blocked)
            self.assertIsNone(runner._reconciliation)
            self.assertIsNone(runner._recovery)

    async def test_hold_does_not_clear_failure_limit_but_verified_input_does(self):
        """Interleaved HOLD decisions cannot evade the three-failure limit."""
        runner = self.make_runner()
        for attempt in range(3):
            await self.complete(runner)
            if attempt < 2:
                await self.observe(runner); await self.observe(runner)
                await self.complete(runner, 'hold', result={'verified': True, 'dispatched': False})
        self.assertTrue(runner.blocked)
        self.assertEqual(runner._reconciliation_failures, 3)
        runner = self.make_runner(); await self.complete(runner)
        await self.observe(runner); await self.observe(runner)
        await self.complete(runner, result={'verified': True, 'dispatched': True})
        self.assertEqual(runner._reconciliation_failures, 0)

    async def test_reconciled_launch_restores_roles_without_claiming_verified_execution(self):
        """Observed launch roles retain handoff timing after a later center mix."""
        runner = self.make_runner()
        frame = loaded(loaded(raw(), playing=True), 'B', 1)
        frame['mixer']['crossfader_position'] = 0.
        before = snapshot(frame)
        await self.complete(runner, 'play_B', before=before)
        frame['playingIndicators']['deck2'] = True
        frame['mixer']['red_bar_aligned'] = True
        after = snapshot(frame)
        await self.observe(runner, after); await self.observe(runner, after)
        self.assertEqual(runner.transition['incoming'], 'B')
        self.assertEqual(runner.transition['outgoing'], 'A')
        self.assertEqual(runner.transition['source'], 'observed_silent_successor_start')
        self.assertEqual(runner.verified_actions, 0)
        self.assertEqual(runner.history, [])
        center = deepcopy(after); center['mixer']['cross'] = .5
        runner._remember_verified({'decision': {'transport': 'mix'}, 'before_snapshot': after},
                                  {'snapshot': center})
        self.assertIn('audible_mix_started_ns', runner.transition)
        from djjev import policy
        center['decks']['A']['remaining'] = 20.
        context = policy.prepare(center, runner.policy_history(), False)['state']['transition']
        self.assertEqual(context['outgoing_remaining_seconds'], 20.)

    async def test_changed_identity_does_not_invent_launch_context(self):
        """A replacement track cannot inherit the uncertain launch's roles."""
        runner = self.make_runner()
        frame = loaded(loaded(raw(), playing=True), 'B', 1)
        frame['mixer']['crossfader_position'] = 0.
        await self.complete(runner, 'play_B', before=snapshot(frame))
        loaded(frame, 'B', 2, True)
        after = snapshot(frame)
        await self.observe(runner, after); await self.observe(runner, after)
        self.assertIsNone(runner.transition)

    async def test_busy_request_is_cancelled_before_fresh_recovery_request(self):
        """Recovery need not wait for an obsolete busy-state provider answer."""
        class BusyClient(Client):
            cancelled = False
            async def ask(self, request):
                if request['state']['busy']:
                    self.requests.append(deepcopy(request))
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        self.cancelled = True
                        raise
                return await super().ask(request)
        runner = self.make_runner(); env = runner.env
        runner.client = client = BusyClient()
        env.gate = asyncio.Event()
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: any(r['state']['busy'] for r in client.requests))
            env.result = {'verified': False, 'dispatched': True}
            env.gate.set()
            await until(lambda: client.cancelled)
            env.result = {'verified': True, 'dispatched': True}
            await until(lambda: runner.verified_actions >= 1)
            self.assertFalse(runner.blocked)
            self.assertGreaterEqual(env.executions, 2)
        finally:
            await runner.stop(); await task
