"""Eight-beat entry preferences anchored to a measured outgoing return.

The grid is musical context, not a kick/drop detector. A model-selected slot
becomes a bounded native start target; no answer is replaced by a local choice.
"""
from .arrangement import context as arrangement_context
from .state import number
from .transition_budget import COMPLETION_MARGIN_SECONDS, LAUNCH_ALIGNMENT_RESERVE_SECONDS

SLOT_BEATS = 8
LONG_TRACK_SECONDS = 300.  # Existing style threshold, not a semantic track label.
ARM_SECONDS = 8.
MIN_PLAN_LEAD_SECONDS = 3.


def context(snapshot, outgoing, incoming, entry):
    result = {'status': 'unavailable', 'spacing_beats': SLOT_BEATS, 'spacing_bars': 2,
              'preferred_slot': None, 'slots': [], 'selected_preference': None,
              'long_track_threshold_seconds': LONG_TRACK_SECONDS,
              'basis': 'x1 is the estimated outgoing return; each x is eight beats/two 4/4 bars. '
                       'Prefer x1 on long outgoing tracks, x2 on shorter ones; later even x points '
                       'are alternatives, not x3/x5. Arrangement and continuity can override this. '
                       'The four-bar analysis does not identify the exact first kick.'}
    source = (snapshot.get('audio_windows') or {}).get(outgoing, {})
    decks = snapshot['decks']
    out, inc = decks.get(outgoing, {}), decks.get(incoming, {})
    anchor = (entry.get('evidence') or {}).get('last_return')
    grid = source.get('source_grid', {})
    ratio, position = source.get('tempo_ratio'), source.get('position_seconds')
    if (source.get('status') != 'available' or not anchor or inc.get('playing') is not False
            or not inc.get('track_id') or not number(grid.get('bpm'), 60, 200)
            or not number(grid.get('start_seconds'), 0) or not number(ratio, .94, 1.06)
            or not number(position, 0) or not number(out.get('remaining'), 0)
            or not number(out.get('bpm'), 60, 200)
            or type(snapshot.get('captured_ns')) is not int):
        return result
    # Only a validated constant source grid can establish the two-bar phase.
    beat_seconds = 60 / grid['bpm']
    anchor_beats = (anchor['start_seconds'] - grid['start_seconds']) / beat_seconds
    if abs(anchor_beats / 4 - round(anchor_beats / 4)) > .01:
        return result
    total = position + out['remaining']
    preferred = 1 if total >= LONG_TRACK_SECONDS else 2
    result.update(status='estimated_grid', preferred_slot=preferred,
                  outgoing=outgoing, incoming=incoming, outgoing_duration_seconds=total,
                  anchor_source_seconds=anchor['start_seconds'])
    for index in (1, 2, 4, 6, 8):
        target = anchor['start_seconds'] + (index - 1) * SLOT_BEATS * beat_seconds
        if target >= anchor['end_seconds']:
            break
        wait = (target - position) / ratio
        available = min((anchor['end_seconds'] - target) / ratio,
                        out['remaining'] / ratio - max(0., wait))
        available = max(0., available-COMPLETION_MARGIN_SECONDS-LAUNCH_ALIGNMENT_RESERVE_SECONDS)
        # The entry calculation also bounds the stopped incoming file length.
        incoming_limit = (entry.get('candidate') or {}).get('overlap_budget_seconds')
        if number(incoming_limit, 0):
            available = min(available, incoming_limit)
        arrangement = arrangement_context(snapshot, outgoing, incoming, max(0., wait))
        minimum = 16 * 60 / out['bpm']
        slot = {'id': f'x{index}', 'index': index, 'source_seconds': target,
                'seconds_until': wait, 'beat_monotonic_ns': snapshot['captured_ns'] + round(wait * 1e9),
                'outgoing_remaining_at_target': total-target,
                'blend_budget_seconds': round(available, 3),
                'enough_time': available >= minimum,
                'drop_fit': arrangement['handover_fit'],
                'armable': MIN_PLAN_LEAD_SECONDS <= wait <= ARM_SECONDS}
        result['slots'].append(slot)
    viable = [s for s in result['slots'] if s['seconds_until'] >= MIN_PLAN_LEAD_SECONDS
              and s['enough_time'] and s['drop_fit'] not in
              ('too_late_for_planned_handoff', 'intro_too_short_for_planned_blend')]
    # Prefer x2 for short tracks, but retain an earlier suitable x1 if x2 cannot
    # accommodate the incoming intro. No physical action is selected here.
    ranked = sorted(viable, key=lambda s: (s['index'] != preferred,
                                          s['index'] < preferred, s['index']))
    if ranked:
        result['selected_preference'] = ranked[0]['id']
    return result


def attach_question(request):
    state, questions = request['state'], request['questions']
    grid = state['musical_timing'].get('entry_grid', {})
    if (not questions or state['preparation']['required_now'] or state.get('transition')
            or not any(k.startswith('play_') for k in questions['transport']['criteria'])
            or grid.get('status') != 'estimated_grid'):
        return
    slots = [s for s in grid['slots'] if s['armable'] and s['enough_time']
             and s['drop_fit'] not in ('too_late_for_planned_handoff', 'intro_too_short_for_planned_blend')]
    criteria = {'fallback': 'No suitable x target: use measured late-window/continuity timing '
                           'or an alternative arrangement, never pretend a missed x was reached.'}
    criteria.update({s['id']: f"Arm {s['id']} in {s['seconds_until']:.2f} seconds; "
                     f"drop fit: {s['drop_fit']}." for s in slots})
    questions['entry_slot'] = {'type': 'choice', 'criteria': criteria, 'instructions':
        'Assume PLAY of the prepared silent successor is selected. Read musical_timing.entry_grid '
        'and arrangement. Each x is eight beats/two bars after the previous x, anchored to the '
        'estimated outgoing return. Prefer selected_preference: x1 for a long outgoing track, '
        'x2 for a shorter track if room remains. x2/x4 and later even slots can replace a missed '
        'slot; do not drift onto x3. A candidate is not a proven kick or drop. '
        'Use fallback when the arrangement or available time makes this approach unsuitable. '
        'Outside PLAY this answer is unused; it never moves a fader.'}
    questions['transport']['instructions'] = (
        'For a supported outgoing return, choose PLAY ahead of the selected x while its slot '
        'is armable (3–8 seconds before it); native control waits for that exact grid point. '
        'Do not HOLD until the point has already passed. PLAY remains a silent launch: '
        'after alignment choose the first center blend promptly, then build toward the incoming drop '
        'only when the two arrangements support it. Never treat silent playback as audible mixing. '
        + questions['transport']['instructions'])


def chosen_target(request, answers):
    if not answers['transport']['choice'].startswith('play_'):
        return None
    choice = answers.get('entry_slot', {}).get('choice')
    if choice in (None, 'fallback'):
        return None
    state = request['state']
    grid = state['musical_timing']['entry_grid']
    slot = next(s for s in grid['slots'] if s['id'] == choice)
    out, inc = grid['outgoing'], grid['incoming']
    source = state['audio_context']['decks'][out]
    return {**slot, 'outgoing': out, 'incoming': inc, 'analysis_id': source['analysis_id'],
            'outgoing_track_id': state['decks'][out]['track_id'],
            'incoming_track_id': state['decks'][inc]['track_id'],
            'tempo_ratio': source['tempo_ratio'], 'bpm': state['decks'][out]['bpm']}


def target_current(target, snapshot):
    """Reject a missed/changed target; never silently roll it to the next bar."""
    try:
        out, inc = target['outgoing'], target['incoming']
        decks = snapshot['decks']
        audio = snapshot['audio_windows'][out]
        if (decks[out]['track_id'] != target['outgoing_track_id']
                or decks[inc]['track_id'] != target['incoming_track_id']
                or decks[out]['playing'] is not True or decks[inc]['playing'] is not False
                or audio.get('analysis_id') != target['analysis_id']
                or not number(audio.get('tempo_ratio'), .94, 1.06)
                or abs(audio['tempo_ratio'] - target['tempo_ratio']) > .001
                or abs(decks[out]['bpm'] - target['bpm']) > .02):
            return False
        wait = (target['source_seconds'] - audio['position_seconds']) / audio['tempo_ratio']
        predicted = snapshot['captured_ns'] + round(wait * 1e9)
        return (1. <= wait <= ARM_SECONDS and type(target['beat_monotonic_ns']) is int
                and abs(predicted-target['beat_monotonic_ns']) <= 200_000_000)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
