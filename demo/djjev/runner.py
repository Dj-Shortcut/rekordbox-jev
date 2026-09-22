"""Doom-shaped independent observation, inference and bounded actuation tasks.

Reference: github.com/AmoghCreator/doom-jev/blob/main/main.py, api_task pattern.
The world/UI keeps ticking while one request waits. Unlike held game buttons,
DJ relative controls are never replayed or queued between model answers.
"""
import asyncio
from copy import deepcopy
import time

from .events import emit
from .state import ENDPOINT_TOLERANCE, number


class Runner:
    def __init__(self, env, policy, client, events, *, tick_interval=.02,
                 observe_interval=.1, decision_interval=.1, clock=time.monotonic):
        self.env, self.policy, self.client, self.events = env, policy, client, events
        self.tick_interval, self.observe_interval, self.decision_interval = tick_interval, observe_interval, decision_interval
        self.clock = clock
        self.latest = None
        self.history = []
        self.meaningful_actions = []
        self.recent_tracks = []
        self.transition = None
        self._overlap_observation = None
        self.sequence = 0
        self.request_sequence = 0
        self.verified_actions = 0
        self.verified_decisions = 0
        self.busy = False
        self.blocked = False
        self.stopping = False
        self._stop_event = asyncio.Event()
        self._env_stopped = False
        self._observe_task = self._api_task = self._actuator_task = None
        self._epoch = 0
        self._last_actuation_end = float('-inf')
        self._latest_observation_start = float('-inf')
        self._last_request_snapshot = 0
        self._last_observe_start = self._last_request_start = float('-inf')
        self._api_context = None
        self._actuator_context = None

    def request_stop(self):
        self.stopping = True
        self._stop_event.set()

    async def stop(self):
        self.request_stop()
        # Stop native inputs before waiting for or cancelling any HTTP task.
        if not self._env_stopped:
            self._env_stopped = True
            try:
                await self.env.stop()
            except Exception as error:
                emit(self.events, 'error', stage='stop', error_type=type(error).__name__, message=str(error))

    @staticmethod
    def valid(snapshot):
        return isinstance(snapshot, dict) and snapshot.get('valid') is True

    @staticmethod
    def explicitly_rejected_before_input(error):
        """Only an explicit zero-input guard may authorize another decision.

        Native flags come from the actual completed reply. The local flag is
        emitted only before any control call in this bundle has been attempted.
        Missing flags, truthy strings/integers and unknown errors never qualify.
        """
        return ((getattr(error, 'native_pre_dispatch', None) is True
                 or getattr(error, 'local_pre_dispatch', None) is True)
                and getattr(error, 'retryable', None) is True
                and getattr(error, 'commands_sent', None) is False
                and getattr(error, 'dispatched', None) is False)

    def emit(self, name, **data):
        emit(self.events, name, snapshot_seq=self.sequence, busy=self.busy, blocked=self.blocked, **data)

    def policy_history(self):
        return {'actions':self.history, 'meaningful_actions':self.meaningful_actions,
                'recent_tracks':self.recent_tracks, 'transition':self.transition}

    @staticmethod
    def _identities(snapshot):
        decks=snapshot.get('decks',{}) if isinstance(snapshot,dict) else {}
        if not snapshot or snapshot.get('valid') is not True or set(decks)!={'A','B'}:
            return None
        return {n:{'title':d.get('title'),'track_id':d.get('track_id')} for n,d in decks.items()}

    def _invalidate_transition(self, snapshot):
        identities=self._identities(snapshot)
        if self.transition and identities is not None and identities!=self.transition['identities']:
            self.transition=None
            self._overlap_observation=None
        if not self.transition or identities!=self.transition['identities']:
            return
        cross=snapshot.get('mixer',{}).get('cross')
        stamp=snapshot.get('captured_ns')
        overlap=(number(cross,0,1) and ENDPOINT_TOLERANCE<cross<1-ENDPOINT_TOLERANCE
                 and all(d.get('playing') is True and number(d.get('channel'),.9,1)
                         for d in snapshot['decks'].values()))
        prior=self._overlap_observation
        interrupted=not overlap
        if prior and type(stamp) is int and stamp>=prior['stamp']:
            wall=(stamp-prior['stamp'])/1e9
            for name,deck in snapshot['decks'].items():
                old=prior['elapsed'].get(name);new=deck.get('elapsed')
                if number(old,0) and number(new,0) and (new-old < -1 or new-old > wall*1.2+2):
                    interrupted=True  # Seek/realignment, not continuous overlap.
        if interrupted:
            self.transition.pop('audible_mix_started_ns',None)
        self._overlap_observation=({'stamp':stamp,'elapsed':{n:d.get('elapsed')
            for n,d in snapshot['decks'].items()}} if overlap and type(stamp) is int else None)

    def _remember_verified(self, context, result):
        decision=context['decision'];control=decision.get('transport')
        if control!='hold':
            self.meaningful_actions.append({k:deepcopy(decision[k]) for k in
                ('transport','track_id','crossfader','bass','duration_beats','expected_titles') if k in decision})
            self.meaningful_actions=self.meaningful_actions[-12:]
        if str(control).startswith('load_') and isinstance(decision.get('track_id'),str):
            self.recent_tracks=(self.recent_tracks+[decision['track_id']])[-12:]
        before=context.get('before_snapshot');after=result.get('snapshot')
        self._invalidate_transition(after)
        if self.transition and control in ('align_A','align_B'):
            self.transition.pop('audible_mix_started_ns',None)
            self._overlap_observation=None
        identity=self._identities(before)
        if (control in ('play_A','play_B') and identity is not None
                and identity==self._identities(after) and all(v['track_id'] for v in identity.values())):
            incoming=control[-1];outgoing='B' if incoming=='A' else 'A'
            inc=before['decks'][incoming];out=before['decks'][outgoing]
            cross=before.get('mixer',{}).get('cross')
            muted=number(cross,0,1) and (cross>=1-ENDPOINT_TOLERANCE if incoming=='A' else cross<=ENDPOINT_TOLERANCE)
            if (muted and inc.get('playing') is False and out.get('playing') is True
                    and number(out.get('channel'),.9,1)
                    and all(after['decks'][n].get('playing') is True for n in ('A','B'))):
                self.transition={'incoming':incoming,'outgoing':outgoing,'identities':identity,
                    'source':'verified_silent_successor_start', 'started_snapshot_version':before.get('version')}
        if self.transition and self._identities(after)==self.transition['identities'] and control!='hold':
            self.transition['last_verified_action']=control
            if control=='mix':
                self.transition['last_verified_mix']={k:decision.get(k) for k in ('crossfader','bass','duration_beats')}
                # Count actual audible overlap, never the earlier muted start.
                # Conservatively start at the verified after-frame, not dispatch;
                # a closed route, pause, seek or alignment invalidates old timing.
                for frame in (after,):
                    if self._identities(frame)!=self.transition['identities']:
                        continue
                    cross=frame.get('mixer',{}).get('cross')
                    stamp=frame.get('captured_ns')
                    if (number(cross,ENDPOINT_TOLERANCE,1-ENDPOINT_TOLERANCE)
                            and ENDPOINT_TOLERANCE<cross<1-ENDPOINT_TOLERANCE
                            and type(stamp) is int and stamp>0
                            and all(frame['decks'][n].get('playing') is True
                                    and number(frame['decks'][n].get('channel'),.9,1) for n in ('A','B'))):
                        self.transition.setdefault('audible_mix_started_ns',stamp)
                        break

    async def _observe(self):
        started = self.clock()
        result = await self.env.observe()
        return started, result

    def _observe_done(self):
        task, self._observe_task = self._observe_task, None
        try:
            started, snapshot = task.result()
            self.sequence += 1
            self.latest = deepcopy(snapshot)
            self._invalidate_transition(snapshot)
            self._latest_observation_start = started
            self.emit('snapshot', snapshot=self.latest, valid=self.valid(snapshot), seconds=self.clock()-started)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.latest = None
            self.emit('error', stage='observe', error_type=type(error).__name__, message=str(error))

    def _actuator_done(self):
        task, self._actuator_task = self._actuator_task, None
        context, self._actuator_context = self._actuator_context, None
        self.busy = False
        self._epoch += 1
        self._last_actuation_end = self.clock()
        try:
            result = task.result()
            if not isinstance(result, dict) or result.get('verified') is not True:
                self.blocked = True
                self.emit('error', stage='execute', reason='execution_unconfirmed',
                          request_id=context['request_id'], decision=context['decision'], result=result)
                return
            self.history.append({'decision': context['decision'], 'verified': True, 'time': self.clock()})
            self._remember_verified(context,result)
            self.verified_decisions += 1
            self.verified_actions += result.get('dispatched') is True
            self.history = self.history[-30:]
            # env's after-snapshot is evidence; dispatch still waits for an
            # independent observation begun after this actuation finished.
            self.emit('verified', request_id=context['request_id'], decision=context['decision'], result=result,
                      seconds=self.clock()-context['started'])
        except asyncio.CancelledError:
            pass
        except Exception as error:
            if self.explicitly_rejected_before_input(error):
                # No input occurred. Discard this answer; the actuation epoch
                # and completion barrier above require a later observation and
                # a new Jev response, never a replay of the rejected decision.
                reason = 'local_pre_dispatch' if getattr(error, 'local_pre_dispatch', None) is True else 'native_pre_dispatch'
                self.emit('execution_deferred', stage='execute', reason=reason,
                          request_id=context['request_id'],
                          code=getattr(error, 'code', None), message=str(error),
                          decision=context['decision'], commands_sent=False,
                          dispatched=False, retryable=True)
                return
            self.blocked = True
            self.emit('error', stage='execute', reason='execution_unconfirmed',
                      request_id=context['request_id'], error_type=type(error).__name__,
                      message=str(error), decision=context['decision'])

    def _api_done(self):
        task, self._api_task = self._api_task, None
        context, self._api_context = self._api_context, None
        try:
            response = task.result()
            self.emit('answer', request_id=context['id'], request=context['request'], response=response,
                      seconds=self.clock()-context['started'])
            if self.stopping or self.blocked or self.busy or context['busy'] or context['epoch'] != self._epoch:
                self.emit('ignored', request_id=context['id'], reason='busy_stopped_or_changed_execution')
                return
            if not self.valid(self.latest) or self._latest_observation_start < self._last_actuation_end:
                self.emit('ignored', request_id=context['id'], reason='fresh_observation_required')
                return
            decision = self.policy.resolve(context['request'], response)
            if not isinstance(decision, dict) or self.policy.applicable(decision, self.latest, self.policy_history()) is not True:
                self.emit('ignored', request_id=context['id'], reason='decision_no_longer_applicable')
                return
            # No action queue: either act now against latest state or discard.
            self.busy = True
            self._epoch += 1
            self._actuator_context = {'decision': deepcopy(decision), 'started': self.clock(),
                                      'request_id': context['id'], 'before_snapshot':deepcopy(self.latest)}
            self.emit('dispatch', request_id=context['id'], decision=decision)
            self._actuator_task = asyncio.create_task(self.env.execute(deepcopy(decision), deepcopy(self.latest)))
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.emit('error', stage='inference', request_id=context['id'], error_type=type(error).__name__, message=str(error))

    async def run(self):
        self.emit('started')
        try:
            while not self.stopping:
                # Completion order rejects answers overlapping a physical action,
                # including when action and API finish in the very same UI tick.
                if self._actuator_task is not None and self._actuator_task.done():
                    self._actuator_done()
                if self._observe_task is not None and self._observe_task.done():
                    self._observe_done()
                if self._api_task is not None and self._api_task.done():
                    self._api_done()
                now = self.clock()
                if self._observe_task is None and now-self._last_observe_start >= self.observe_interval:
                    self._last_observe_start = now
                    self._observe_task = asyncio.create_task(self._observe())
                fresh_after_action = self.busy or self._latest_observation_start >= self._last_actuation_end
                if (not self.blocked and self._api_task is None and self.valid(self.latest)
                        and fresh_after_action and self.sequence != self._last_request_snapshot
                        and now-self._last_request_start >= self.decision_interval):
                    try:
                        request = self.policy.prepare(deepcopy(self.latest), deepcopy(self.policy_history()), self.busy)
                        if isinstance(request, dict) and request.get('questions'):
                            self.request_sequence += 1
                            self._last_request_start, self._last_request_snapshot = now, self.sequence
                            self._api_context = {'id': self.request_sequence, 'request': request,
                                                 'started': now, 'busy': self.busy, 'epoch': self._epoch}
                            self.emit('request', request_id=self.request_sequence, request=request)
                            self._api_task = asyncio.create_task(self.client.ask(deepcopy(request)))
                    except Exception as error:
                        self.emit('error', stage='prepare', error_type=type(error).__name__, message=str(error))
                self.emit('tick', api_pending=self._api_task is not None,
                          observation_pending=self._observe_task is not None)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_interval)
                except TimeoutError:
                    pass
        finally:
            await self.stop()
            tasks = [task for task in (self._api_task, self._observe_task, self._actuator_task) if task is not None]
            for task in tasks:
                task.cancel()
            if hasattr(self.client, 'close'):
                await self.client.close()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.emit('stopped')
        return {'requests': self.request_sequence, 'snapshots': self.sequence,
                'verified_actions': self.verified_actions, 'verified_decisions': self.verified_decisions,
                'blocked': self.blocked}
