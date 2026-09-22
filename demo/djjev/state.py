"""Observed Rekordbox state and folder-26 metadata. No DJ decisions or controls."""
from hashlib import sha256
from pathlib import Path
import math
import re
import time
import unicodedata
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

BANDS = ('trim', 'high', 'mid', 'low')
ENDPOINT_TOLERANCE = .02  # Same visual endpoint tolerance as closeStoppedDeck.


def number(value, low=None, high=None):
    return (type(value) in (int, float) and math.isfinite(value)
            and (low is None or value >= low) and (high is None or value <= high))


def text(value):
    return ' '.join(unicodedata.normalize('NFC', value or '').casefold().split())


def track_id(filename):
    return 't_'+sha256(('26/'+filename).encode()).hexdigest()[:16]


def identify_track(displayed_title, library):
    """Resolve exact text first, then only a long unique literal display prefix."""
    displayed = text(displayed_title)
    aliases = [(track, (text(track['title']), text(Path(track['file']).stem)))
               for track in library]
    exact = {track['id']: track for track, names in aliases if displayed in names}
    if exact:
        return (next(iter(exact.values())), 'exact') if len(exact) == 1 else (None, None)
    prefix = re.sub(r'(?:…|\.{2,3})$', '', displayed).rstrip()
    if len(prefix) < 32:
        return None, None
    matches = {track['id']: track for track, names in aliases
               if any(name.startswith(prefix) for name in names)}
    return ((next(iter(matches.values())), 'unique_display_prefix')
            if len(matches) == 1 else (None, None))


def displayed_bpm(value):
    """Read one tempo and optional percent-labelled pitch in either OCR order."""
    if not isinstance(value, str):
        return None
    pitch = r'[+\-−]?\d+(?:[.,]\d+)?\s*%'
    match = re.fullmatch(
        rf'\s*(?:(?P<before>{pitch})\s+)?(?P<bpm>\d{{2,3}}(?:[.,]\d+)?)'
        rf'(?:\s+(?P<after>{pitch}))?\s*', value)
    if not match or (match['before'] is not None and match['after'] is not None):
        return None
    bpm = float(match['bpm'].replace(',', '.'))
    return bpm if number(bpm, 1, 999) else None


def read_library(export_path=None, music_root=None):
    """Read only exported MP3 files physically inside Music/Music/26."""
    export = Path(export_path or Path.home()/'Desktop/rekordbox.xml')
    root = Path(music_root or Path.home()/'Music/Music/26').resolve()
    def numeric(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (ValueError, TypeError):
            return None
    tracks = []
    for item in ET.parse(export).getroot().findall('./COLLECTION/TRACK'):
        path = Path(unquote(urlparse(item.get('Location', '')).path))
        if not path.is_file() or path.suffix.lower() != '.mp3' or path.resolve().parent != root:
            continue
        tracks.append({'id': track_id(path.name), 'file': path.name, 'folder': '26',
            'title': item.get('Name') or path.stem, 'artist': item.get('Artist'),
            'bpm': numeric(item.get('AverageBpm')), 'key': item.get('Tonality') or None,
            'duration': numeric(item.get('TotalTime')), 'beatgrid': [
                {'position_seconds': numeric(t.get('Inizio')), 'bpm': numeric(t.get('Bpm')),
                 'beat_in_bar': numeric(t.get('Battito')), 'meter': t.get('Metro')}
                for t in item.findall('TEMPO')]})
    counts = {t['id']: sum(x['id'] == t['id'] for x in tracks) for t in tracks}
    return [t for t in tracks if counts[t['id']] == 1]


def cue_offset(track, bpm):
    grid = track.get('beatgrid') or []
    first = grid[0] if grid and isinstance(grid[0], dict) else {}
    start, grid_bpm, beat = (first.get(k) for k in ('position_seconds', 'bpm', 'beat_in_bar'))
    original = track.get('bpm')
    if not (first.get('meter') == '4/4' and number(start, 0) and number(grid_bpm, 1)
            and number(beat, 1, 4) and int(beat) == beat and number(original, 1) and number(bpm, 1)):
        return None
    offset = (start+((1-int(beat)) % 4)*60/grid_bpm)*original/bpm
    return offset if 0 <= offset <= 2 else None


def normalize(raw, library, version, *, now_ns=None):
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    raw = raw if isinstance(raw, dict) else {}
    body = raw.get('result', {}) if 'ok' in raw or 'result' in raw else raw
    body = body if isinstance(body, dict) else {}
    result = {'version': version, 'captured_ns': body.get('sampledAtMonotonicNS'),
              'valid': False, 'decks': {}, 'mixer': {}, 'folder': body.get('browserHeading'),
              'library': library}
    def invalid(message):
        return {**result, 'error': message}
    stamp = result['captured_ns']
    if (raw.get('ok', True) is not True or body.get('layoutCalibrated') is not True
            or type(stamp) is not int or not 0 <= now_ns-stamp <= 3_000_000_000):
        return invalid('Observation is stale, unsuccessful or not calibrated.')
    mm = body.get('mixer', {})
    if not number(mm.get('crossfader_position'), 0, 1) or mm.get('deck_assignments') != {'1': 'left', '2': 'right'}:
        return invalid('Mixer position or deck assignments are unknown.')
    result['mixer'] = {'cross': mm['crossfader_position'], 'aligned': mm.get('red_bar_aligned'),
                       'assignments': mm['deck_assignments']}
    for n, name in ((1, 'A'), (2, 'B')):
        items = [d for d in body.get('decks', []) if d.get('deck') == n]
        if len(items) != 1:
            return invalid('Deck observation missing or ambiguous.')
        item = items[0]
        playing = body.get('playingIndicators', {}).get('deck'+str(n))
        channel = body.get('faders', {}).get('deck'+str(n))
        title, metadata, displayed = item.get('title'), item.get('metadata'), item.get('displayedBPM')
        if not isinstance(title, str) or type(playing) is not bool or not number(channel, 0, 1):
            return invalid('Track, transport or channel position is unknown.')
        empty = (text(title).rstrip('. ') == 'not loaded' and metadata == '' and displayed == '' and playing is False)
        track, identity_match = identify_track(title, library)
        if (empty and track) or (not empty and track is None):
            return invalid('Track identity is not uniquely known in folder 26.')
        bpm = displayed_bpm(displayed)
        key = re.search(r'\b\d{2,3}[.,]\d+\s+([A-G](?:b|#)?m?)(?=\s|$)', metadata or '')
        times = re.findall(r'(-?)(\d{2,3}):(\d{2})\.(\d)', metadata or '')
        elapsed = [int(m)*60+int(s)+int(d)/10 for sign,m,s,d in times if not sign and int(s)<60]
        remaining = [int(m)*60+int(s)+int(d)/10 for sign,m,s,d in times if sign and int(s)<60]
        neutrals = mm.get('eq_neutral', {}).get(str(n), {})
        bass = 0 if neutrals.get('low') is True else mm.get('eq_position', {}).get(str(n), {}).get('low')
        result['decks'][name] = {'title': title, 'track_id': track['id'] if track else None,
            'identity_match': identity_match,
            'playing': playing, 'bpm': bpm, 'bpm_text': displayed, 'key': key[1] if key else None,
            'elapsed': elapsed[0] if len(elapsed)==len(remaining)==1 else None,
            'remaining': remaining[0] if len(elapsed)==len(remaining)==1 else None,
            'channel': channel, 'bass': bass if number(bass, -1, 1) else None,
            'eq_neutral': {band: neutrals.get(band) for band in BANDS},
            'sync': mm.get('beat_sync_lit', {}).get(str(n)), 'master': mm.get('master_lit', {}).get(str(n)),
            'cue_offset': cue_offset(track, bpm) if track else None}
    result['valid'] = True
    return result
