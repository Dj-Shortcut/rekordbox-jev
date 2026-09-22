"""Execute Jev's chosen control; no track selection or musical policy here.

All inputs are bounded. Every operation uses the actual native after-frame.
Relative inputs are never replayed after an unconfirmed result.
"""
from copy import deepcopy
import math
import os
import time

from contextual_observation import adapt_observation


class ControlBlocked(RuntimeError):
    def __init__(self, message, *, retryable=False, dispatched=False):
        super().__init__(message)
        self.retryable, self.dispatched = retryable, dispatched


def observe(native):
    return native({'command': 'observe', 'fast': True})


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def deck_number(value):
    if value in ('A', '1', 1):
        return 1
    if value in ('B', '2', 2):
        return 2
    raise ControlBlocked('Onbekend doeldeck.')


def title(raw, deck):
    matches = [d for d in raw.get('decks', []) if d.get('deck') == deck]
    if len(matches) != 1 or not isinstance(matches[0].get('title'), str) or not matches[0]['title'].strip():
        raise ControlBlocked('Tracktitel is niet betrouwbaar uitgelezen.', retryable=True)
    return matches[0]['title']


def playing(raw, deck):
    value = raw.get('playingIndicators', {}).get('deck'+str(deck))
    if type(value) is not bool:
        raise ControlBlocked('Afspeelstand is onbekend.', retryable=True)
    return value


def mixer(raw):
    value = raw.get('mixer', {})
    if value.get('deck_assignments') != {'1': 'left', '2': 'right'}:
        raise ControlBlocked('Crossfadertoewijzing is niet bevestigd.', retryable=True)
    return value


def cross(raw):
    value = mixer(raw).get('crossfader_position')
    if not number(value) or not 0 <= value <= 1:
        raise ControlBlocked('Crossfaderstand is onbekend.', retryable=True)
    return value


def muted(raw, deck):
    channel = raw.get('faders', {}).get('deck'+str(deck))
    if not number(channel) or not 0 <= channel <= 1:
        raise ControlBlocked('Kanaalfaderstand is onbekend.', retryable=True)
    position = cross(raw)
    return channel <= .01 or (position >= .99 if deck == 1 else position <= .01)


def neutral(raw, deck, band='low'):
    return mixer(raw).get('eq_neutral', {}).get(str(deck), {}).get(band) is True


def bass(raw, deck):
    if neutral(raw, deck):
        return 0.0
    value = mixer(raw).get('eq_position', {}).get(str(deck), {}).get('low')
    if not number(value) or not -1 <= value <= 1:
        raise ControlBlocked('Bassknop niet betrouwbaar uitgelezen.', retryable=True)
    return value


def require_fresh(raw, now_ns):
    stamp = raw.get('sampledAtMonotonicNS')
    if (raw.get('layoutCalibrated') is not True or type(stamp) is not int
            or not 0 <= now_ns()-stamp <= 3_000_000_000):
        raise ControlBlocked('Rekordbox-waarneming is verouderd of onleesbaar.', retryable=True)


def require_aligned(raw):
    if mixer(raw).get('red_bar_aligned') is not True:
        raise ControlBlocked('Rode beatgroepen niet gelijk; faders blijven staan.', retryable=True)


def execute(action, raw, tracks, session, *, native, now_ns=time.monotonic_ns):
    """Return an observed after-frame and bookkeeping, never a fabricated state."""
    if not isinstance(action, dict) or not isinstance(action.get('parameters'), dict):
        raise ControlBlocked('Ongeldige DJ-bediening.')
    require_fresh(raw, now_ns)
    name, params = action.get('action'), action['parameters']
    expected = {str(d): title(raw, d) for d in (1, 2)}
    supplied = action.get('expected_tracks')
    if supplied is not None:
        observed = adapt_observation(raw, tracks, now_ns=now_ns(), session=session)['current']
        # The model refers to stable library identities; native control guards
        # independently compare the exact freshly observed titles.
        identities = {str(deck_number(k)): v for k, v in supplied.items()}
        if identities != {'1': observed['A']['track'], '2': observed['B']['track']}:
            raise ControlBlocked('Tracks veranderden na Jevs keuze.', retryable=True)
    updated = deepcopy(session)
    sent = False

    def send(payload):
        nonlocal raw, sent
        mutating = payload['command'] not in ('observe', 'status', 'activate', 'openFolder26')
        result = native({**payload, 'clientPID': os.getpid()})
        if not isinstance(result, dict):
            raise ControlBlocked('Bediening niet bevestigd.', dispatched=sent or mutating)
        if mutating and result.get('dispatched') is not False:
            sent = True
        after = result.get('after')
        if mutating and not isinstance(after, dict):
            raise ControlBlocked('Bediening heeft geen nieuwe waarneming teruggegeven; niet herhaald.', dispatched=sent)
        if isinstance(after, dict):
            require_fresh(after, now_ns)
            if payload['command'] != 'loadChosenTrack' and {str(d): title(after, d) for d in (1, 2)} != expected:
                raise ControlBlocked('Track veranderde tijdens de handeling; niet herhaald.', dispatched=sent)
            raw = after
        return result

    def refresh():
        nonlocal raw
        raw = observe(native)
        require_fresh(raw, now_ns)
        if {str(d): title(raw, d) for d in (1, 2)} != expected:
            raise ControlBlocked('Tracks veranderden tijdens bediening.', dispatched=sent)
        return raw

    def dispatch_guard(seconds=15):
        return {'expectedTracks': expected, 'notAfterMonotonicNS': now_ns()+int(seconds*1e9)}

    def reset(deck, bands):
        wanted = [b for b in bands if not neutral(raw, deck, b)]
        if wanted:
            result = send({'command': 'eqReset', 'deck': deck, 'bands': wanted,
                           **dispatch_guard()})
            if result.get('verified') is not True or not all(neutral(raw, deck, b) for b in wanted):
                raise ControlBlocked('EQ-reset niet bevestigd; niet herhaald.', dispatched=sent)

    def key_action(deck, what):
        send({'command': 'action', 'action': f'deck{deck}.{what}',
              'expectedTrack': expected[str(deck)], **dispatch_guard()})
        require_fresh(raw, now_ns)

    def set_playing(deck, desired):
        result = send({'command': 'setPlayback', 'deck': deck, 'playing': desired,
                       'expectedTrack': expected[str(deck)], **dispatch_guard()})
        if result.get('verified') is not True or playing(raw, deck) is not desired:
            raise ControlBlocked('Afspeelhandeling niet bevestigd; niet herhaald.', dispatched=sent)

    def track_for(deck):
        adapted = adapt_observation(raw, tracks, now_ns=now_ns(), session=session)
        label = 'A' if deck == 1 else 'B'
        identifier = adapted['current'][label]['track']
        found = [t for t in adapted['library'] if t['id'] == identifier]
        if len(found) != 1:
            raise ControlBlocked('Geladen muziek niet uniek bekend in map 26.', dispatched=sent)
        return found[0], adapted['current'][label]

    def beat_launch(incoming, outgoing):
        if playing(raw, incoming) or not playing(raw, outgoing) or not muted(raw, incoming):
            raise ControlBlocked('Inkomend deck is niet gestopt en onhoorbaar.', retryable=not sent, dispatched=sent)
        info, state = track_for(incoming)
        _, out = track_for(outgoing)
        mm = mixer(raw)
        if (mm.get('master_lit', {}).get(str(outgoing)) is not True
                or mm.get('beat_sync_lit', {}).get(str(incoming)) is not True
                or not number(out.get('bpm')) or not number(state.get('bpm'))
                or abs(state['bpm']-out['bpm']) > .05):
            raise ControlBlocked('Master, sync of tempo nog niet bevestigd.', dispatched=sent)
        source = next((t for t in tracks if t.get('file') == info['file']), {})
        grid = source.get('beatgrid') or []
        if (not grid or grid[0].get('beat_in_bar') not in (1, 2, 3, 4)
                or not number(grid[0].get('position_seconds')) or not number(grid[0].get('bpm'))
                or grid[0]['bpm'] <= 0 or not number(source.get('original_bpm'))):
            raise ControlBlocked('Eerste beatpositie ontbreekt voor deze track.', dispatched=sent)
        next_downbeat = grid[0]['position_seconds']+((1-int(grid[0]['beat_in_bar'])) % 4)*60/grid[0]['bpm']
        offset = next_downbeat*source['original_bpm']/out['bpm']
        if not 0 <= offset <= 2:
            raise ControlBlocked('Beatpositie valt buiten de ondersteunde lokale start.', dispatched=sent)
        key_action(incoming, 'start')
        send({'command': 'launchAligned', 'incoming': incoming, 'outgoing': outgoing,
              'bpm': float(out['bpm']), 'cueOffsetSeconds': float(offset), **dispatch_guard(8)})
        if not playing(raw, incoming) or not playing(raw, outgoing):
            raise ControlBlocked('Inzet niet bevestigd; geen blinde herhaling.', dispatched=sent)
        # Launch and alignment are distinct facts. Jev sees the measured red dots
        # next; an unaligned start never authorizes a mixer gesture here.

    try:
        if name == 'LOAD_TRACK':
            deck = deck_number(params.get('deck'))
            if playing(raw, deck):
                raise ControlBlocked('Spelend deck wordt niet overschreven.', retryable=True)
            adapted = adapt_observation(raw, tracks, now_ns=now_ns(), session=session)
            chosen = [t for t in adapted['library'] if t['id'] == params.get('track_id')]
            if len(chosen) != 1:
                raise ControlBlocked('Jevs trackkeuze komt niet uniek uit map 26.')
            empty = title(raw, deck).casefold().strip().rstrip('.') == 'not loaded'
            if not empty and (not muted(raw, deck) or not playing(raw, 3-deck)):
                raise ControlBlocked('Bestaande track kan niet veilig worden vervangen.', retryable=True)
            if raw.get('browserHeading') != '26':
                folder = send({'command': 'openFolder26'})
                if folder.get('verified') is not True:
                    raise ControlBlocked('Map 26 niet bevestigd.', dispatched=sent)
            result = send({'command': 'loadChosenTrack', 'deck': deck, 'file': chosen[0]['file'],
                           'replaceStopped': not empty, 'expectedTrack': expected[str(deck)],
                           'notAfterMonotonicNS': now_ns()+55_000_000_000})
            if result.get('verified') is not True:
                raise ControlBlocked('Laden niet bevestigd; geen nieuwe laadpoging.', dispatched=sent)
            after = adapt_observation(raw, tracks, now_ns=now_ns(), session=session)
            if after['current']['A' if deck == 1 else 'B']['track'] != chosen[0]['id'] or playing(raw, deck):
                raise ControlBlocked('Geladen track of transport wijkt af.', dispatched=sent)
            updated['last_loaded'] = {'deck': 'A' if deck == 1 else 'B', 'track_id': chosen[0]['id']}
        elif name == 'PLAY':
            deck = deck_number(params.get('deck'))
            if playing(raw, deck):
                raise ControlBlocked('Deck speelt al; Jev moet de nieuwe toestand gebruiken.', retryable=True)
            if playing(raw, 3-deck):
                beat_launch(deck, 3-deck)
            else:
                track_for(deck)
                if muted(raw, deck) or any(not neutral(raw, deck, band) for band in ('low', 'mid', 'high', 'trim')):
                    raise ControlBlocked('Openingsdeck heeft geen open route met neutrale EQ.', retryable=True)
                set_playing(deck, True)
        elif name == 'PREPARE':
            deck, outgoing = deck_number(params.get('deck')), deck_number(params.get('outgoing'))
            if deck == outgoing or playing(raw, deck) or not playing(raw, outgoing) or muted(raw, outgoing):
                raise ControlBlocked('Voorbereidingstoestand is veranderd.', retryable=True)
            track_for(deck)
            # Only the stopped incoming deck is modified. The playing deck remains on.
            if mixer(raw).get('master_lit', {}).get(str(outgoing)) is not True:
                key_action(outgoing, 'master')
                if mixer(raw).get('master_lit', {}).get(str(outgoing)) is not True:
                    raise ControlBlocked('Master niet bevestigd.', dispatched=sent)
            if mixer(raw).get('beat_sync_lit', {}).get(str(deck)) is not True:
                key_action(deck, 'sync')
                if mixer(raw).get('beat_sync_lit', {}).get(str(deck)) is not True:
                    raise ControlBlocked('Sync niet bevestigd.', dispatched=sent)
            # Stopped grids can cross the playing grid. Wait locally for measured
            # alignment before any fader input, even when the incoming deck is stopped.
            if not muted(raw, deck):
                until = now_ns()+10_000_000_000
                while mixer(raw).get('red_bar_aligned') is not True and now_ns() < until:
                    refresh()
                require_aligned(raw)
                send({'command': 'crossfader', 'value': float(outgoing-1), **dispatch_guard(2)})
                if not muted(raw, deck):
                    raise ControlBlocked('Inkomende route niet bevestigd dicht.', dispatched=sent)
            reset(deck, ('mid', 'high', 'trim'))
            if bass(raw, deck) > -.45:
                before_low = bass(raw, deck)
                send({'command': 'eq', 'deck': deck, 'band': 'low', 'pixels': 35.0,
                      **dispatch_guard()})
                if bass(raw, deck) >= before_low-.04:
                    raise ControlBlocked('Bassvermindering niet bevestigd; niet herhaald.', dispatched=sent)
        elif name == 'ALIGN':
            incoming, outgoing = deck_number(params.get('incoming')), deck_number(params.get('outgoing'))
            if incoming == outgoing or not muted(raw, incoming) or not playing(raw, outgoing):
                raise ControlBlocked('Uitlijnen zou hoorbare muziek onderbreken.', retryable=True)
            if playing(raw, incoming):
                set_playing(incoming, False)
            beat_launch(incoming, outgoing)
        elif name == 'MIX':
            target = {'A': 0.0, 'center': .5, 'B': 1.0}.get(params.get('target'))
            if target is None:
                raise ControlBlocked('Onbekend mixtarget.')
            require_aligned(raw)
            if not all(playing(raw, d) for d in (1, 2)):
                raise ControlBlocked('Beide tracks moeten spelen voor deze mixbeweging.', retryable=True)
            beats = params.get('duration_beats')
            if not number(beats) or not 1 <= beats <= 24:
                raise ControlBlocked('Onbekende lengte van de gekozen beweging.')
            _, state = track_for(1)
            if not number(state.get('bpm')) or state['bpm'] <= 0:
                raise ControlBlocked('Actueel tempo onbekend.', retryable=True)
            seconds = min(12.0, max(.25, beats*60/state['bpm']))
            result = send({'command': 'mixGesture', 'crossfader': target, 'durationSeconds': seconds,
                           **dispatch_guard(seconds+4)})
            if result.get('crossfaderVerified') is not True or abs(cross(raw)-target) > .02:
                raise ControlBlocked('Mixbeweging niet bevestigd; niet herhaald.', dispatched=sent)
        elif name == 'BASS':
            require_aligned(raw)
            if not all(playing(raw, d) for d in (1, 2)):
                raise ControlBlocked('Beide tracks moeten spelen voor een basswissel.', retryable=True)
            goals = {'A': {1: 0.0, 2: -.6}, 'B': {1: -.6, 2: 0.0},
                     'balanced': {1: -.3, 2: -.3}}.get(params.get('target'))
            if goals is None:
                raise ControlBlocked('Onbekend bassdoel.')
            # Visual feedback controls bounded paired movements. The angles are not
            # dB or sound pressure; no equivalent-loudness claim is made.
            initial = {d: bass(raw, d) for d in (1, 2)}
            beats = params.get('duration_beats', 4)
            _, state = track_for(1)
            if not number(beats) or not 1 <= beats <= 24 or not number(state.get('bpm')):
                raise ControlBlocked('Tempo of bewegingsduur onbekend.', retryable=True)
            duration = min(12., max(.25, beats*60/state['bpm']))
            for step in range(20):
                require_aligned(raw)
                values = {d: bass(raw, d) for d in (1, 2)}
                lower = [d for d in (1, 2) if values[d] > goals[d]+.07]
                raise_ = [d for d in (1, 2) if values[d] < goals[d]-.07]
                if not lower and not raise_:
                    break
                if lower and raise_:
                    down, up = lower[0], raise_[0]
                    pixels = min(5., max(1., min(values[down]-goals[down], goals[up]-values[up])*50))
                    result = send({'command': 'mixGesture', 'outgoing': down, 'incoming': up,
                                   'bassPixels': pixels, 'durationSeconds': max(.25, duration/8),
                                   **dispatch_guard(duration+4)})
                    if result.get('bassDirectionVerified') is not True:
                        raise ControlBlocked('Gekoppelde bassbeweging niet bevestigd.', dispatched=sent)
                    if bass(raw, down) >= values[down]-.01 or bass(raw, up) <= values[up]+.01:
                        raise ControlBlocked('Gekoppelde knopstanden bevestigen de beweging niet.', dispatched=sent)
                else:
                    d = (lower or raise_)[0]
                    direction = 1 if lower else -1
                    pixels = min(5., max(1., abs(values[d]-goals[d])*45))
                    send({'command': 'eq', 'deck': d, 'band': 'low', 'pixels': direction*pixels,
                          **dispatch_guard()})
                    changed = bass(raw, d)-values[d]
                    if changed*direction >= -.015:
                        raise ControlBlocked('Bassrichting niet bevestigd; niet herhaald.', dispatched=sent)
            else:
                raise ControlBlocked('Bassdoel niet bereikt binnen de begrensde beweging.', dispatched=sent)
            for d in (1, 2):
                if goals[d] == 0:
                    reset(d, ('low',))
            if any(abs(bass(raw, d)-goals[d]) > .10 for d in (1, 2)):
                raise ControlBlocked('Bassdoel wijkt af van de gemeten knopstanden.', dispatched=sent)
            updated['last_bass_move'] = {'before': initial, 'after': {d: bass(raw, d) for d in (1, 2)}}
        elif name == 'STOP':
            deck = deck_number(params.get('deck'))
            if not muted(raw, deck) or not playing(raw, 3-deck):
                raise ControlBlocked('Hoorbaar deck wordt niet gestopt.', retryable=True)
            set_playing(deck, False)
        elif name == 'RESET_EQ':
            deck = deck_number(params.get('deck'))
            if not muted(raw, deck) and playing(raw, 3-deck) and not muted(raw, 3-deck):
                raise ControlBlocked('EQ-reset zou de huidige dubbele bassmix veranderen.', retryable=True)
            reset(deck, ('low', 'mid', 'high', 'trim'))
        elif name != 'HOLD':
            raise ControlBlocked('Bediening is niet geïmplementeerd: '+str(name))
        require_fresh(raw, now_ns)
        updated.update(pending_action='none', action_confirmed=True)
        updated['last_control_result'] = {'action': name, 'dispatched': sent, 'verified': True}
        return raw, updated
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        if sent:
            exc.dispatched = True
            exc.retryable = False
        raise
