"""Continuous Jev-owned set: observe, ask, recheck, execute, repeat.

The signed host supplies one in-memory credential. This process owns a reused
HTTPS connection, records actual provider answers, and never substitutes its own
musical choice. Stop cancels further inputs and leaves playback untouched.
"""
from collections import deque
from copy import deepcopy
import http.client
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
import uuid

import contextual_live
from jev_decisions import ENDPOINT
from jev_monitor import DEFAULT_EVENTS, WIDGET_EVENTS, evaluate_recorded
import music_context

ROOT = Path(__file__).resolve().parents[1]


class SetBlocked(RuntimeError):
    """An input may have occurred; do not repeat it without confirmation."""


class PersistentTransport:
    """urllib-compatible transport with one TLS connection for the whole set.

Failure drops the connection but never retries the old POST. The next loop
iteration obtains fresh state before making another provider request.
"""
    def __init__(self, endpoint=ENDPOINT, connection_factory=http.client.HTTPSConnection):
        self.endpoint = endpoint
        self.url = urlsplit(endpoint)
        if self.url.scheme != 'https' or not self.url.hostname:
            raise ValueError('Alleen de vaste HTTPS-provider wordt ondersteund.')
        self.connection_factory = connection_factory
        self.connection = None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __call__(self, request, timeout=8):
        if request.full_url != self.endpoint:
            raise ValueError('Onverwacht provideradres.')
        try:
            if self.connection is None:
                self.connection = self.connection_factory(self.url.hostname, self.url.port or 443, timeout=timeout)
            self.connection.timeout = timeout
            if getattr(self.connection, 'sock', None) is not None:
                self.connection.sock.settimeout(timeout)
            path = self.url.path or '/'
            if self.url.query:
                path += '?' + self.url.query
            self.connection.request(request.get_method(), path, body=request.data,
                                    headers=dict(request.header_items()))
            response = self.connection.getresponse()
            if response.status < 200 or response.status >= 300:
                status, headers = response.status, response.headers
                response.close()
                self.close()
                raise HTTPError(self.endpoint, status, 'Provider request rejected', headers, None)
            return response
        except HTTPError:
            raise
        except (OSError, http.client.HTTPException) as error:
            self.close()
            raise URLError('Provider connection interrupted') from None


class DJSet:
    def __init__(self, key, *, native=None, read_library=music_context.library,
                 brain=None, controls=None, evaluator=None, recorder=evaluate_recorded,
                 output_root=None, clock=time.monotonic, now_ns=time.monotonic_ns,
                 wall_clock=time.time, sleeper=time.sleep):
        if brain is None:
            import dj_brain as brain
        if controls is None:
            import dj_controls as controls
        self.owned_native = native is None
        if native is None:
            from dj_transport import NativeTransport
            native = NativeTransport()
        self.key, self.native, self.brain, self.controls = key, native, brain, controls
        self.clock, self.now_ns, self.wall_clock, self.sleeper = clock, now_ns, wall_clock, sleeper
        self.transport = PersistentTransport() if evaluator is None else None
        self.evaluator = evaluator or (lambda prepared, api_key: contextual_live.evaluate(
            prepared, api_key=api_key, transport=self.transport, clock=self.clock))
        self.recorder = recorder
        self.tracks = read_library()
        self.session = {'pending_action': 'none', 'action_confirmed': True,
                        'selected': 'none', 'outgoing_deck': 'none', 'incoming_deck': 'none',
                        'recent_tracks': [], 'history': []}
        self.run_id = str(uuid.uuid4())
        self.directory = Path(output_root or ROOT/'evidence'/'dj-sets')/self.run_id
        self.directory.mkdir(parents=True)
        self.widget_events = self.directory/'jev-events' if output_root is not None else DEFAULT_EVENTS
        self.status_paths = [] if output_root is not None else [
            ROOT/'evidence'/'dj-session-status.json', WIDGET_EVENTS.parent/'dj-session-status.json']
        self.events = deque(maxlen=500)
        self.started = self.clock()
        self.iterations = self.requests = self.answers = self.executed = self.commands = 0
        self.stop_requested = False

    def clean(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[verborgen]') if self.key else value
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return value

    def record(self, event, *, message=None, **data):
        item = self.clean({'event': event, 'time': self.wall_clock(),
                           'elapsed_seconds': self.clock()-self.started, **data})
        if message:
            item['message'] = self.clean(message)
        self.events.append(item)
        try:
            with (self.directory/'events.jsonl').open('a') as stream:
                stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False)+'\n')
        except OSError:
            pass
        if message:
            status = {**item, 'run_id': self.run_id, 'status': event,
                      'requests': self.requests, 'answers': self.answers, 'actions': self.executed}
            for path in self.status_paths:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(status, ensure_ascii=False, allow_nan=False))
                    temporary.replace(path)
                except OSError:
                    pass

    def command(self, payload):
        if self.stop_requested:
            raise KeyboardInterrupt
        name = payload.get('command')
        if name not in ('status', 'observe', 'activate', 'openFolder26'):
            self.commands += 1
        started = self.clock()
        reply = self.native(payload)
        if not isinstance(reply, dict) or reply.get('ok') is not True or not isinstance(reply.get('result'), dict):
            error = reply.get('error', 'Bridge-antwoord ontbreekt.') if isinstance(reply, dict) else 'Bridge-antwoord ontbreekt.'
            failure = SetBlocked(self.clean(str(error)))
            if (isinstance(reply, dict) and reply.get('errorKind') == 'pre_dispatch_guard'
                    and reply.get('commandsSent') is False and reply.get('retryable') is True):
                failure.retryable, failure.dispatched = True, False
            raise failure
        result = reply['result']
        self.record('native_result', command=name, seconds=self.clock()-started,
                    verified=result.get('verified'), dispatched=result.get('dispatched'))
        return result

    def observe(self):
        started = self.clock()
        raw = self.controls.observe(self.command)
        self.record('observed', seconds=self.clock()-started)
        return raw

    def prepare(self, raw):
        return self.brain.prepare(raw, self.tracks, session=deepcopy(self.session), now_ns=self.now_ns())

    @staticmethod
    def available(prepared):
        return prepared.get('decision', {}).get('source') == 'jev' and bool(prepared.get('payload', {}).get('questions'))

    def applicable(self, action, prepared):
        if not self.available(prepared):
            return False
        if hasattr(self.brain, 'applicable'):
            return self.brain.applicable(action, prepared)
        return action in prepared.get('decision', {}).get('option_actions', {}).values()

    def request_stop(self):
        self.stop_requested = True

    def retain_history(self, action, before, after, updated):
        # Bookkeeping records observed starts; it never selects or ranks music.
        recent = list(updated.get('recent_tracks', self.session.get('recent_tracks', [])))
        before_decks = {d.get('deck'): d for d in before.get('decks', [])}
        before_playing = before.get('playingIndicators', {})
        for deck in after.get('decks', []):
            number, title = deck.get('deck'), deck.get('title')
            playing = after.get('playingIndicators', {}).get(f'deck{number}') is True
            was_playing = before_playing.get(f'deck{number}') is True
            if playing and title and (not was_playing or before_decks.get(number, {}).get('title') != title):
                if not recent or recent[-1] != title:
                    recent.append(title)
        updated['recent_tracks'] = recent[-30:]
        history = list(updated.get('history', self.session.get('history', [])))
        history.append({'action': deepcopy(action), 'time': self.wall_clock(), 'verified': True})
        updated['history'] = history[-20:]
        return updated

    def run(self, *, max_iterations=None):
        """Production has no cycle limit. A finite limit exists only for tests."""
        status, reason, error = 'stopped', 'user_stop', None
        raw = None
        try:
            host = self.command({'command': 'status'})
            if host.get('protocolVersion', 0) < 3:
                raise SetBlocked('De bestaande Bridge ondersteunt deze set niet.')
            self.command({'command': 'activate'})
            self.record('set_started', message='DJ Jev draait · actuele toestand lezen')
            while not self.stop_requested and (max_iterations is None or self.iterations < max_iterations):
                self.iterations += 1
                iteration_started = self.clock()
                try:
                    raw = raw if raw is not None else self.observe()
                    prepared = self.prepare(raw)
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as read_error:
                    self.record('observation_retry', message='Opnieuw uitlezen · muziek blijft spelen',
                                error=str(read_error))
                    raw = None
                    self.sleeper(.2)
                    continue
                if not self.available(prepared):
                    self.record('observation_retry', message='Opnieuw uitlezen · '+str(
                        prepared.get('decision', {}).get('reason', 'toestand nog niet duidelijk')))
                    raw = None
                    self.sleeper(.2)
                    continue
                self.requests += 1
                question_started = self.clock()
                self.record('jev_pending', message='Jev kiest de volgende handeling…', request_number=self.requests)
                try:
                    result = self.recorder(prepared, api_key=self.key, evaluator=self.evaluator, directory=self.widget_events)
                    action = self.brain.resolve(prepared, result)
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as provider_error:
                    if self.transport:
                        self.transport.close()
                    self.record('provider_retry', message='Jev-antwoord niet beschikbaar · opnieuw met actuele toestand',
                                error=str(provider_error), seconds=self.clock()-question_started)
                    raw = None
                    self.sleeper(.2)
                    continue
                self.answers += 1
                self.record('jev_answer', action=action, request_number=self.requests,
                            seconds=self.clock()-question_started, request_seconds=result.get('request_seconds'))
                # A reply is a proposal until the action remains available in a
                # freshly observed state. Changed state causes a new question.
                try:
                    fresh = self.observe()
                    current = self.prepare(fresh)
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as read_error:
                    self.record('answer_discarded', message='Toestand opnieuw lezen · antwoord niet uitgevoerd', error=str(read_error))
                    raw = None
                    continue
                if not self.applicable(action, current):
                    self.record('answer_discarded', message='Toestand gewijzigd · Jev krijgt een nieuwe vraag', action=action)
                    raw = fresh
                    continue
                if self.stop_requested:
                    break
                name = action.get('action', '')
                if name == 'HOLD':
                    self.record('holding', message='Jev wacht · opnieuw kijken', action=action)
                    self.sleeper(.2)
                    raw = None
                    continue
                previous_session = deepcopy(self.session)
                self.session.update(pending_action=name, action_confirmed=False)
                self.record('action_pending', message='Uitvoeren · '+str(action.get('label', name)), action=action)
                started = self.clock()
                try:
                    after, updated = self.controls.execute(action, fresh, self.tracks, previous_session,
                                                           native=self.command, now_ns=self.now_ns)
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as control_error:
                    if (getattr(control_error, 'retryable', False) is True
                            and getattr(control_error, 'dispatched', True) is False):
                        self.session = previous_session
                        self.record('answer_discarded', message='Toestand gewijzigd · Jev krijgt een nieuwe vraag',
                                    error=str(control_error), action=action)
                        raw = None
                        continue
                    raise
                if not isinstance(after, dict) or not isinstance(updated, dict):
                    raise SetBlocked('Handeling heeft geen bevestigde nieuwe toestand; niet herhaald.')
                if updated.get('pending_action') != 'none' or updated.get('action_confirmed') is not True:
                    raise SetBlocked('Handeling niet bevestigd; niet automatisch herhaald.')
                self.session = self.retain_history(action, fresh, after, updated)
                self.executed += 1
                self.record('action_verified', message='Uitgevoerd · '+str(action.get('label', name)), action=action,
                            seconds=self.clock()-started, cycle_seconds=self.clock()-iteration_started)
                raw = after
            if not self.stop_requested and max_iterations is not None:
                reason = 'test_iteration_limit'
        except KeyboardInterrupt:
            self.stop_requested = True
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as failure:
            status, reason, error = 'blocked', 'execution_unconfirmed', self.clean(str(failure))
        finally:
            if self.transport:
                self.transport.close()
            if self.owned_native:
                self.native.close()
        self.record('set_'+status, message=('Bediening onderbroken · '+error) if error else 'DJ Jev gestopt · muziek blijft spelen')
        report = {'run_id': self.run_id, 'status': status, 'reason': reason, 'error': error,
                  'duration_seconds': self.clock()-self.started, 'iterations': self.iterations,
                  'jev_requests': self.requests, 'jev_answers': self.answers,
                  'verified_actions': self.executed, 'native_commands': self.commands,
                  'session': self.session, 'events': list(self.events),
                  'evidence_directory': str(self.directory)}
        try:
            (self.directory/'result.json').write_text(json.dumps(self.clean(report), ensure_ascii=False, indent=2))
        except OSError:
            pass
        return report


def run(key, **kwargs):
    report = DJSet(key, **kwargs).run()
    print(json.dumps({key: report[key] for key in ('run_id', 'status', 'reason', 'error', 'jev_requests',
                                                  'jev_answers', 'verified_actions')}, ensure_ascii=False), flush=True)
    return report
