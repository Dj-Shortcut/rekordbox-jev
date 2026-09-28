"""Musical pacing context for Jev, never a scheduler or replacement decision."""
from .state import number
from .entry_timing import entry_context
from .transition_budget import COMPLETION_MARGIN_SECONDS, LAUNCH_ALIGNMENT_RESERVE_SECONDS
from .arrangement import context as arrangement_context
from .entry_grid import context as entry_grid_context

# Conservative style fallback when arrangement evidence is unavailable. This
# describes track position, not proof that a listener heard its main passage.
LAUNCH_PROGRESS_FLOOR = .75


def grid_hint(deck, track):
    """A regular-grid estimate, not detected arrangement or a launch deadline."""
    grid = track.get('beatgrid') or []
    if not grid or not number(deck.get('elapsed'), 0):
        return None
    first = grid[0]
    bpm = first.get('bpm')
    # OCR clock semantics under pitch changes have not been verified. Do not
    # invent phrase positions at a changed tempo or across a variable grid.
    if (not number(bpm, 60, 200) or not number(deck.get('bpm'), 60, 200)
            or abs(deck['bpm'] - bpm) > .05
            or any(g.get('meter') != '4/4' or not number(g.get('bpm'), 60, 200)
                   or abs(g['bpm'] - bpm) > .05 for g in grid)
            or not number(first.get('position_seconds'), 0)
            or not number(first.get('beat_in_bar'), 1, 4)
            or int(first['beat_in_bar']) != first['beat_in_bar']):
        return None
    downbeat = first['position_seconds'] + ((1 - int(first['beat_in_bar'])) % 4) * 60 / bpm
    bars = (deck['elapsed'] - downbeat) / (4 * 60 / bpm)
    if bars < 0:
        return None
    within = bars % 16
    return {'bars_since_first_downbeat': round(bars, 2),
            'bars_until_next_16_bar_group': round(16 - within, 2),
            'near_16_bar_group': within <= 1 or within >= 15,
            'basis': 'Estimated 16-bar grouping of exported constant 4/4 grid and displayed track position; '
                     'not an observed phrase, drop or breakdown, and not an exact native launch target.'}


def context(snapshot, transition, audible):
    decks = snapshot['decks']
    lead = (transition['outgoing'] if transition and not transition['handoff_endpoint_reached']
            else audible[0] if len(audible) == 1 else None)
    deck = decks.get(lead, {})
    bpm = deck.get('bpm')
    bar_seconds = 4 * 60 / bpm if number(bpm, 60, 200) else None
    remaining = deck.get('remaining')
    elapsed = deck.get('elapsed')
    total = elapsed + remaining if number(elapsed, 0) and number(remaining, 0) else None
    incoming = (transition.get('incoming') if transition and not transition['handoff_endpoint_reached']
                else ('B' if lead == 'A' else 'A') if lead and not transition else None)
    incoming_deck = decks.get(incoming, {})
    if not incoming_deck.get('track_id'):
        incoming = None
        incoming_deck = {}
    incoming_elapsed, incoming_remaining = incoming_deck.get('elapsed'), incoming_deck.get('remaining')
    incoming_total = (incoming_elapsed + incoming_remaining
                      if number(incoming_elapsed, 0) and number(incoming_remaining, 0) else None)
    known_durations = [v for v in (total, incoming_total) if number(v, 0) and v > 0]
    # User style preference only. No actions are removed, scheduled or selected.
    # Duration is not evidence of a climax or phrase boundary.
    preferred_bars = 64 if known_durations and min(known_durations) >= 300 else 32
    shorter_bars = 32 if preferred_bars == 64 else 16
    preferred = preferred_bars * bar_seconds if bar_seconds else None
    shorter = shorter_bars * bar_seconds if bar_seconds else None
    # This is a soft style window; native safety and Jev's choice remain separate.
    window = preferred + 16 * bar_seconds if bar_seconds else None
    # A long desired overlap must not pull entry into the first half of a song.
    # For the 300.4 s / 123 BPM live regression, 80 bars meant entry at 144.3 s.
    # Limit that soft window to the final quarter; Jev can shorten the blend.
    if window is not None and number(total, 0) and total > 0:
        window = min(window, total * (1 - LAUNCH_PROGRESS_FLOOR))
    since = transition.get('audible_mix_started_ns') if transition else None
    stamp = snapshot.get('captured_ns')
    overlap = ((stamp - since) / 1e9 if type(since) is int and type(stamp) is int
               and 0 <= since <= stamp else None)
    track = next((t for t in snapshot['library'] if t['id'] == deck.get('track_id')), {})
    mixing = transition is not None and not transition['handoff_endpoint_reached']
    running_remaining = [remaining] if number(remaining, 0) else []
    if incoming_deck.get('playing') is True and number(incoming_remaining, 0):
        running_remaining.append(incoming_remaining)
    limiting_remaining = min(running_remaining) if running_remaining else None
    available = (max(0., limiting_remaining - COMPLETION_MARGIN_SECONDS)
                 if mixing and limiting_remaining is not None else None)
    overlap_left = max(0., preferred - overlap) if preferred is not None and overlap is not None else None
    entry = entry_context(snapshot, lead, incoming, preferred)
    candidate = entry.get('candidate')
    if mixing and candidate and candidate['position'] in ('within', 'after') and available is not None:
        available = min(available, max(0., candidate['seconds_until_section_end']-COMPLETION_MARGIN_SECONDS))
    fallback_wait = max(0., remaining-window) if window and number(remaining, 0) else None
    use_candidate = bool(not mixing and entry['status'] == 'candidate' and candidate
                         and candidate['enough_time_for_short_mix'])
    # The candidate is a preference that Jev must assess, not a proven section.
    launch_reserve = COMPLETION_MARGIN_SECONDS + LAUNCH_ALIGNMENT_RESERVE_SECONDS
    urgency_margin = ((16 * 60 / bpm + launch_reserve) if use_candidate and bar_seconds
                      else (shorter + launch_reserve if shorter else None))
    # A longer desired blend must not pull fallback entry before the late window.
    # A return without usable blend time cannot postpone that fallback.
    if not use_candidate and urgency_margin is not None and window is not None:
        urgency_margin = min(urgency_margin, window)
    launch_wait = candidate['seconds_until_start'] if use_candidate else fallback_wait
    entry_grid = entry_grid_context(snapshot, lead, incoming, entry)
    preferred_slot = next((s for s in entry_grid['slots']
                           if s['id'] == entry_grid['selected_preference']), None)
    if preferred_slot:
        launch_wait = preferred_slot['seconds_until']
    arrangement = arrangement_context(snapshot, lead, incoming, launch_wait)
    entry_target = transition.get('entry_target', {}) if transition else {}
    target_stamp = entry_target.get('beat_monotonic_ns')
    entry_age = ((stamp-target_stamp)/1e9 if type(stamp) is int and type(target_stamp) is int else None)
    blend_after_target = ((since-target_stamp)/1e9
                          if type(since) is int and type(target_stamp) is int else None)
    return {
        'entry_preference': entry,
        'arrangement': arrangement,
        'entry_grid': entry_grid,
        'seconds_since_planned_entry': entry_age,
        'confirmed_blend_after_entry_seconds': blend_after_target,
        'seconds_until_fallback_launch_window': fallback_wait,
        'style': 'A flowing musical set, not a rapid control demonstration. These are preferences, not forced actions.',
        'lead_deck': lead,
        'lead_track_duration_seconds': total,
        'lead_track_progress_fraction': round(elapsed / total, 3) if total and total > 0 else None,
        'incoming_deck': incoming,
        'incoming_track_duration_seconds': incoming_total,
        'incoming_remaining_seconds': incoming_remaining,
        'shortest_known_track_duration_seconds': min(known_durations) if known_durations else None,
        'preferred_overlap_bars': preferred_bars,
        'shorter_overlap_bars': shorter_bars,
        'preferred_overlap_seconds': preferred,
        'shorter_overlap_seconds': shorter,
        'launch_progress_floor_preference': LAUNCH_PROGRESS_FLOOR,
        'launch_window_remaining_seconds': window,
        'seconds_until_preferred_launch_window': launch_wait,
        'completion_reserve_seconds': COMPLETION_MARGIN_SECONDS,
        'launch_alignment_reserve_seconds': LAUNCH_ALIGNMENT_RESERVE_SECONDS if not mixing else 0.,
        'ending_needs_priority': limiting_remaining <= (COMPLETION_MARGIN_SECONDS if mixing else urgency_margin)
            if urgency_margin is not None and limiting_remaining is not None else None,
        'overlap_time_available_before_finish_seconds': available,
        'confirmed_audible_overlap_seconds': overlap,
        'seconds_to_preferred_overlap': overlap_left,
        'suggested_remaining_overlap_seconds': min(overlap_left, available)
            if overlap_left is not None and available is not None else None,
        'grid_hint': grid_hint(deck, track),
        'timing_basis': 'A proposed final sustained return takes preference over the percentage fallback; '
                        'Jev must assess its suitability and post-peak interpretation. No fixed maximum track playtime. '
                        'Overlap uses monotonic screenshot timestamps from a confirmed audible blend, '
                        'not muted playback or the number of HOLD answers. '
                        'confirmed_blend_after_entry_seconds is the conservative after-frame delay '
                        'from a planned silent launch, not a measured audio onset. Entry window is approximate '
                        'using displayed remaining time and current tempo, capped to the final quarter '
                        'when total duration is known. This is a conservative style fallback, not detected '
                        'song structure or proof a climax was heard. Long overlap and grid hints do not '
                        'justify earlier entry. The shorter known track sets the overlap preference. '
                        'The smaller remaining clock of the running decks limits the blend, reserving '
                        'the same 30 seconds used by the completion guard. Before launch, budget an additional '
                        '8 seconds for launch/alignment; this is an estimate, not a latency guarantee. '
                        'A candidate without room for a short blend uses the late-track fallback. '
                        'A stopped successor has no running deadline. '
                        'Unknown values are null.'}
