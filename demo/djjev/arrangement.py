"""Conditional intro-to-drop planning, never a semantic drop detector."""
from copy import deepcopy

from .state import number
from .transition_budget import COMPLETION_MARGIN_SECONDS, LAUNCH_ALIGNMENT_RESERVE_SECONDS


def first_drop_evidence(rows, structure):
    """Project a conservative hypothesis from existing validated four-bar rows.

    A loud opening is not silently replaced by a later return and called the
    first drop. Missing/ambiguous arrangements remain explicit alternatives.
    """
    base = {'status': 'no_clear_drop', 'candidate': None, 'drop_confirmed': False,
            'basis': 'Four-bar source energy/low-band/attack changes. A possible first drop, '
                     'not verified song structure or an exact kick timestamp.'}
    groups = structure.get('groups', [])
    if structure.get('status') == 'too_fragmented' or not rows or not groups:
        return base
    first = groups[0]
    if first['start_bar'] == 0:
        return {**base, 'status': 'starts_with_activity'}
    index = next((i for i, row in enumerate(rows)
                  if row['start_bar'] == first['start_bar']), None)
    if index is None or index == 0:
        return base
    before, onset = rows[index-1], rows[index]
    energy_rise = onset['energy_dbfs'] - before['energy_dbfs']
    low_rise = onset['low_energy_dbfs_estimate'] - before['low_energy_dbfs_estimate']
    if (before['bars'] < 3.99 or low_rise < 6 or energy_rise < 3
            or onset['onsets_per_beat'] < .5):
        return base
    return {**base, 'status': 'possible_first_drop', 'candidate': {
        'source_seconds': first['start_seconds'], 'source_bar': first['start_bar'],
        'sustained_bars': first['bars'], 'energy_rise_db': round(energy_rise, 2),
        'low_band_rise_db': round(low_rise, 2), 'precision_bars': 4}}


def context(snapshot, outgoing, incoming, launch_wait=0.):
    """Compare a successor's actual starting point/tempo with outgoing space."""
    decks = snapshot['decks']
    audio = snapshot.get('audio_windows') or {}
    inc, out = decks.get(incoming, {}), decks.get(outgoing, {})
    source = audio.get(incoming, {})
    evidence = source.get('first_drop') if source.get('status') == 'available' else None
    result = {'strategy': 'Begin blending at the outgoing entry; build toward the incoming first drop '
              'as a possible bass/handoff moment, only when both arrangements support it.',
              'incoming': incoming, 'outgoing': outgoing,
              'first_drop': deepcopy(evidence) if evidence else {
                  'status': 'missing_evidence', 'candidate': None, 'drop_confirmed': False},
              'seconds_to_incoming_drop': None, 'beats_to_incoming_drop': None,
              'outgoing_seconds_at_drop': None, 'handover_fit': 'unknown',
              'fallback': 'Use another suitable entry or a shorter measured blend; do not wait '
                          'for an invented drop, skip a cue, or impose a verse/build/drop template.'}
    candidate = (evidence or {}).get('candidate')
    ratio = source.get('tempo_ratio')
    if not candidate or not number(ratio, .94, 1.06):
        return result
    # PLAY resets a stopped successor to the file beginning. Running decks use
    # their actual source position; a seek/reload must not retain a countdown.
    position = inc.get('elapsed') if inc.get('playing') is True else 0.
    if not number(position, 0) or not number(candidate.get('source_seconds'), 0):
        return result
    seconds = (candidate['source_seconds'] - position) / ratio
    result['seconds_to_incoming_drop'] = round(seconds, 3)
    if number(inc.get('bpm'), 60, 200):
        result['beats_to_incoming_drop'] = round(seconds * inc['bpm'] / 60, 2)
    if seconds <= 0:
        result['handover_fit'] = 'drop_already_passed'
        return result
    out_audio = audio.get(outgoing, {})
    out_ratio = out_audio.get('tempo_ratio') if out_audio.get('status') == 'available' else None
    if not number(out_ratio, .94, 1.06) or not number(out.get('remaining'), 0):
        return result
    wait = max(0., launch_wait) if inc.get('playing') is not True and number(launch_wait) else 0.
    after = out['remaining'] / out_ratio - wait - seconds
    result['outgoing_seconds_at_drop'] = round(after, 3)
    if after < COMPLETION_MARGIN_SECONDS:
        result['handover_fit'] = 'too_late_for_planned_handoff'
    elif inc.get('playing') is not True and seconds <= LAUNCH_ALIGNMENT_RESERVE_SECONDS:
        result['handover_fit'] = 'intro_too_short_for_planned_blend'
    else:
        result['handover_fit'] = 'possible_if_arrangement_supported'
    return result


GUIDANCE = (
    'Read musical_timing.arrangement for BOTH tracks. Begin the blend at the chosen outgoing entry '
    'and build toward the incoming first drop as a possible bass handoff, not a universal recipe. '
    'The source only proposes a possible drop; never call it confirmed. If the track starts with '
    'full activity, has no clear drop, has already passed it, has insufficient intro or would '
    'reach it too late, choose an alternative shorter blend in time. Never delay until an emergency '
    'to preserve this template. Do not infer vocals or structure from genre/title. '
    'When the approach fits, let the intro breathe and progressively give the incoming bass room '
    'toward the proposed drop; avoid transferring bass back and forth while waiting. '
    'Other simultaneous answers are unknown: judge the evidence independently. '
)


def attach_question(request):
    questions, state = request['questions'], request['state']
    timing = state['musical_timing']
    arrangement = timing.get('arrangement', {})
    if (not questions or state['preparation']['required_now'] or not state.get('audio_context')
            or not arrangement.get('incoming') or not arrangement.get('outgoing')
            or not any(k == 'mix' or k.startswith('play_') for k in questions['transport']['criteria'])):
        return
    questions['arrangement_fit'] = {'type': 'choice', 'instructions': GUIDANCE +
        'Does beginning this blend and building to the proposed incoming drop fit both tracks?',
        'criteria': {'supported': 'Measured evidence supports this approach as a hypothesis and the timing fits.',
                     'alternative': 'The arrangement or time budget needs a different/shorter transition.',
                     'unknown': 'Insufficient evidence: use the available measured timing without inventing a drop.'}}
    for name in ('transport', 'crossfader', 'bass', 'duration'):
        if name in questions:
            questions[name]['instructions'] = GUIDANCE + questions[name]['instructions']
