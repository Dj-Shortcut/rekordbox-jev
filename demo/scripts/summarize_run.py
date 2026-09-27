#!/usr/bin/env python3
"""Read-only evidence report. Never imports or starts the DJ application.

Usage: python3 scripts/summarize_run.py [run-directory|events.jsonl] [--json] [--transitions]
Without a path, reads the most recently modified evidence/*/events.jsonl.
An unfinished final JSONL line is reported, not treated as an execution result.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
BANDS = ('low', 'mid', 'high', 'trim')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def safe(value, max_text=1000, public_hashes=False):
    """Only call on projected fields, never on entire requests or responses."""
    if isinstance(value, str):
        value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', value)
        value = re.sub(r'\b(?:sk|ts|typesafe)[_-][A-Za-z0-9_-]{12,}', '[redacted]', value)
        return re.sub(r'[A-Za-z0-9_+/=-]{48,}', '[long token redacted]', value)[:max_text]
    if isinstance(value, dict):
        return {k: (v if public_hashes and k == 'analysis_id' and isinstance(v, str)
                    and re.fullmatch(r'[0-9a-f]{64}', v) else safe(v, max_text, public_hashes))
                for k, v in value.items()}
    if isinstance(value, list):
        return [safe(v, max_text, public_hashes) for v in value]
    return value


def pick(value, keys):
    if not isinstance(value, dict):
        return {}
    return {k: value[k] for k in keys if k in value
            and (value[k] is None or type(value[k]) in (str, bool) or number(value[k]))}


def state_summary(snapshot):
    if not isinstance(snapshot, dict):
        return {}
    decks = snapshot.get('decks', {})
    decks = decks if isinstance(decks, dict) else {}
    result = pick(snapshot, ('valid', 'version', 'captured_ns', 'error'))
    result['mixer'] = pick(snapshot.get('mixer'), ('cross', 'aligned'))
    result['decks'] = {}
    for name in ('A', 'B'):
        deck = decks.get(name, {})
        result['decks'][name] = pick(deck, ('title', 'track_id', 'playing', 'channel',
            'bass', 'bpm', 'bpm_text', 'remaining', 'sync', 'master'))
        if isinstance(deck, dict):
            result['decks'][name]['eq_neutral'] = pick(deck.get('eq_neutral'), BANDS)
    return result


def native_summary(event):
    result = event.get('result', {})
    result = result if isinstance(result, dict) else {}
    summary = pick(event, ('command_id', 'command', 'phase', 'seconds', 'message', 'error_type'))
    summary['result'] = pick(result, ('dispatched', 'commandsSent', 'verified',
        'crossfaderVerified', 'bassDirectionVerified', 'pointerActions', 'elapsedMS',
        'requestedCrossfader', 'measuredCrossfader', 'stepsCompleted', 'stepsRequested',
        'appliedBassPixels', 'playing', 'requestedPlaying', 'reason'))
    verification = result.get('verification', {})
    summary['result']['verification'] = pick(verification, ('attempts', 'elapsedMS',
        'renderWaitExhausted', 'targetPlaying', 'otherPlaying', 'aligned', 'measuredCrossfader'))
    if isinstance(verification, dict):
        summary['result']['verification']['reasons'] = [v for v in verification.get('reasons', [])
            if isinstance(v, str)] if isinstance(verification.get('reasons', []), list) else []
        for key, fields in (('actualTitles', ('1', '2')), ('expectedTitles', ('1', '2')),
                            ('playing', ('1', '2')), ('measuredBass', ('outgoing', 'incoming'))):
            if key in verification:
                summary['result']['verification'][key] = pick(verification[key], fields)
    if isinstance(result.get('after'), dict):
        summary['result']['after'] = state_summary(result['after'])
    if isinstance(event.get('flags'), dict):
        summary['flags'] = pick(event['flags'], ('native_pre_dispatch', 'commands_sent', 'dispatched', 'retryable'))
    return summary


def neutral(deck):
    values = deck.get('eq_neutral', {})
    return isinstance(values, dict) and all(values.get(band) is True for band in BANDS)


def summarize(path):
    counts = Counter()
    failures, deferred, actions, holds, loads, timings, handoffs, native_unconfirmed = [], [], [], [], [], [], [], []
    observed_titles, malformed = [], []
    active_request = None
    dispatch_states = {}
    native_calls = {}
    trace_by_request = {}
    latest = {}
    pair = None
    overlap = None
    mixed = None
    completed_pair = False
    endpoint_pending = None
    first_time = last_time = None

    def inspect_state(snapshot, line):
        nonlocal latest, pair, overlap, mixed, completed_pair, endpoint_pending
        if not isinstance(snapshot, dict) or snapshot.get('valid') is not True:
            return
        latest = state_summary(snapshot)
        decks, mixer = latest['decks'], latest['mixer']
        identity = tuple((decks[d].get('track_id'), decks[d].get('title')) for d in ('A', 'B'))
        if identity != pair:
            pair, overlap, mixed, completed_pair, endpoint_pending = identity, None, None, False, None
        for d in ('A', 'B'):
            title = decks[d].get('title')
            if decks[d].get('track_id') and title and title not in observed_titles:
                observed_titles.append(title)
        cross = mixer.get('cross')
        both_playing = all(decks[d].get('playing') is True for d in ('A', 'B'))
        if (both_playing and mixer.get('aligned') is True and number(cross) and .01 < cross < .99
                and all(number(decks[d].get('channel')) and decks[d]['channel'] > .01 for d in ('A', 'B'))):
            overlap = overlap or {'line': line, 'cross': cross}
        endpoint = 'A' if number(cross) and cross <= .01 else 'B' if number(cross) and cross >= .99 else None
        if overlap and mixed and not completed_pair and endpoint and decks[endpoint].get('playing') is True:
            outgoing = 'B' if endpoint == 'A' else 'A'
            if mixed.get('toward') not in (None, endpoint):
                return
            endpoint_pending = {'from': decks[outgoing].get('title'), 'to': decks[endpoint].get('title'),
                'deck': endpoint, 'overlap_line': overlap['line'], 'mix_line': mixed['line'],
                'endpoint_line': line, 'eq_neutral': {d: neutral(decks[d]) for d in ('A', 'B')}}
            if all(endpoint_pending['eq_neutral'].values()):
                handoffs.append(dict(endpoint_pending))
                completed_pair, endpoint_pending = True, None

    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('not an object')
            except ValueError:
                malformed.append(line_number)
                continue
            name = event.get('event')
            counts[name if isinstance(name, str) else 'unknown'] += 1
            timestamp = event.get('time')
            if number(timestamp):
                first_time = timestamp if first_time is None else min(first_time, timestamp)
                last_time = timestamp if last_time is None else max(last_time, timestamp)
            request_id = event.get('request_id')
            if name == 'dispatch':
                active_request = request_id
                dispatch_states[request_id] = latest
                trace_by_request[active_request] = []
            elif name == 'snapshot':
                inspect_state(event.get('snapshot'), line_number)
            elif name == 'answer':
                response = event.get('response', {})
                response = response if isinstance(response, dict) else {}
                timings.append({'request_id': request_id, 'line': line_number,
                    'roundtrip_seconds': event.get('seconds') if number(event.get('seconds')) else None,
                    'client_seconds': response.get('request_seconds') if number(response.get('request_seconds')) else None})
            elif name == 'native_command' and event.get('phase') in ('returned', 'error', 'cancelled'):
                trace = {'line': line_number, 'request_id': active_request, **native_summary(event)}
                trace_by_request.setdefault(active_request, []).append(trace)
                native_calls[event.get('command_id', line_number)] = trace
                result = event.get('result', {})
                result = result if isinstance(result, dict) else {}
                if result.get('verified') is False or event.get('phase') in ('error', 'cancelled'):
                    native_unconfirmed.append(trace)
                inspect_state(result.get('after'), line_number)
            elif name == 'verified':
                result, decision = event.get('result', {}), event.get('decision', {})
                if not isinstance(result, dict) or result.get('verified') is not True:
                    continue
                decision = decision if isinstance(decision, dict) else {}
                item = {'line': line_number, 'request_id': request_id,
                    'decision': pick(decision, ('transport', 'crossfader', 'bass', 'mid', 'high', 'duration_beats')),
                    'seconds': event.get('seconds') if number(event.get('seconds')) else None}
                pure_hold = all(decision.get(k, 'hold') == 'hold' for k in ('transport', 'crossfader', 'bass', 'mid', 'high'))
                if result.get('dispatched') is True:
                    actions.append(item)
                    counts['physical_verified'] += 1
                    if decision.get('crossfader', 'hold') != 'hold':
                        before = dispatch_states.get(request_id, {})
                        inspect_state(result.get('snapshot'), line_number)
                        before_cross = before.get('mixer', {}).get('cross')
                        after_cross = latest.get('mixer', {}).get('cross')
                        both_states = (before, latest)
                        same_titles = all(before.get('decks', {}).get(d, {}).get('title') ==
                            latest.get('decks', {}).get(d, {}).get('title') for d in ('A', 'B'))
                        aligned_playing = all(s.get('valid') is True and s.get('mixer', {}).get('aligned') is True
                            and all(s.get('decks', {}).get(d, {}).get('playing') is True for d in ('A', 'B')) for s in both_states)
                        if (overlap and same_titles and aligned_playing and number(before_cross)
                                and number(after_cross) and abs(after_cross-before_cross) > .005):
                            mixed = {'line': line_number, 'toward': decision.get('crossfader') if decision.get('crossfader') in ('A', 'B') else None}
                elif result.get('dispatched') is False and pure_hold:
                    holds.append(item)
                elif result.get('dispatched') is False:
                    counts['other_verified_without_input'] += 1
                else:
                    counts['verified_dispatch_unknown'] += 1
                inspect_state(result.get('snapshot'), line_number)
                transport = decision.get('transport', '')
                if (transport in ('load_A', 'load_B') and result.get('dispatched') is True
                        and isinstance(result.get('snapshot'), dict) and result['snapshot'].get('valid') is True):
                    deck = transport[-1]
                    actual = latest.get('decks', {}).get(deck, {})
                    if actual.get('track_id') and actual.get('playing') is False:
                        loads.append({'line': line_number, 'request_id': request_id, 'deck': deck,
                            'title': actual.get('title'), 'track_id': actual.get('track_id')})
                active_request = None
            elif name in ('error', 'execution_deferred'):
                failure = {'line': line_number, **pick(event, ('request_id', 'stage', 'reason', 'message', 'error', 'error_type', 'blocked'))}
                context = request_id if request_id is not None else active_request
                if event.get('stage') == 'execute':
                    failure['native_commands'] = trace_by_request.get(context, [])
                    failure['native_evidence_missing'] = not bool(failure['native_commands'])
                    active_request = None
                (deferred if name == 'execution_deferred' else failures).append(failure)

    values = [t['roundtrip_seconds'] for t in timings if number(t['roundtrip_seconds'])]
    latency = {'count': len(values), 'min': min(values) if values else None,
        'median': statistics.median(values) if values else None,
        'max': max(values) if values else None}
    return safe({'path': str(path.resolve()), 'events': sum(counts[k] for k in counts if k not in
        ('physical_verified', 'other_verified_without_input', 'verified_dispatch_unknown')),
        'duration_seconds': last_time-first_time if first_time is not None else None,
        'stopped_event_seen': bool(counts['stopped']), 'requests': counts['request'],
        'answers': counts['answer'], 'ignored_answers': counts['ignored'],
        'physical_verified_actions': actions, 'verified_holds': holds,
        'other_verified_without_input': counts['other_verified_without_input'],
        'verified_dispatch_unknown': counts['verified_dispatch_unknown'],
        'jev_roundtrip_seconds': latency, 'jev_timings': timings,
        'confirmed_loads': loads, 'observed_loaded_titles': observed_titles,
        'completed_handoffs': handoffs, 'incomplete_endpoint': endpoint_pending,
        'handoff_rule': 'Aligned both-playing overlap, verified physical mix, endpoint, both decks all EQ bands neutral.',
        'failures': failures, 'deferred_before_input': deferred,
        'native_unconfirmed_replies': native_unconfirmed,
        'native_trace_available': bool(native_calls), 'malformed_lines': malformed,
        'last_observed_state': latest})


DECISION_FIELDS = ('transport', 'track_id', 'crossfader', 'bass', 'mid', 'high', 'duration_beats')
QUESTION_NAMES = ('transport', 'next_track', 'crossfader', 'bass', 'mid', 'high', 'duration')
TIMING_FIELDS = ('style', 'lead_deck', 'lead_track_progress_fraction', 'preferred_overlap_bars',
    'shorter_overlap_bars', 'preferred_overlap_seconds', 'shorter_overlap_seconds',
    'seconds_until_preferred_launch_window', 'ending_needs_priority',
    'overlap_time_available_before_finish_seconds', 'confirmed_audible_overlap_seconds',
    'seconds_to_preferred_overlap', 'suggested_remaining_overlap_seconds',
    'lead_track_duration_seconds', 'incoming_track_duration_seconds', 'incoming_remaining_seconds',
    'shortest_known_track_duration_seconds', 'launch_progress_floor_preference',
    'launch_window_remaining_seconds', 'timing_basis')
FEATURE_FIELDS = ('energy_dbfs', 'low_energy_dbfs_estimate', 'low_fraction',
    'onsets_per_beat', 'flux', 'energy_delta_db', 'low_energy_delta_db',
    'onsets_per_beat_delta', 'flux_delta')


def choice_key(question, key):
    if not isinstance(key, str):
        return False
    if question == 'next_track':
        return re.fullmatch(r't_[0-9a-f]{16}', key) is not None
    if question == 'transport':
        return key in ('hold', 'mix') or re.fullmatch(r'(?:load|prepare|play|align|stop|reset|echo)_[AB]', key) is not None
    return key in {'crossfader': ('hold', 'A', 'center', 'B'),
        'bass': ('hold', 'A', 'balanced', 'B', 'A_gentle', 'B_gentle', 'A_deep', 'B_deep'),
        'mid': ('hold', 'A_soft', 'B_soft', 'A_cut', 'B_cut', 'neutral'),
        'high': ('hold', 'A_soft', 'B_soft', 'A_cut', 'B_cut', 'neutral'), 'duration': ('beats2', 'beats4', 'beats8', 'beats16')}.get(question, ())


def replay_state(value):
    result = state_summary(value)
    decks = value.get('decks') if isinstance(value, dict) else None
    if isinstance(decks, dict):
        for name in ('A', 'B'):
            result['decks'][name].update(pick(decks.get(name), ('elapsed',)))
    return result


def audio_window(value):
    result = pick(value, ('index', 'window_index', 'bars', 'start_bar', 'end_bar',
        'start_seconds', 'end_seconds', 'seconds', 'bars_ahead', 'offset_bars',
        'horizon_bars', 'coverage_bars', 'at_seconds', 'in_bars', 'status', 'basis') + FEATURE_FIELDS)
    if isinstance(value, dict):
        observations = value.get('observations')
        if isinstance(observations, list):
            result['observations'] = [v for v in observations[:8] if v in (
                'energy_rising', 'energy_falling', 'low_band_rising', 'low_band_falling',
                'onset_activity_rising', 'onset_activity_falling', 'spectral_change_increasing')]
    return result


def audio_summary(value):
    """Project only the context actually recorded in this request, not new analysis."""
    if not isinstance(value, dict):
        return None
    result = pick(value, ('basis', 'overlap_bars_estimate', 'status', 'limitations'))
    if isinstance(value.get('limitations'), list):
        result['limitations'] = [v for v in value['limitations'][:16] if isinstance(v, str)]
    result['roles'] = pick(value.get('roles'), ('outgoing', 'incoming', 'source'))
    decks = value.get('decks')
    result['decks'] = {}
    for name in ('A', 'B'):
        deck = decks.get(name) if isinstance(decks, dict) else None
        if not isinstance(deck, dict):
            continue
        item = pick(deck, ('status', 'reason', 'track_id', 'analysis_id', 'position_seconds',
                          'position_bar', 'basis', 'window_bars', 'clock_basis', 'tempo_ratio', 'position_region'))
        item['current_window'] = audio_window(deck['current_window']) if isinstance(deck.get('current_window'),dict) else None
        ahead = deck.get('lookahead')
        if isinstance(ahead, list):
            item['lookahead'] = [audio_window(w) for w in ahead[:8] if isinstance(w, dict)]
        elif isinstance(ahead, dict):
            item['lookahead'] = {k: audio_window(ahead[k]) for k in ('8', '16', '32') if k in ahead}
        changes = deck.get('changes')
        if isinstance(changes, list):
            item['changes'] = [audio_window(w) for w in changes[:16] if isinstance(w, dict)]
        contour = deck.get('source_contour')
        if isinstance(contour, dict):
            item['source_contour'] = pick(contour, ('segment_bars', 'position_segment_index', 'basis'))
            segments = contour.get('segments')
            if isinstance(segments, list):
                item['source_contour']['segments'] = [pick(w, ('start_seconds', 'end_seconds',
                    'start_bar', 'coverage_bars', 'energy_dbfs', 'low_energy_dbfs_estimate',
                    'onsets_per_beat', 'flux')) for w in segments[:16] if isinstance(w, dict)]
        result['decks'][name] = item
    return result


def transition_decisions(path, request_id=None):
    """Join read-only evidence by request ID, including HOLD and ignored answers.

    No API, cache lookup or actuator imports. Observed inputs and typed choices
    let reviewers assess decisions; they do not reveal a model's hidden reason.
    """
    records, malformed = {}, []

    def record(identifier):
        return records.setdefault(identifier, {'request_id': identifier,
            'audio_evidence': 'not_available_at_decision', 'audio_context': None,
            'outcome': 'request_missing', 'lines': {}})

    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('not an object')
            except ValueError:
                malformed.append(line_number)
                continue
            identifier = event.get('request_id')
            if type(identifier) is not int or identifier < 1:
                continue
            if request_id is not None and identifier != request_id:
                continue
            name = event.get('event')
            if name not in ('request', 'answer', 'dispatch', 'verified', 'ignored', 'error', 'execution_deferred'):
                continue
            item = record(identifier)
            item['lines'][name] = line_number
            if name == 'request':
                request = event.get('request')
                request = request if isinstance(request, dict) else {}
                state = request.get('state')
                state = state if isinstance(state, dict) else {}
                item['outcome'] = 'requested'
                item['observed'] = replay_state(state)
                item['snapshot_version'] = state.get('snapshot_version') if type(state.get('snapshot_version')) is int else None
                item['audio_context'] = audio_summary(state.get('audio_context'))
                if item['audio_context'] is not None:
                    item['audio_evidence'] = 'recorded_at_decision'
                item['musical_timing'] = pick(state.get('musical_timing'), TIMING_FIELDS)
                timing = state.get('musical_timing')
                if isinstance(timing, dict):
                    item['musical_timing']['grid_hint'] = pick(timing.get('grid_hint'),
                        ('bars_since_first_downbeat', 'bars_until_next_16_bar_group', 'near_16_bar_group', 'basis'))
                transition = state.get('transition')
                item['transition'] = pick(transition, ('incoming', 'outgoing', 'source',
                    'audible_mix_started_ns', 'last_verified_action', 'handoff_endpoint_reached',
                    'outgoing_remaining_seconds', 'incoming_eq_neutral', 'outgoing_eq_neutral'))
                if isinstance(transition, dict):
                    item['transition']['last_verified_mix'] = pick(transition.get('last_verified_mix'), DECISION_FIELDS)
                actions = state.get('recent_meaningful_actions')
                item['recent_meaningful_actions'] = [pick(v, DECISION_FIELDS) for v in actions[-12:]
                    if isinstance(v, dict)] if isinstance(actions, list) else []
                questions = request.get('questions')
                item['questions'] = {}
                for key in QUESTION_NAMES:
                    q = questions.get(key) if isinstance(questions, dict) else None
                    if not isinstance(q, dict):
                        continue
                    projected = pick(q, ('type', 'instructions'))
                    criteria = q.get('criteria')
                    projected['criteria'] = {k: v for k, v in list(criteria.items())[:256]
                        if choice_key(key, k) and (v is None or isinstance(v, str))} if isinstance(criteria, dict) else {}
                    item['questions'][key] = projected
            elif name == 'answer':
                response = event.get('response')
                response = response if isinstance(response, dict) else {}
                item['answer'] = pick(response, ('model', 'request_seconds'))
                item['answer']['answers'] = {}
                answers = response.get('answers')
                for key in QUESTION_NAMES:
                    a = answers.get(key) if isinstance(answers, dict) else None
                    if not isinstance(a, dict):
                        continue
                    answer = pick(a, ('type', 'choice', 'confidence'))
                    probabilities = a.get('probabilities')
                    criteria = item.get('questions', {}).get(key, {}).get('criteria', {})
                    answer['probabilities'] = {k: probabilities[k] for k in criteria
                        if k in probabilities and number(probabilities[k])} if isinstance(probabilities, dict) else {}
                    item['answer']['answers'][key] = answer
                item['roundtrip_seconds'] = event.get('seconds') if number(event.get('seconds')) else None
                item['outcome'] = 'answered'
            elif name == 'dispatch':
                item['dispatch'] = pick(event.get('decision'), DECISION_FIELDS)
                item['outcome'] = 'dispatched_unconfirmed'
            elif name == 'verified':
                result = event.get('result')
                result = result if isinstance(result, dict) else {}
                item['execution'] = {**pick(result, ('verified', 'dispatched')),
                    'decision': pick(event.get('decision'), DECISION_FIELDS)}
                item['execution_seconds'] = event.get('seconds') if number(event.get('seconds')) else None
                if result.get('verified') is True:
                    item['verified_state'] = replay_state(result.get('snapshot'))
                    item['outcome'] = ('verified_physical' if result.get('dispatched') is True else
                        'verified_hold' if result.get('dispatched') is False and
                        all(item['execution']['decision'].get(k, 'hold') == 'hold' for k in ('transport', 'crossfader', 'bass', 'mid', 'high'))
                        else 'verified_without_input' if result.get('dispatched') is False else 'verified_dispatch_unknown')
                else:
                    item['outcome'] = 'execution_unconfirmed'
            else:
                item['outcome'] = name
                item['failure' if name != 'ignored' else 'ignored'] = pick(event,
                    ('stage', 'reason', 'message', 'error_type', 'error', 'commands_sent', 'dispatched', 'retryable'))
    return safe({'decisions': list(records.values()), 'malformed_lines': malformed,
        'limitations': 'Recorded inputs and typed choices, not model rationales or proof of better audio. '
                       'Missing audio context was not available at decision time; no later analysis is added. '
                       'Only allowlisted fields are shown; text is limited to 12000 characters.'},
                max_text=12000, public_hashes=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path)
    parser.add_argument('--json', action='store_true', help='Machine-readable projected evidence.')
    parser.add_argument('--transitions', action='store_true', help='Include recorded two-deck context, choices and verified outcomes.')
    parser.add_argument('--request-id', type=int, help='Filter --transitions to one request ID.')
    args = parser.parse_args()
    if args.request_id is not None and (not args.transitions or args.request_id < 1):
        parser.error('--request-id requires --transitions and a positive integer.')
    path = args.path
    if path is None:
        paths = list((ROOT/'evidence').glob('*/events.jsonl'))
        if not paths:
            parser.error('No evidence events.jsonl found.')
        path = max(paths, key=lambda p: p.stat().st_mtime)
    if path.is_dir():
        path = path/'events.jsonl'
    try:
        report = summarize(path)
        if args.transitions:
            report['transition_decisions'] = transition_decisions(path, args.request_id)
    except OSError as error:
        parser.error(safe(str(error)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return
    print(f"Run: {path.parent.name}")
    print(f"Fysiek bevestigde acties: {len(report['physical_verified_actions'])}; HOLD zonder input: {len(report['verified_holds'])}; overige zonder input: {report['other_verified_without_input']}")
    print(f"Jev: {report['requests']} API-calls, {report['answers']} antwoorden, {report['ignored_answers']} vervallen antwoorden")
    timing = report['jev_roundtrip_seconds']
    if timing['count']:
        print(f"Jev roundtrip: mediaan {timing['median']:.3f}s; min {timing['min']:.3f}s; max {timing['max']:.3f}s")
    for load in report['confirmed_loads']:
        print(f"Bevestigd geladen: {load['deck']} · {load['title']} (regel {load['line']})")
    print('Waargenomen geladen titels: '+(', '.join(report['observed_loaded_titles']) or 'geen'))
    print(f"Volledig bewezen overdrachten inclusief EQ-herstel: {len(report['completed_handoffs'])}")
    for handoff in report['completed_handoffs']:
        print(f"  {handoff['from']} → {handoff['to']} (regels {handoff['overlap_line']}, {handoff['mix_line']}, {handoff['endpoint_line']})")
    if report['incomplete_endpoint']:
        print('Endpoint gezien, volledig EQ-herstel ontbreekt: '+json.dumps(report['incomplete_endpoint'], ensure_ascii=False))
    for failure in report['failures']:
        print('Fout: '+json.dumps(failure, ensure_ascii=False))
    for failure in report['deferred_before_input']:
        print('Uitgesteld vóór input: '+json.dumps(failure, ensure_ascii=False))
    print(f"Native trace aanwezig: {report['native_trace_available']}; onbevestigde native replies: {len(report['native_unconfirmed_replies'])}; Stop gezien: {report['stopped_event_seen']}")
    if report['malformed_lines']:
        print('Onvolledige/ongeldige JSONL-regels: '+str(report['malformed_lines']))
    print('Alleen logbewijs; dit rapport meet geen audio of mixkwaliteit.')
    if args.transitions:
        print(json.dumps(report['transition_decisions'], ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
