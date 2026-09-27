"""Failure injection into production lifecycle code; never starts Rekordbox."""
import asyncio
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from test_runner import Environment, Policy, Client, until
from test_policy import snapshot
from djjev.environment import Native
from djjev.health import HealthBlocked, NativeConnectionError
from djjev.runner import Runner


def status(role):
    return {'demoRole':role, 'protocolVersion':3, 'bridgePID':123,
            'rekordboxRunning':True, 'rekordboxFrontmost':True,
            'accessibility':True, 'screenRecording':True}


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.events = []
        self.native = Native(sockets=self.root, bridge=self.root, trace=self.events.append)

    async def test_lost_reply_is_possible_input_and_never_replayed(self):
        received = []
        async def server(reader, writer):
            received.append(json.loads(await reader.readline()))
            writer.close(); await writer.wait_closed()
        service = await asyncio.start_unix_server(server, path=str(self.root/'demo-control.sock'))
        try:
            with self.assertRaises(NativeConnectionError) as caught:
                await self.native.call('control', 'mixGesture')
            self.assertTrue(caught.exception.commands_sent)
            self.assertEqual(len(received), 1)
        finally:
            service.close(); await service.wait_closed()

    async def test_missing_socket_is_not_misreported_as_sent_input(self):
        with self.assertRaises(NativeConnectionError) as caught:
            await self.native.call('control', 'mixGesture')
        self.assertFalse(caught.exception.commands_sent)

    async def test_live_worker_lock_prevents_duplicate_start(self):
        with (self.root/'demo-control.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
                with self.assertRaises(HealthBlocked) as caught:
                    await self.native.ensure_role('control')
                self.assertEqual(caught.exception.code, 'bridge_worker_unresponsive')
                spawn.assert_not_awaited()

    async def test_dead_worker_is_started_once_then_requires_new_generation(self):
        calls = []
        async def call(role, command, **params):
            calls.append((role, command))
            if len(calls) == 1:
                raise NativeConnectionError(role, command, False, ConnectionRefusedError())
            return status(role)
        self.native.call = call
        child = type('Child', (), {'returncode':None})()
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=child)) as spawn:
            await self.native.ensure_role('observer')
            self.assertEqual(spawn.await_count, 1)
        self.assertEqual(self.native.generation, 1)
        self.assertEqual(self.native.restarts.used, 1)
        self.assertEqual(calls, [('observer','status'), ('observer','status')])

    async def test_exhausted_restart_budget_blocks_before_spawning(self):
        self.native.restarts.used = 3
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(HealthBlocked) as caught:
                await self.native.ensure_role('observer')
            self.assertEqual(caught.exception.code, 'bridge_restarts_exhausted')
            spawn.assert_not_awaited()

    async def test_health_never_polls_or_refocuses_during_active_control(self):
        self.native.call = AsyncMock()
        async with self.native._role_locks['control']:
            await self.native.health()
        self.native.call.assert_not_awaited()

    async def test_focus_recovery_is_bounded_and_never_controls_decks(self):
        now = [10.]
        self.native.clock = lambda: now[0]
        commands = []
        async def call(role, command, **params):
            commands.append(command)
            return {**status(role), 'rekordboxFrontmost':False}
        self.native.call = call
        for value in (10., 12., 16., 22.):
            now[0] = value
            if value == 22.:
                with self.assertRaises(HealthBlocked):
                    await self.native.health()
            else:
                await self.native.health()
        self.assertEqual(commands.count('activate'), 2)
        self.assertTrue(set(commands) <= {'status','activate'})
        self.assertEqual(self.native.generation, 2)

    async def test_stop_and_inhibit_prevent_restarts_focus_and_physical_input(self):
        self.native.inhibit()
        self.assertTrue(self.native.cancel.exists())
        with self.assertRaises(HealthBlocked):
            await self.native.call('control', 'mixGesture')
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            await self.native.health()
            self.native.stopping = True
            with self.assertRaises(HealthBlocked):
                await self.native.ensure_role('observer')
            spawn.assert_not_awaited()

    async def test_stop_before_runner_initialization_is_not_erased(self):
        self.native.cancel.touch()
        later = Native(sockets=self.root, bridge=self.root)
        self.assertTrue(later.stopping)
        self.assertTrue(later.cancel.exists())
        with self.assertRaises(HealthBlocked):
            await later.call('control', 'activate')

    def test_protocol_and_build_mismatch_fail_closed(self):
        self.native.expected_build = {'source_digest':'expected'}
        for values in ({'protocolVersion':2}, {'demoRole':'control'}, {'sourceDigest':'old'}):
            with self.assertRaises(HealthBlocked):
                self.native.validate_status('observer', {**status('observer'), **values})


class RunnerHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_outage_has_deadline_and_never_dispatches(self):
        class Invalid(Environment):
            async def observe(self):
                await asyncio.sleep(.001)
                return {'valid':False}
        now = [0.]; events = []; env = Invalid()
        runner = Runner(env, Policy(), Client(), events.append, tick_interval=.001,
                        observe_interval=0, clock=lambda:now[0])
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: runner._observation_failure_since is not None)
            now[0] = 13.
            await until(lambda: runner.blocked)
            self.assertEqual(env.executions, 0)
            self.assertTrue(any(e.get('reason') == 'observation_timeout' for e in events))
        finally:
            await runner.stop(); await task

    async def test_permanent_api_error_exhausts_session_budget_with_fresh_requests(self):
        class Broken(Client):
            async def ask(self, request):
                self.requests.append(deepcopy(request))
                raise ValueError('malformed response')
        env = Environment(); client = Broken(); events = []
        # Advance the fake clock every loop without waiting through real backoff.
        now = [0.]
        def clock():
            now[0] += .1
            return now[0]
        runner = Runner(env, Policy(), client, events.append, tick_interval=.001,
                        observe_interval=0, decision_interval=0, clock=clock)
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: runner.blocked)
            self.assertEqual(len(client.requests), 4)  # initial + three recoveries
            self.assertEqual(env.executions, 0)
            observations = [r['state']['observation'] for r in client.requests]
            self.assertEqual(observations, sorted(set(observations)))
            self.assertTrue(any(e.get('reason') == 'api_recovery_exhausted' for e in events))
        finally:
            await runner.stop(); await task

    async def test_unexpected_track_change_blocks_while_request_is_pending(self):
        env = Environment(); events = []; client = Client(gate=asyncio.Event())
        runner = Runner(env, Policy(), client, events.append,
                        tick_interval=.001, observe_interval=.002, decision_interval=.002)
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: bool(client.requests))
            env.title = 'Unexpected track'
            await until(lambda: runner.blocked)
            self.assertEqual(env.executions, 0)
            self.assertTrue(any(e.get('reason') == 'tracks_changed' for e in events))
        finally:
            await runner.stop(); await task

    async def test_recovery_during_action_is_not_forgotten_when_action_finishes(self):
        env = Environment(); env.health_generation = 0; env.gate = asyncio.Event()
        events = []
        runner = Runner(env, Policy(), Client(), events.append,
                        tick_interval=.001, observe_interval=.002, decision_interval=.002)
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: env.executions == 1)
            env.health_generation = 1
            await until(lambda: runner._needs_health_reconciliation)
            self.assertTrue(runner.busy)
            env.gate.set()
            await until(lambda: env.executions >= 2)
            recovered = next(e for e in events if e['event']=='execution_reconciled')
            first = next(e for e in events if e['event']=='verified')
            second = [e for e in events if e['event']=='dispatch'][1]
            self.assertGreaterEqual(recovered['snapshot_seq'],first['snapshot_seq']+2)
            self.assertGreaterEqual(second['snapshot_seq'],recovered['snapshot_seq'])
        finally:
            await runner.stop(); await task

    async def test_process_recovery_waits_for_two_new_readings_before_new_answer(self):
        class Recovering(Environment):
            health_generation = 0
            async def observe(self):
                value = await super().observe()
                if self.observations == 3:
                    self.health_generation += 1
                return value
        env = Recovering(); events = []
        runner = Runner(env, Policy(), Client(delay=.03), events.append,
                        tick_interval=.001, observe_interval=.002, decision_interval=.002)
        task = asyncio.create_task(runner.run())
        try:
            await until(lambda: runner.verified_actions >= 1)
            self.assertFalse(runner.blocked)
            recovered = next(e for e in events if e['event']=='execution_reconciled')
            dispatch = next(e for e in events if e['event']=='dispatch')
            self.assertGreaterEqual(recovered['snapshot_seq'], 5)
            self.assertGreaterEqual(dispatch['snapshot_seq'], recovered['snapshot_seq'])
        finally:
            await runner.stop(); await task
