"""Hard local execution rules. Jev cannot relax these rules or authorize a fallback."""
import math
import time


class MixBlocked(RuntimeError):
    pass


def require_aligned(state, now=None):
    now = time.monotonic() if now is None else now
    capture = state.get('sampledAtMonotonicNS')
    if type(capture) not in (int, float) or not 0 <= now-capture/1e9 <= 1.5:
        raise MixBlocked('Geen recente waarneming; faders blijven staan.')
    if state.get('layoutCalibrated') is not True:
        raise MixBlocked('Onbekende indeling; faders blijven staan.')
    mixer = state.get('mixer') or {}
    error = mixer.get('red_bar_alignment_error_px')
    if (mixer.get('red_bar_aligned') is not True or type(error) not in (float,int)
            or not math.isfinite(error) or not 0 <= error <= 3):
        raise MixBlocked('Rode maatmarkeringen lopen niet aantoonbaar gelijk; faders blijven staan.')


def require_neutral(state, decks=(1,2)):
    eq = (state.get('mixer') or {}).get('eq_neutral') or {}
    if any(eq.get(str(deck), {}).get(band) is not True
           for deck in decks for band in ('high','mid','low','trim')):
        raise MixBlocked('EQ nog niet aantoonbaar neutraal; overgang is niet klaar.')


def guarded_crossfader(value, observe, dispatch):
    require_aligned(observe())
    return dispatch({'command':'crossfader','value':float(value)})


def paired_bass_step(outgoing, incoming, pixels, observe, dispatch):
    if {outgoing,incoming} != {1,2} or not 0 < pixels <= 5:
        raise ValueError('Een bass-stap vereist twee verschillende decks en maximaal 5 pixels.')
    state = observe()
    require_aligned(state)
    if len(state.get('decks',[])) != 2 or any(d.get('playingIndicator') is not True for d in state['decks']):
        raise MixBlocked('Beide decks moeten spelen voor de bass-overdracht.')
    # One native operation; both deltas are validated before either gesture is sent.
    # The GUI has a single pointer. These are tightly paired, not literally simultaneous events.
    return dispatch({'command':'eqPair','outgoing':outgoing,'incoming':incoming,'pixels':float(pixels)})
