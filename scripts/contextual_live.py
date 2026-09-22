"""One real observation -> contextual question -> Jev answer, with no DJ execution.

The signed Bridge supplies its existing credential through the normal child pipe.
Only the native observe command is used here. The frozen Playground planner is
copied unchanged; missing live musical facts are never replaced by test fixtures.
"""
import json
import math
from copy import deepcopy
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bridge import request
from contextual_observation import adapt_observation
from contextual_planner import build_decision
from contextual_payload import compact_prepared
from jev_decisions import digest, ENDPOINT, MODEL
from jev_monitor import evaluate_recorded, write_event, DEFAULT_EVENTS
import music_context

ROOT = Path(__file__).resolve().parents[1]


def prepare(raw, library, *, now_ns, session=None, consumer='observer'):
    adapted = adapt_observation(raw, library, session=session, now_ns=now_ns)
    decision = build_decision(adapted['current'], adapted['library'])
    prepared = {'kind': 'jev_contextual_request', 'provenance': 'observed',
                'execution_enabled': False, 'adapted': adapted, 'decision': decision}
    if decision['source'] == 'jev':
        state = deepcopy(decision['state'])
        state['state_contract']['low_db'] = ('0 denotes the visually observed neutral pointer, not an exact audio measurement. '
                                           'Non-neutral positions have unknown sign and magnitude and stay null.')
        state['state_contract']['scope'] = (
            'Propose the next action for the observed state. The local application may execute it only '
            'after fresh state checks, then observe again and ask the next relevant question.'
            if consumer == 'contextual_session' else
            'Read-only proposal: the offered choices belong to the observed phase. No action will be executed by this observer.')
        prepared['consumer'] = consumer
        state['live_evidence'] = adapted['provenance']
        prepared['payload'] = {'model': MODEL, 'state': state,
                               'questions': {decision['question_id']: decision['question']}}
        prepared['payload_id'] = digest(prepared['payload'])
        prepared['context_id'] = digest(adapted['current'])
    return compact_prepared(prepared)


def evaluate(prepared, api_key=None, *, transport=urlopen, clock=time.monotonic):
    """Return the original typed answer, never repair it or execute it."""
    if (prepared.get('execution_enabled') is not False or
            prepared.get('decision', {}).get('source') != 'jev' or
            digest(prepared['payload']) != prepared.get('payload_id')):
        raise ValueError('Geen geldige alleen-lezen Jev-vraag.')
    if not isinstance(api_key, str) or not api_key:
        raise RuntimeError('De bestaande sleutel is niet beschikbaar; geen nieuwe invoer gevraagd.')
    payload = prepared['payload']
    http = Request(ENDPOINT, data=json.dumps(payload, allow_nan=False).encode(),
                   headers={'Authorization': 'Bearer '+api_key, 'Content-Type': 'application/json'}, method='POST')
    started = clock()
    try:
        with transport(http, timeout=8) as stream:
            body = stream.read(1_000_001)
        elapsed = clock()-started
        if len(body) > 1_000_000:
            raise ValueError('Jev-antwoord te groot.')
        response = json.loads(body)
    except HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError(f'TypeSafe HTTP {code}; geen bediening uitgevoerd.') from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError('TypeSafe-aanvraag niet voltooid; geen bediening uitgevoerd.') from None
    except (ValueError, UnicodeError):
        raise RuntimeError('TypeSafe gaf geen geldig JSON-antwoord; geen bediening uitgevoerd.') from None
    answers = response.get('answers') if isinstance(response, dict) else None
    questions = payload['questions']
    if not isinstance(answers, dict) or set(answers) != set(questions) or not isinstance(response.get('model'), str):
        raise ValueError('Jev-antwoord hoort niet bij deze vraag.')
    def probability(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict):
            raise ValueError('Ongeldig Jev-antwoord.')
        probabilities = answer.get('probabilities')
        if (answer.get('type') != 'choice' or answer.get('choice') not in question['criteria'] or
                not isinstance(probabilities, dict) or set(probabilities) != set(question['criteria']) or
                not all(probability(v) for v in probabilities.values()) or
                abs(sum(probabilities.values())-1) > .03 or not probability(answer.get('confidence')) or
                probabilities[answer['choice']]+1e-6 < max(probabilities.values())):
            raise ValueError('Ongeldige Jev-keuze; antwoord niet vervangen.')
    return {**response, 'raw_response': response, 'request_seconds': elapsed,
            'inference_performed': True, 'execution_enabled': False,
            'execution': {'status': 'not_performed', 'commands_sent': 0},
            'payload_id': prepared['payload_id'], 'context_id': prepared['context_id']}


def observe(native=request):
    response = native({'command': 'observe'})
    if response.get('ok') is not True or not isinstance(response.get('result'), dict):
        raise RuntimeError('Actuele Rekordbox-uitlezing niet beschikbaar; geen bediening uitgevoerd.')
    return response['result']


def run(api_key, *, native=request, read_library=music_context.library,
        evaluator=evaluate, recorder=evaluate_recorded, output_root=None, now_ns=time.monotonic_ns):
    """Bounded signed-host entrypoint. Deliberately has no action executor."""
    directory = Path(output_root or ROOT/'evidence'/'contextual-live')/str(uuid.uuid4())
    directory.mkdir(parents=True)
    report = {'started_at': time.time(), 'mode': 'contextual_observer',
              'execution_enabled': False, 'control_commands_sent': 0}
    recording_started = False
    def local_event(status, message, prepared=None):
        # New local outcomes replace old answers even when there was no Jev question.
        event = {'schema_version': 1, 'id': str(uuid.uuid4()),
                 'started_at': report['started_at'], 'finished_at': time.time(), 'status': status,
                 'request': prepared or {'kind': 'jev_contextual_request', 'provenance': 'observed',
                     'execution_enabled': False, 'payload': {'state': {}, 'questions': {}}},
                 'result': {'inference_performed': False, 'execution_enabled': False, 'message': message},
                 'error': message if status == 'error' else None}
        write_event(event, DEFAULT_EVENTS)
    try:
        tracks = read_library()
        before = time.monotonic()
        raw = observe(native)
        report.update(raw_observation=raw, observation_seconds=time.monotonic()-before)
        prepared = prepare(raw, tracks, now_ns=now_ns())
        report['prepared'] = prepared
        (directory/'request.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2))
        if prepared['decision']['source'] == 'code':
            report.update(status='code_proposal_only', inference_performed=False,
                          proposed_action=prepared['decision'])
            action = prepared['decision']['action']
            descriptions = {'OBSERVE_AGAIN': 'De uitlezing is nog onvoldoende voor een Jev-vraag.',
                            'START_LOADED': 'Er staat al een track klaar. Starten is de volgende lokale stap.',
                            'LOAD_SELECTED': 'Er is al een track gekozen. Laden is de volgende lokale stap.',
                            'FINISH_HANDOFF': 'De overgang is al afgerond; de lokale afronding is de volgende stap.',
                            'ALIGN_BEATS': 'De beatmarkeringen zijn nog niet gelijk.'}
            local_event('not_sent', descriptions.get(action, 'De volgende stap is een lokale handeling.') +
                        ' Deze proef voert geen bediening uit.', prepared)
        else:
            recording_started = True
            result = recorder(prepared, api_key=api_key, evaluator=evaluator)
            report.update(status='answered', inference_performed=True, result=result)
            question_id = prepared['decision']['question_id']
            choice = result['answers'][question_id]['choice']
            report['chosen_action'] = prepared['decision']['option_actions'][choice]
            track_id = report['chosen_action'].get('parameters', {}).get('track_id')
            report['chosen_track'] = next((t for t in prepared['adapted']['library'] if t['id'] == track_id), None)
            report['observation_age_at_answer_seconds'] = (now_ns()-raw['sampledAtMonotonicNS'])/1e9
            report['original_observation_within_three_seconds'] = 0 <= report['observation_age_at_answer_seconds'] <= 3
            try:
                after = observe(native)
                report['after_observation'] = after
                # This is a read-only comparison, not permission to apply an answer.
                report['deck_and_mixer_fields_unchanged'] = all(raw.get(k) == after.get(k)
                    for k in ('decks', 'faders', 'playingIndicators', 'mixer'))
            except (RuntimeError, OSError, ValueError) as error:
                report['readback_error_type'] = type(error).__name__
                report['deck_and_mixer_fields_unchanged'] = None
        return report
    except Exception as error:
        report.update(status='error', error_type=type(error).__name__)
        if not recording_started:
            message = 'Proef niet gestart: ' + str(error)
            if api_key:
                message = message.replace(api_key, '[verborgen]')
            local_event('error', message)
        raise
    finally:
        report['finished_at'] = time.time()
        (directory/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        summary = {'status': report.get('status'), 'control_commands_sent': 0,
                   'report': str(directory/'result.json'),
                   'chosen_track': (report.get('chosen_track') or {}).get('title'),
                   'question': (report.get('prepared') or {}).get('decision', {}).get('question_id')}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
