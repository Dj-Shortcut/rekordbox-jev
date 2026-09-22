"""Small credential-free event stream for the demo UI and local evidence."""
from copy import deepcopy
import json
import math
from pathlib import Path
import time


def _scalars(value, fields):
    """Explicit projection: never serialize an arbitrary native reply."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key in fields if key in value
            and ((item := value[key]) is None or type(item) in (str, bool, int)
                 or type(item) is float and math.isfinite(item))}


def snapshot_summary(snapshot):
    result = _scalars(snapshot, ('version', 'captured_ns', 'valid', 'error'))
    if not isinstance(snapshot, dict):
        return result
    decks = snapshot.get('decks', {})
    result['decks'] = {}
    for deck in ('A', 'B'):
        item = decks.get(deck, {}) if isinstance(decks, dict) else {}
        result['decks'][deck] = _scalars(item, ('title', 'track_id', 'playing', 'bpm', 'bpm_text',
            'channel', 'bass', 'sync', 'master', 'elapsed', 'remaining', 'identity_match'))
        if isinstance(item, dict):
            result['decks'][deck]['eq_neutral'] = _scalars(item.get('eq_neutral'), ('low', 'mid', 'high', 'trim'))
    result['mixer'] = _scalars(snapshot.get('mixer'), ('cross', 'aligned'))
    return result


def native_parameters(parameters):
    result = _scalars(parameters, ('deck', 'incoming', 'outgoing', 'action', 'expectedTrack',
        'expectedOtherTrack', 'playing', 'endOnly', 'replaceStopped', 'allowSilentReplacement',
        'file', 'band', 'pixels', 'bassPixels', 'crossfader', 'value', 'durationSeconds',
        'bpm', 'cueOffsetSeconds'))
    if isinstance(parameters.get('bands'), list):
        result['bands'] = [band for band in parameters['bands'] if band in ('low', 'mid', 'high', 'trim')]
    return result


def native_result_summary(result):
    summary = _scalars(result, ('dispatched', 'commandsSent', 'verified', 'crossfaderVerified',
        'bassDirectionVerified', 'closedDeck', 'pointerActions', 'elapsedMS', 'pairMS',
        'latenessMS', 'search_seconds', 'stepsCompleted', 'stepsRequested', 'appliedBassPixels',
        'requestedCrossfader', 'measuredCrossfader',
        'requestedPlaying', 'playing', 'reason', 'identityMatch'))
    verification = result.get('verification') if isinstance(result, dict) else None
    if isinstance(verification, dict):
        summary['verification'] = _scalars(verification, ('attempts', 'elapsedMS',
            'renderWaitExhausted', 'targetPlaying', 'otherPlaying', 'aligned', 'measuredCrossfader'))
        reasons = verification.get('reasons')
        if isinstance(reasons, list):
            summary['verification']['reasons'] = [reason for reason in reasons[:16] if isinstance(reason, str)]
        for key in ('actualTitles', 'expectedTitles', 'playing'):
            if isinstance(verification.get(key), dict):
                summary['verification'][key] = _scalars(verification[key], ('1', '2'))
        if isinstance(verification.get('measuredBass'), dict):
            summary['verification']['measuredBass'] = _scalars(
                verification['measuredBass'], ('outgoing', 'incoming'))
    scheduling = result.get('scheduling') if isinstance(result, dict) else None
    if isinstance(scheduling, list):
        summary['scheduling'] = [_scalars(step, ('attempt', 'phase', 'result',
            'sampledAtMonotonicNS', 'dueMonotonicNS', 'plannedAtMonotonicNS',
            'readyAtMonotonicNS', 'checkedMonotonicNS', 'sentAtMonotonicNS',
            'latenessMS')) for step in scheduling[:24] if isinstance(step, dict)]
    return summary


class Events:
    def __init__(self, callback=None, path=None):
        self.callback = callback
        self.path = Path(path) if path else None
        self.sequence = 0

    def __call__(self, event):
        self.sequence += 1
        item = {'event_seq': self.sequence, 'time': time.time(), **deepcopy(event)}
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('a') as stream:
                    stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False)+'\n')
            except (OSError, TypeError, ValueError):
                pass
        if self.callback:
            self.callback(item)


def emit(callback, name, **fields):
    """Only caller-selected state/body fields enter events; never HTTP headers."""
    if callback:
        try:
            callback({'event': name, 'time': time.time(), **deepcopy(fields)})
        except Exception:
            # A broken display must not interrupt an in-flight physical control.
            pass
