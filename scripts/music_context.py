"""Actual Rekordbox export + current UI positions. No guessed phrases or fixed mix plan."""
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MUSIC = Path.home()/'Music/Music/26'
EXPORT = Path.home()/'Desktop/rekordbox.xml'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=2)
def read_library(export_path, modified_ns):
    root = MUSIC.resolve()
    result = []
    for item in ET.parse(export_path).getroot().findall('./COLLECTION/TRACK'):
        path = Path(unquote(urlparse(item.get('Location', '')).path))
        if path.suffix.lower() != '.mp3' or not path.is_file() or path.resolve().parent != root:
            continue
        grid = []
        for tempo in item.findall('TEMPO'):
            entry = {'position_seconds': number(tempo.get('Inizio')), 'bpm': number(tempo.get('Bpm')),
                     'meter': tempo.get('Metro'), 'beat_in_bar': number(tempo.get('Battito'))}
            if entry['position_seconds'] is not None and entry['bpm'] is not None and entry['bpm'] > 0:
                grid.append(entry)
        result.append({'file': path.name, 'title': item.get('Name'), 'artist': item.get('Artist'),
                       'original_bpm': number(item.get('AverageBpm')), 'key': item.get('Tonality') or None,
                       'duration_seconds': number(item.get('TotalTime')), 'beatgrid': grid,
                       'source': 'rekordbox.xml export', 'music_scope': '26'})
    return result


def library():
    if not EXPORT.exists():
        return []
    return read_library(str(EXPORT), EXPORT.stat().st_mtime_ns)


def time_value(text):
    match = re.fullmatch(r'(-?)(\d{2,3}):(\d{2})\.(\d)', text)
    if not match or int(match[3]) >= 60:
        return None
    value = int(match[2])*60 + int(match[3]) + int(match[4])/10
    return -value if match[1] else value


def enrich(state, tracks, mixer=None):
    cache = ROOT/'evidence/audio-analysis.json'
    analysis = json.loads(cache.read_text()) if cache.exists() else {}
    for deck in state.get('decks', []):
        title = deck.get('title', '').casefold().strip()
        matches = [t for t in tracks if title in (t['title'].casefold(), Path(t['file']).stem.casefold())]
        if len(matches) == 1:
            deck['library'] = matches[0]
        else:
            deck['library'] = None
        displays = deck.get('timeDisplays', [])
        positive = [time_value(s) for s in displays if not s.startswith('-')]
        negative = [time_value(s) for s in displays if s.startswith('-')]
        if len(positive) == len(negative) == 1 and None not in (positive[0], negative[0]):
            elapsed, remaining = positive[0], -negative[0]
            duration = matches[0].get('duration_seconds') if len(matches) == 1 else None
            if duration is None or abs(elapsed+remaining-duration) < 3:
                deck.update(elapsed_seconds=elapsed, remaining_seconds=remaining,
                            position_source='Current Rekordbox time-display OCR; 0.1 second precision')
                if len(matches) == 1:
                    filename = matches[0]['file']
                    source = MUSIC/filename
                    cached = analysis.get(filename)
                    if cached and source.exists() and cached['file_modified_ns'] == source.stat().st_mtime_ns and cached['file_size'] == source.stat().st_size:
                        nearby = [v for v in cached['levels'] if int(elapsed) <= v['second'] < int(elapsed)+8]
                        deck['source_audio'] = {'source': cached['source'], 'next_eight_seconds': nearby,
                                                'limits': cached['limits']}
    if mixer is not None:
        state['mixer'] = mixer
    state['session_state'] = 'stopped' if state.get('decks') and all(d.get('playingIndicator') is False for d in state['decks']) else 'playing_or_unknown'
    return state


def read_frame():
    tool = ROOT/'bin/read-frame'
    if not tool.is_file():
        return None
    output = subprocess.run([str(tool), str(ROOT/'evidence/native-snapshot.png')],
                            capture_output=True, text=True, timeout=2, check=True)
    return json.loads(output.stdout)
