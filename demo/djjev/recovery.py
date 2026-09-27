"""Bounded reconciliation of a returned, readable mixer operation.

This is not permission to replay relative input. A new observation and policy
answer are required after reconciliation. Unknown/unfinished native calls fail
closed, as do changed tracks, playback, alignment, or unreadable controls.
"""
from copy import deepcopy
from .state import number

TARGET_REASONS = frozenset(('crossfader_not_at_target', 'bass_direction_not_confirmed'))


def mixer_state(snapshot, titles):
    if not isinstance(snapshot, dict) or snapshot.get('valid') is not True or snapshot.get('folder') != '26':
        return None
    mixer = snapshot.get('mixer', {})
    if mixer.get('aligned') is not True or not number(mixer.get('cross'), 0, 1):
        return None
    positions = [mixer['cross']]
    for name in ('A', 'B'):
        deck = snapshot.get('decks', {}).get(name, {})
        if (deck.get('title') != titles.get(name) or not deck.get('track_id')
                or deck.get('playing') is not True or not number(deck.get('channel'), .9, 1)):
            return None
        for band in ('low', 'mid', 'high', 'trim'):
            value = deck.get('eq_position', {}).get(band)
            if not number(value, -1, .08):
                return None
            positions.append(value)
    return positions


class MixReadbackIncomplete(RuntimeError):
    def __init__(self, snapshot, reasons):
        super().__init__('Mixerdoel niet volledig bereikt; actuele stand opnieuw controleren.')
        self.snapshot = deepcopy(snapshot)
        self.reasons = list(reasons)


def reconcileable_mix(result, snapshot, titles):
    verification = result.get('verification', {})
    reasons = verification.get('reasons')
    return (result.get('verified') is False and result.get('dispatched') is True
            and result.get('commandsSent') is True
            and type(result.get('stepsCompleted')) is int
            and type(result.get('stepsRequested')) is int
            and result['stepsCompleted'] > 0
            and result['stepsCompleted'] == result.get('stepsRequested')
            and isinstance(reasons, list) and bool(reasons)
            and all(isinstance(r, str) and r in TARGET_REASONS for r in reasons)
            and verification.get('aligned') is True
            and verification.get('actualTitles') == verification.get('expectedTitles')
            == {'1': titles['A'], '2': titles['B']}
            and isinstance(verification.get('playing'), dict)
            and all(verification['playing'].get(d) is True for d in ('1', '2'))
            and mixer_state(snapshot, titles) is not None)


def control_state(snapshot):
    """Project readable controls, excluding advancing clocks and effect cooldown."""
    if not isinstance(snapshot, dict) or snapshot.get('valid') is not True or snapshot.get('folder') != '26':
        return None
    decks = snapshot.get('decks', {})
    mixer = snapshot.get('mixer', {})
    if set(decks) != {'A', 'B'} or not number(mixer.get('cross'), 0, 1):
        return None
    aligned = mixer.get('aligned')
    # There is no beat-pair alignment to read with a confirmed stopped/empty
    # deck. This reconciles controls only; mixer_state still demands alignment.
    if (type(aligned) is not bool and not (aligned is None and
            any(deck.get('playing') is False for deck in decks.values()))):
        return None
    exact = [snapshot.get('folder'), mixer.get('assignments'), aligned]
    positions = [mixer['cross']]
    tempos = []
    for name in ('A', 'B'):
        deck = decks[name]
        if (not isinstance(deck.get('title'), str)
                or any(type(deck.get(k)) is not bool for k in ('playing', 'sync', 'master'))
                or not number(deck.get('channel'), 0, 1)):
            return None
        exact.extend(deck.get(k) for k in ('title', 'track_id', 'playing', 'sync', 'master'))
        exact.append(deck.get('eq_neutral'))
        positions.append(deck['channel'])
        for band in ('low', 'mid', 'high', 'trim'):
            value = deck.get('eq_position', {}).get(band)
            if not number(value, -1, 1):
                return None
            positions.append(value)
        bpm = deck.get('bpm')
        if bpm is not None and not number(bpm, 1, 999):
            return None
        tempos.append(bpm)
    return deepcopy((exact, positions, tempos))


def same_control_state(previous, current):
    """Allow visual control jitter, while comparing discrete state exactly."""
    if previous is None or current is None or previous[0] != current[0]:
        return False
    return (all(abs(a-b) <= .025 for a, b in zip(previous[1], current[1]))
            and all(a == b if a is None or b is None else abs(a-b) <= .1
                    for a, b in zip(previous[2], current[2])))
