"""Bounded EQ vocabulary, not a transition sequence. Values are pointer angles, not dB."""
from .state import number

BASS_TARGETS = {
    'A': {'A': 0., 'B': -.6}, 'B': {'A': -.6, 'B': 0.},
    'balanced': {'A': -.3, 'B': -.3},
    'A_gentle': {'A': 0., 'B': -.24}, 'B_gentle': {'A': -.24, 'B': 0.},
    'A_deep': {'A': 0., 'B': -.84}, 'B_deep': {'A': -.84, 'B': 0.},
}
TONE_TARGETS = {
    'A_soft': {'A': -.16, 'B': 0.}, 'B_soft': {'A': 0., 'B': -.16},
    'A_cut': {'A': -.32, 'B': 0.}, 'B_cut': {'A': 0., 'B': -.32},
    'neutral': {'A': 0., 'B': 0.},
}

def position(deck, band):
    return deck.get('bass') if band == 'low' else deck.get('eq_position', {}).get(band)

def readable(decks, band):
    return all(number(position(d, band), -1, 0) for d in decks.values())

def reached(decks, band, targets):
    return all(number(position(decks[d], band)) and abs(position(decks[d], band)-goal) <= .07
               and (goal != 0 or decks[d]['eq_neutral'].get(band) is True)
               for d, goal in targets.items())
