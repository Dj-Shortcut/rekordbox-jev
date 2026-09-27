"""Folder-26 entry preference from measured returns, never a kick/climax oracle.

Whole-file four-bar measurements are precomputed offline. This module proposes
bounded evidence and independent judgments; it never schedules native playback.
"""
from copy import deepcopy
from .state import number


def activity_sections(rows):
    """Group sustained low-band + attack activity, retaining every group.

    These deliberately conservative thresholds are a testable heuristic, not a
    trained kick detector. A continuous bassline or other percussion may qualify.
    Four-bar averaging limits the precision of every proposed entrance.
    """
    values = sorted(r['low_energy_dbfs_estimate'] for r in rows
                    if r['energy_dbfs'] > -60 and r['bars'] >= 3.99)
    if not values:
        return {'groups': [], 'last_return': None, 'status': 'no_activity'}
    reference = values[int((len(values)-1)*.75)]
    threshold = max(-55., reference-6.)
    groups, current = [], []
    for row in rows:
        active = (row['bars'] >= 3.99 and row['energy_dbfs'] > -50
                  and row['low_energy_dbfs_estimate'] >= threshold
                  and row['low_fraction'] >= .08 and row['onsets_per_beat'] >= .5)
        if active:
            current.append(row)
        elif current:
            groups.append(current); current = []
    if current:
        groups.append(current)
    sustained = [g for g in groups if sum(r['bars'] for r in g) >= 16]
    summaries = [{'start_seconds': round(g[0]['start_seconds'], 3),
                  'end_seconds': round(g[-1]['end_seconds'], 3),
                  'start_bar': g[0]['start_bar'],
                  'bars': round(sum(r['bars'] for r in g), 3)} for g in sustained]
    candidate = None
    if sustained:
        last = sustained[-1]
        index = rows.index(last[0])
        # A return requires >= 4 bars of substantially lower bass activity, and
        # a previous sustained group. Never call the track opening a return.
        if len(sustained) >= 2 and index > 0:
            previous = rows[index-1]
            rise = last[0]['low_energy_dbfs_estimate']-previous['low_energy_dbfs_estimate']
            if previous['bars'] >= 3.99 and rise >= 6:
                candidate = {**summaries[-1], 'low_band_return_db': round(rise, 2)}
    # Bound payload without silently replacing a complex track by its last few
    # groups: excessive fragmentation makes the candidate unavailable.
    if len(summaries) > 32:
        return {'groups': [], 'last_return': None, 'status': 'too_fragmented'}
    return {'groups': summaries, 'last_return': candidate,
            'status': 'candidate' if candidate else 'no_clear_return',
            'low_band_threshold_dbfs': round(threshold, 2),
            'basis': 'Four-bar source measurements: sustained low-band energy plus detected attacks. '
                     'Not confirmed kicks, silence gaps, phrase boundaries, vocals or a climax. '
                     'Last means last qualifying group across the whole file; short later bursts may exist.'}


def entry_context(snapshot, lead, incoming, preferred_seconds):
    """Source timestamps stay source timestamps; countdowns use wall seconds."""
    audio = (snapshot.get('audio_windows') or {}).get(lead, {})
    structure = audio.get('entry_structure') if audio.get('status') == 'available' else None
    base = {'status': 'missing_evidence', 'candidate': None,
            'climax_confirmed': False, 'phrase_confirmed': False,
            'preference': 'Prefer entry at the last suitable sustained kick return after the main high point; '
                          'a folder-26 style heuristic, not a rule for every song. Long tracks may play longer.'}
    if not structure:
        return base
    base.update(status=structure['status'], evidence=deepcopy(structure))
    candidate = structure.get('last_return')
    if not candidate:
        return base
    position, ratio = audio['position_seconds'], audio['tempo_ratio']
    remaining = snapshot['decks'][lead].get('remaining')
    if not number(remaining, 0) or not number(ratio, .94, 1.06):
        return base
    # Reserve time for starting/alignment and completing the handoff. The
    # desired 32/64-bar overlap never moves an entrance before the candidate.
    until = (candidate['start_seconds']-position)/ratio
    section_end = (candidate['end_seconds']-position)/ratio
    outgoing_budget = (remaining-max(0., candidate['start_seconds']-position))/ratio
    incoming_deck = snapshot['decks'].get(incoming, {})
    incoming_budget = incoming_deck.get('remaining')
    incoming_audio = (snapshot.get('audio_windows') or {}).get(incoming, {})
    incoming_ratio = incoming_audio.get('tempo_ratio') if incoming_audio.get('status') == 'available' else None
    if not number(incoming_ratio, .94, 1.06):
        track = next((t for t in snapshot['library'] if t['id'] == incoming_deck.get('track_id')), {})
        original, live = track.get('bpm'), incoming_deck.get('bpm')
        incoming_ratio = live/original if number(original, 60, 200) and number(live, 60, 200) else None
    if number(incoming_budget, 0):
        # Use conservative maximum supported speed if source tempo is unknown.
        incoming_budget /= incoming_ratio if number(incoming_ratio, .94, 1.06) else 1.06
        if incoming_deck.get('playing'):
            incoming_budget = max(0., incoming_budget-max(0., until))
    budgets = [max(0., outgoing_budget), max(0., section_end-max(0., until))]
    if number(incoming_budget, 0):
        budgets.append(incoming_budget)
    usable = max(0., min(budgets)-12.)
    bpm = snapshot['decks'][lead].get('bpm')
    minimum = 8*60/bpm if number(bpm, 60, 200) else 8.
    base.update(status='candidate' if section_end > 0 else 'candidate_passed',
                candidate={**candidate, 'seconds_until_start': round(max(0., until), 3),
                           'seconds_until_section_end': round(max(0., section_end), 3),
                           'overlap_budget_seconds': round(usable, 3),
                           'suggested_overlap_seconds': round(min(preferred_seconds, usable), 3)
                               if preferred_seconds is not None else None,
                           'enough_time_for_short_mix': usable >= minimum,
                           'requires_shorter_mix': preferred_seconds > usable
                               if preferred_seconds is not None else None,
                           'position': 'before' if until > 0 else 'within' if section_end > 0 else 'after',
                           'boundary_precision': '4-bar window, estimated entrance; not exact detected phrase'})
    return base


def attach_questions(request):
    state = request['state']
    if (not request['questions'] or len(state['continuity']['audible_decks']) != 1
            or state.get('transition') is not None or not state.get('audio_context')
            or state.get('preparation',{}).get('required_now')):
        return
    premise = ('Judge musical_timing.entry_preference for its lead deck using the whole source_contour '
               'and measured activity groups. Each question is independent; other answers in this call '
               'are unknown. A candidate is not verified kick or climax evidence. ')
    rows = {
        'kick_pattern': ('Do the measured groups plausibly match sustained kick sections separated by breaks?',
                         {'plausible': 'The low-band and attack pattern supports this interpretation, without proving instrument identity.',
                          'unclear': 'Evidence is missing, ambiguous or inconsistent with that pattern.'}),
        'last_section': ('Is the proposed last_return a plausible final sustained kick return for this track?',
                         {'supported': 'Whole-track evidence supports the proposed final return.',
                          'unsuitable': 'The proposed group is misleading or unsuitable as the final return.',
                          'unknown': 'No candidate or insufficient evidence.'}),
        'post_peak': ('At that proposed return, does the musical development appear to be past its main high point?',
                      {'past': 'Earlier and later evidence supports a post-development interpretation; this remains an inference.',
                       'ahead': 'A substantial later development still appears to follow.',
                       'unknown': 'Measurements cannot establish where the main high point lies. A maximum RMS alone is insufficient.'}),
        'entry_fit': ('Assuming entry at the proposed return, does this passage suit introducing the successor?',
                      {'suitable': 'A plausible entry without known passage conflict; shorten the blend to its computed budget.',
                       'protect': 'Let this passage develop further before overlapping.',
                       'unsuitable': 'This candidate is not a suitable entry.',
                       'unknown': 'Insufficient evidence; vocals and melody content are unmeasured.'}),
    }
    for key, (question, criteria) in rows.items():
        request['questions'][key] = {'type': 'choice', 'instructions': premise+question, 'criteria': criteria}


def launch_consistent(decision, timing):
    """Reject contradictory PLAY; never substitute a fabricated model choice.

    Urgency wins. Unknown structure uses the existing late-track fallback, so
    missing semantic labels cannot trap the set in HOLD indefinitely.
    """
    if not decision.get('transport', '').startswith('play_') or timing.get('ending_needs_priority'):
        return True
    answers = decision.get('answers', {})
    if 'last_section' not in answers:
        return True
    choices = {k: v.get('choice') for k, v in answers.items()}
    entry = timing['entry_preference']
    candidate = entry.get('candidate')
    if choices.get('post_peak') == 'ahead' or choices.get('entry_fit') == 'protect':
        return False
    supported = (choices.get('kick_pattern') == 'plausible' and choices.get('last_section') == 'supported'
                 and choices.get('post_peak') == 'past' and choices.get('entry_fit') == 'suitable')
    if supported and candidate and entry['status'] == 'candidate' and candidate['enough_time_for_short_mix']:
        return candidate['seconds_until_start'] <= 0
    return (timing.get('seconds_until_fallback_launch_window') or 0) <= 0
