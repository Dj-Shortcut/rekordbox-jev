"""Musical pacing context for Jev, never a scheduler or replacement decision."""
from .state import number


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
    preferred = 32 * bar_seconds if bar_seconds else None
    shorter = 16 * bar_seconds if bar_seconds else None
    # This is a soft style window; native safety and Jev's choice remain separate.
    window = preferred + 16 * bar_seconds if bar_seconds else None
    since = transition.get('audible_mix_started_ns') if transition else None
    stamp = snapshot.get('captured_ns')
    overlap = ((stamp - since) / 1e9 if type(since) is int and type(stamp) is int
               and 0 <= since <= stamp else None)
    track = next((t for t in snapshot['library'] if t['id'] == deck.get('track_id')), {})
    mixing = transition is not None and not transition['handoff_endpoint_reached']
    return {
        'style': 'A flowing musical set, not a rapid control demonstration. These are preferences, not forced actions.',
        'lead_deck': lead,
        'lead_track_progress_fraction': round(elapsed / total, 3) if total and total > 0 else None,
        'preferred_overlap_bars': 32,
        'shorter_overlap_bars': 16,
        'preferred_overlap_seconds': preferred,
        'shorter_overlap_seconds': shorter,
        'seconds_until_preferred_launch_window': max(0., remaining - window)
            if window and number(remaining, 0) else None,
        'ending_needs_priority': remaining <= (12 if mixing else shorter + 12)
            if shorter and number(remaining, 0) else None,
        'overlap_time_available_before_finish_seconds': max(0., remaining - 12)
            if mixing and number(remaining, 0) else None,
        'confirmed_audible_overlap_seconds': overlap,
        'seconds_to_preferred_overlap': max(0., preferred - overlap)
            if preferred is not None and overlap is not None else None,
        'grid_hint': grid_hint(deck, track),
        'timing_basis': 'Overlap uses monotonic screenshot timestamps from a confirmed audible blend, '
                        'not muted playback or the number of HOLD answers. Entry window is approximate '
                        'using displayed remaining time and current tempo. Unknown values are null.'}
