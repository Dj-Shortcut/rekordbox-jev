"""Bounded reconciliation of a returned, readable mixer operation.

This is not permission to replay relative input. A new observation and policy
answer are required after reconciliation. Unknown/unfinished native calls fail
closed, as do changed tracks, playback, alignment, or unreadable controls.
"""
from copy import deepcopy
from .state import number

TARGET_REASONS = frozenset(('crossfader_not_at_target', 'bass_direction_not_confirmed'))


def mixer_state(snapshot, titles):
    if not isinstance(snapshot, dict) or snapshot.get('valid') is not True:
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
