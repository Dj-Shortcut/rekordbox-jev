"""Observe -> contextual Jev decision -> verified bounded action -> observe.

The production loop has no arbitrary opener/track-choice stopping point. Local
conditions remain code decisions, musical choices remain provider answers.
Partial or unconfirmed physical actions stop the loop without automatic retries.
The signed host passes its key in memory; evidence never includes it.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

import contextual_live as live
import music_context
from bridge import request
from contextual_planner import build_decision
from contextual_observation import adapt_observation
from jev_monitor import evaluate_recorded, WIDGET_EVENTS, DEFAULT_EVENTS, write_event


class SessionBlocked(RuntimeError):
    pass


class ContextualSession:
    def __init__(self, key, *, native=request, read_library=music_context.library,
                 evaluator=live.evaluate, recorder=evaluate_recorded,
                 output_root=None, clock=time.monotonic, now_ns=time.monotonic_ns,
                 sleeper=time.sleep, controls_executor=None, observer=None):
        self.key, self.native = key, native
        self.evaluator, self.recorder = evaluator, recorder
        self.clock, self.now_ns = clock, now_ns
        self.sleeper, self.controls_executor = sleeper, controls_executor
        self.observer = observer
        self.tracks = read_library()
        self.session = dict(selected='none', outgoing_deck='none', incoming_deck='none',
                            pending_action='none', action_confirmed=True)
        self.directory = Path(output_root or live.ROOT/'evidence'/'contextual-session')/str(uuid.uuid4())
        self.directory.mkdir(parents=True)
        self.events = []
        self.started = clock()
        self.commands = 0
        self.status_path = WIDGET_EVENTS.parent/'dj-session-status.json' if output_root is None else None
        self.widget_events = DEFAULT_EVENTS if output_root is None else self.directory/'widget-events'

    def record(self, event, **data):
        item = dict(event=event, elapsed_seconds=self.clock()-self.started, **data)
        self.events.append(item)
        # No key/environment/headers enter these data structures.
        (self.directory/'events.json').write_text(json.dumps(self.events, ensure_ascii=False, indent=2))
        message = {'trial_started': 'Startproef loopt…', 'loop_started': 'DJ Jev volgt de actuele toestand…',
                   'loop_stopped': data.get('message', 'DJ-lus gestopt.'),
                   'loop_blocked': data.get('message', 'DJ-lus onderbroken.'),
                   'observation_retry': data.get('reason', 'Opnieuw uitlezen…'),
                   'action_pending': 'Uitvoeren: '+str(data.get('action', 'handeling')),
                   'local_decision': 'Lokale stap: '+str(data.get('action', 'handeling')),
                   'holding': 'Jev wacht · actuele toestand opnieuw lezen',
                   'jev_pending': 'Jev kiest…',
                   'trial_complete': 'Eerste track speelt · volgende track gekozen',
                   'trial_blocked': data.get('message', 'Proef onderbroken.')}.get(event)
        if event == 'action_verified':
            message = {'LOAD_SELECTED': 'Gekozen track geladen', 'START_LOADED': 'Gekozen track speelt'}.get(
                data.get('action'), 'Handeling bevestigd: '+str(data.get('action', '')))
        if self.status_path and message:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.status_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'event': 'contextual_trial', 'message': message,
                                             'time': time.time(), 'status': event}))
            temporary.replace(self.status_path)
        if message and event in ('loop_blocked', 'loop_stopped', 'action_pending', 'action_verified',
                                  'observation_retry', 'holding', 'local_decision'):
            # Replace stale question/answer cards with the current local outcome.
            # These events never pretend that local progress is a Jev answer.
            local = {'schema_version': 1, 'id': str(uuid.uuid4()), 'started_at': time.time(),
                     'finished_at': time.time(), 'status': 'error' if event == 'loop_blocked' else 'not_sent',
                     'request': {'kind': 'jev_contextual_request', 'provenance': 'observed',
                                 'consumer': 'contextual_session', 'execution_enabled': True,
                                 'payload': {'state': {}, 'questions': {}}},
                     'result': {'inference_performed': False, 'message': message,
                                'local_event': event, 'action': data.get('action')},
                     'error': message if event == 'loop_blocked' else None}
            write_event(local, self.widget_events)

    def command(self, payload):
        before = self.clock()
        if payload.get('command') not in ('status', 'observe', 'activate', 'openFolder26'):
            self.commands += 1
        response = self.native(payload)
        if not isinstance(response, dict) or response.get('ok') is not True:
            error = response.get('error', 'Bridge niet bereikbaar.') if isinstance(response, dict) else 'Bridge niet bereikbaar.'
            if self.key:
                error = str(error).replace(self.key, '[verborgen]')
            raise SessionBlocked(str(error))
        result = response.get('result')
        if not isinstance(result, dict):
            raise SessionBlocked('Onleesbaar Bridge-resultaat.')
        self.record('native_result', command=payload['command'], seconds=self.clock()-before,
                    dispatched=result.get('dispatched'), verified=result.get('verified'),
                    search_seconds=result.get('search_seconds'), lookup_observations=result.get('lookup_observations'))
        return result

    def observation(self, *, allow_unreadable=False):
        observer = self.observer
        if observer is None:
            from contextual_controls import observe
            observer = observe
        raw = observer(self.command)
        adapted = adapt_observation(raw, self.tracks, now_ns=self.now_ns(), session=self.session)
        if not allow_unreadable and adapted['current'].get('readable') is not True:
            raise SessionBlocked('Rekordbox is niet vers en betrouwbaar uitgelezen.')
        return raw, adapted

    @staticmethod
    def signature(adapted):
        current = adapted['current']
        return {**{d: {k: current[d].get(k) for k in ('track', 'playing', 'channel', 'low_db')}
                   for d in ('A', 'B')},
                'crossfader': current.get('crossfader'),
                'selected': current.get('selected'),
                'outgoing_deck': current.get('outgoing_deck'),
                'incoming_deck': current.get('incoming_deck')}

    def choose(self, raw):
        prepared = live.prepare(raw, self.tracks, now_ns=self.now_ns(), session=self.session,
                                consumer='contextual_session')
        decision = prepared['decision']
        if decision['source'] == 'code':
            self.record('local_decision', action=decision['action'], reason=decision.get('reason'))
            return deepcopy(decision), raw, prepared['adapted']
        index = sum(item['event'] == 'jev_answer' for item in self.events)+1
        (self.directory/f'request-{index}.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2))
        self.record('jev_pending', question=decision['question_id'])
        result = self.recorder(prepared, api_key=self.key, evaluator=self.evaluator)
        (self.directory/f'answer-{index}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        choice = result['answers'][decision['question_id']]['choice']
        action = decision['option_actions'][choice]
        self.record('jev_answer', question=decision['question_id'], choice=choice,
                    action=action, request_seconds=result.get('request_seconds'))
        # Waiting for the API must never make the old screenshot permission to act.
        fresh_raw, fresh = self.observation()
        if self.signature(prepared['adapted']) != self.signature(fresh):
            raise SessionBlocked('Rekordbox veranderde tijdens Jevs antwoord; die keuze is niet uitgevoerd.')
        fresh_decision = build_decision(fresh['current'], fresh['library'])
        if (fresh_decision.get('source') != 'jev'
                or fresh_decision.get('question_id') != decision['question_id']
                or action not in fresh_decision.get('option_actions', {}).values()):
            raise SessionBlocked('Jevs handeling is niet meer beschikbaar in de huidige toestand.')
        return deepcopy(action), fresh_raw, fresh

    def remember_selection(self, action, adapted):
        track_id = action['parameters']['track_id']
        matching = [t for t in adapted['library'] if t['id'] == track_id and t['folder'] == '26']
        if len(matching) != 1:
            raise SessionBlocked('Gekozen trackidentiteit is niet uniek in map 26.')
        track = matching[0]
        self.session['selected'] = {'id': track_id, 'folder': '26',
                                    'target_deck': action['parameters'].get('target_deck')}
        self.record('selection_remembered', track=track)
        return track

    def execute_opening_action(self, action, raw, adapted):
        name = action['action']
        current = adapted['current']
        target = action.get('parameters', {}).get('deck')
        if target not in ('A', 'B'):
            raise SessionBlocked('Geen eenduidig doeldeck voor de uitvoering.')
        number = 1 if target == 'A' else 2
        if name == 'LOAD_SELECTED':
            if current[target]['track'] != 'none' or current[target]['playing'] is not False:
                raise SessionBlocked('Het gekozen doeldeck is niet meer leeg en gestopt.')
            other = 'B' if target == 'A' else 'A'
            if type(current[other]['playing']) is not bool or current[other]['track'] is None:
                raise SessionBlocked('Toestand van het andere deck is onbekend; niet geladen.')
            if current[other]['track'] != 'none' and current[other]['playing'] is not True:
                raise SessionBlocked('Een andere track staat al klaar; eerst de bestaande track afhandelen.')
            selected = self.session['selected']
            if not isinstance(selected, dict) or selected['id'] != action['parameters']['track_id']:
                raise SessionBlocked('Laadactie komt niet overeen met de bewaarde keuze.')
            track = next(t for t in adapted['library'] if t['id'] == selected['id'])
            empty_title = next((d.get('title') for d in raw.get('decks', []) if d.get('deck') == number), None)
            if not isinstance(empty_title, str) or empty_title.casefold().strip().rstrip('.').strip() != 'not loaded':
                raise SessionBlocked('De lege titel van het doeldeck is niet expliciet bevestigd.')
            payload = {'command': 'loadChosenTrack', 'deck': number, 'file': track['file'],
                       'openingOnly': current[other]['playing'] is False,
                       'expectedTrack': empty_title, 'clientPID': os.getpid(),
                       'notAfterMonotonicNS': self.now_ns()+55_000_000_000}
        elif name == 'START_LOADED':
            other = 'B' if target == 'A' else 'A'
            if current[target]['playing'] is not False or current[other]['playing'] is not False:
                raise SessionBlocked('Afspeeltoestand veranderde; openingsstart overgeslagen.')
            if current[other]['track'] != 'none' or current[target]['track'] in (None, 'none'):
                raise SessionBlocked('Startproef vereist precies één geladen track.')
            if current[target]['channel'] < .9 or current['crossfader'] not in (target, 'center'):
                raise SessionBlocked('Gekozen deck heeft geen bevestigde open mixerroute.')
            eq = raw.get('mixer', {}).get('eq_neutral', {}).get(str(number), {})
            if not all(eq.get(band) is True for band in ('low', 'mid', 'high', 'trim')):
                raise SessionBlocked('EQ van het openingsdeck staat niet bevestigd neutraal.')
            title = next(d['title'] for d in raw['decks'] if d['deck'] == number)
            payload = {'command': 'setPlayback', 'deck': number, 'expectedTrack': title, 'playing': True,
                       'openingOnly': True}
        else:
            raise SessionBlocked('Deze openingsproef ondersteunt die handeling nog niet: '+name)
        self.session.update(pending_action=name, action_confirmed=False)
        self.record('action_pending', action=name, deck=target)
        result = self.command(payload)
        if result.get('verified') is not True:
            raise SessionBlocked('Handeling verstuurd maar niet bevestigd; geen automatische herhaling.')
        # The native after-frame is evidence, not a guessed mutation of current.
        after = result.get('after')
        confirmed_session = {**self.session, 'pending_action': 'none', 'action_confirmed': True}
        if name == 'LOAD_SELECTED':
            confirmed_session['selected'] = 'none'
            other = 'B' if target == 'A' else 'A'
            if current[other]['playing'] is True:
                confirmed_session.update(outgoing_deck=other, incoming_deck=target)
        observed = adapt_observation(after, self.tracks, now_ns=self.now_ns(), session=confirmed_session)
        verified = observed['current']
        expected_id = action['parameters'].get('track_id') if name == 'LOAD_SELECTED' else current[target]['track']
        if (verified.get('readable') is not True or verified[target]['track'] != expected_id
                or verified[target]['playing'] is not (name == 'START_LOADED')):
            raise SessionBlocked('Actuele deckwaarneming bevestigt het gewenste resultaat niet.')
        other = 'B' if target == 'A' else 'A'
        if (verified[other]['track'] != current[other]['track']
                or verified[other]['playing'] is not current[other]['playing']):
            raise SessionBlocked('Het andere deck veranderde tijdens de handeling; geen automatische herhaling.')
        self.session = confirmed_session
        self.record('action_verified', action=name, deck=target, track_id=expected_id)
        return after, observed

    def execute_transition(self, action, raw, adapted):
        executor = self.controls_executor
        if executor is None:
            from contextual_controls import execute
            executor = execute
        previous_session = deepcopy(self.session)
        name = action['action']
        self.session.update(pending_action=name, action_confirmed=False)
        self.record('action_pending', action=name)
        after, updated = executor(action, raw, adapted, previous_session,
                                  native=self.command, now_ns=self.now_ns)
        if (not isinstance(after, dict) or not isinstance(updated, dict)
                or updated.get('pending_action') != 'none' or updated.get('action_confirmed') is not True):
            raise SessionBlocked('Overgangshandeling is niet bevestigd; geen automatische herhaling.')
        observed = adapt_observation(after, self.tracks, now_ns=self.now_ns(), session=updated)
        if observed['current'].get('readable') is not True:
            raise SessionBlocked('Geen verse bevestigde waarneming na de overgangshandeling.')
        self.session = deepcopy(updated)
        self.record('action_verified', action=name)
        return after, observed

    def run_loop(self, max_cycles=None):
        """Continue after each verified action; a finite cycle cap is a test seam."""
        report = {'mode': 'contextual_session', 'full_mix_test': False, 'cycles': 0}
        try:
            if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 0):
                raise SessionBlocked('Ongeldige cycluslimiet.')
            self.record('loop_started')
            status = self.command({'command': 'status'})
            if status.get('contextualTransport', {}).get('version') != 1:
                raise SessionBlocked('Bijgewerkte lokale laad- en afspeelbediening vereist.')
            self.command({'command': 'activate'})
            folder = self.command({'command': 'openFolder26'})
            if folder.get('verified') is not True or not isinstance(folder.get('after'), dict):
                raise SessionBlocked('Map 26 en de actuele decktoestand zijn niet bevestigd.')
            raw = folder['after']
            initial = adapt_observation(raw, self.tracks, now_ns=self.now_ns(), session=self.session)
            current = initial['current']
            loaded = [d for d in ('A', 'B') if current[d].get('track') not in (None, 'none')]
            playing = [d for d in loaded if current[d].get('playing') is True]
            if current.get('readable') is True and len(loaded) == 2 and len(playing) == 1:
                other = 'B' if playing[0] == 'A' else 'A'
                if current[other].get('playing') is False:
                    self.session.update(outgoing_deck=playing[0], incoming_deck=other)
                    self.record('session_roles_established', outgoing_deck=playing[0], incoming_deck=other,
                                reason='Existing single playing deck and loaded stopped deck; neither replaced.')
            retries = 0
            while max_cycles is None or report['cycles'] < max_cycles:
                action, raw, adapted = self.choose(raw)
                name = action['action']
                if name == 'OBSERVE_AGAIN':
                    reason = action.get('reason') or 'Toestand onvoldoende bekend voor de volgende handeling.'
                    if self.session.get('pending_action') not in (None, 'none') or self.session.get('action_confirmed') is not True:
                        raise SessionBlocked(reason+' Een eerdere handeling is niet bevestigd; niet herhaald.')
                    if retries >= 3:
                        raise SessionBlocked(reason)
                    retries += 1
                    self.record('observation_retry', reason=reason, attempt=retries)
                    raw, _ = self.observation(allow_unreadable=True)
                    continue
                retries = 0
                report['cycles'] += 1
                if name == 'SELECT_TRACK':
                    self.remember_selection(action, adapted)
                elif name in ('LOAD_SELECTED', 'START_LOADED'):
                    raw, _ = self.execute_opening_action(action, raw, adapted)
                elif name == 'HOLD':
                    self.record('holding')
                    self.sleeper(.2)
                    raw, _ = self.observation(allow_unreadable=True)
                elif name in ('PREPARE_INCOMING', 'LAUNCH_INCOMING', 'ALIGN_BEATS', 'INTRODUCE_INCOMING',
                              'TRANSFER_BASS', 'REMOVE_OUTGOING', 'FINISH_HANDOFF'):
                    raw, _ = self.execute_transition(action, raw, adapted)
                else:
                    raise SessionBlocked(action.get('reason') or 'Niet ondersteunde lokale handeling: '+str(name))
                # The returned after-frame is consumed immediately on the next
                # iteration. There is no sleep, extra screenshot or stop here.
            report.update(status='loop_stopped', reason='test_cycle_limit')
            self.record('loop_stopped', message='Ingestelde testcycluslimiet bereikt; geen volledige mixclaim.')
            return report
        except KeyboardInterrupt:
            report.update(status='loop_stopped', reason='interrupted')
            self.record('loop_stopped', message='DJ-lus onderbroken.')
            return report
        except Exception as error:
            message = str(error).replace(self.key, '[verborgen]') if self.key else str(error)
            report.update(status='blocked', error=message)
            self.record('loop_blocked', message=message)
            return report
        finally:
            report.update(deck_commands_attempted=self.commands,
                          verified_deck_actions=sum(e['event'] == 'action_verified' for e in self.events),
                          jev_requests=sum(e['event'] == 'jev_pending' for e in self.events),
                          jev_answers=sum(e['event'] == 'jev_answer' for e in self.events),
                          elapsed_seconds=self.clock()-self.started, session=deepcopy(self.session),
                          events=self.events, report=str(self.directory/'result.json'))
            (self.directory/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in report.items() if k != 'events'}, ensure_ascii=False), flush=True)

    def run_start_trial(self):
        report = {'mode': 'contextual_start_trial', 'full_mix_test': False}
        try:
            self.record('trial_started')
            status = self.command({'command': 'status'})
            if status.get('contextualTransport', {}).get('version') != 1:
                raise SessionBlocked('Bijgewerkte lokale laad- en afspeelbediening vereist.')
            self.command({'command': 'activate'})
            _, initial = self.observation()
            if any(initial['current'][d]['track'] != 'none' or initial['current'][d]['playing'] is not False
                   for d in ('A', 'B')):
                raise SessionBlocked('Openingsproef vereist twee bevestigde lege, gestopte decks; bestaande muziek blijft staan.')
            folder = self.command({'command': 'openFolder26'})
            if folder.get('verified') is not True:
                raise SessionBlocked('Map 26 kon niet worden bevestigd.')
            raw = folder.get('after')
            if not isinstance(raw, dict):
                raise SessionBlocked('Geen actuele waarneming na mapselectie.')
            # State is re-read each time. Load and start are consequent procedural
            # actions; track preferences come from actual Jev answers.
            for _ in range(6):
                action, raw, fresh = self.choose(raw)
                name = action['action']
                if name == 'SELECT_TRACK':
                    track = self.remember_selection(action, fresh)
                    if any(item.get('action') == 'START_LOADED' for item in self.events if item['event'] == 'action_verified'):
                        report.update(status='opening_playing_next_chosen', next_track=track,
                                      next_selection_source=action.get('source', 'jev'))
                        break
                elif name in ('LOAD_SELECTED', 'START_LOADED'):
                    raw, _ = self.execute_opening_action(action, raw, fresh)
                else:
                    raise SessionBlocked(action.get('reason', 'Geen uitvoerbare openingshandeling: '+name))
            else:
                raise SessionBlocked('Openingsproef bereikte zijn staplimiet.')
            self.record('trial_complete', status=report['status'])
            return report
        except (Exception, KeyboardInterrupt) as error:
            message = 'Proef onderbroken.' if isinstance(error, KeyboardInterrupt) else str(error)
            if self.key:
                message = message.replace(self.key, '[verborgen]')
            report.update(status='blocked', error=message)
            self.record('trial_blocked', message=message)
            return report
        finally:
            report.update(deck_commands_attempted=self.commands,
                          verified_deck_actions=sum(e['event'] == 'action_verified' for e in self.events),
                          jev_requests=sum(e['event'] == 'jev_pending' for e in self.events),
                          jev_answers=sum(e['event'] == 'jev_answer' for e in self.events),
                          elapsed_seconds=self.clock()-self.started,
                          events=self.events, report=str(self.directory/'result.json'))
            (self.directory/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in report.items() if k != 'events'}, ensure_ascii=False), flush=True)


def run(key):
    return ContextualSession(key).run_loop()
