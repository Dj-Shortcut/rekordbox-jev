"""Validated offline evidence; stdlib-only preload and bounded in-memory lookup.

No actuator, inference, decoder or DSP belongs in this module. Source-file
measurements are not mixer output, perceived loudness or semantic song labels.
"""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

from .state import number, track_id
from .entry_timing import activity_sections
from .arrangement import first_drop_evidence

SCHEMA_VERSION = 1
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / '.audio-cache'
FEATURES = ('energy_dbfs', 'low_energy_dbfs_estimate', 'low_fraction', 'onsets_per_beat', 'flux')
CONTOUR_FEATURES = ('energy_dbfs', 'low_energy_dbfs_estimate', 'onsets_per_beat', 'flux')
CONTOUR_MAX_SEGMENTS = 16


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def grid_signature(track):
    return digest({k: track.get(k) for k in ('id', 'file', 'bpm', 'beatgrid')})


def grid_definition(track):
    grid = track.get('beatgrid')
    if not isinstance(grid, list) or not grid or not all(isinstance(g, dict) for g in grid):
        raise ValueError('A constant exported 4/4 beatgrid is required.')
    first = grid[0]
    bpm, start, beat = (first.get(k) for k in ('bpm', 'position_seconds', 'beat_in_bar'))
    if (not number(bpm, 60, 200) or not number(start, 0)
            or not number(beat, 1, 4) or int(beat) != beat):
        raise ValueError('Invalid first beatgrid marker.')
    downbeat = start + ((1 - int(beat)) % 4) * 60 / bpm
    previous = -1.
    for marker in grid:
        tempo, position, beat = (marker.get(k) for k in ('bpm', 'position_seconds', 'beat_in_bar'))
        if (marker.get('meter') != '4/4' or not number(tempo, 60, 200) or abs(tempo-bpm) > .05
                or not number(position, 0) or position < previous
                or not number(beat, 1, 4) or int(beat) != beat):
            raise ValueError('Variable or malformed beatgrid is not supported in this version.')
        phase = ((position-downbeat)*bpm/60 - (beat-1)) % 4
        if min(phase, 4-phase) > .05:
            raise ValueError('Beatgrid markers do not share a consistent bar phase.')
        previous = position
    return {'bpm': bpm, 'start_seconds': downbeat, 'bar_seconds': 240/bpm, 'window_bars': 4}


def file_hash(path):
    with Path(path).open('rb') as stream:
        return sha256_stream(stream)


def sha256_stream(stream):
    result = sha256()
    for block in iter(lambda: stream.read(1024*1024), b''):
        result.update(block)
    return result.hexdigest()


def cache_document(track, path, duration, extractor, windows):
    path = Path(path)
    before = path.stat()
    content_hash = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during fingerprinting.')
    document = {'schema_version': SCHEMA_VERSION, 'track_id': track['id'],
        'source': {'file': track['file'], 'bytes': after.st_size, 'mtime_ns': after.st_mtime_ns,
                   'sha256': content_hash},
        'grid_signature': grid_signature(track), 'grid': grid_definition(track),
        'duration_seconds': duration, 'extractor': extractor, 'windows': windows}
    document['analysis_id'] = digest(document)
    validate(document, track)
    return document


def validate(document, track):
    if not isinstance(document, dict) or document.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Unknown analysis schema.')
    if (document.get('track_id') != track['id'] or track['id'] != track_id(track['file'])
            or Path(track['file']).name != track['file']
            or document.get('grid_signature') != grid_signature(track)
            or document.get('grid') != grid_definition(track)):
        raise ValueError('Analysis identity or grid mismatch.')
    source = document.get('source', {})
    if (not isinstance(source, dict) or source.get('file') != track['file']
            or type(source.get('bytes')) is not int or source['bytes'] <= 0
            or type(source.get('mtime_ns')) is not int
            or not isinstance(source.get('sha256'), str) or len(source['sha256']) != 64):
        raise ValueError('Missing source fingerprint.')
    extractor = document.get('extractor', {})
    if (not isinstance(extractor, dict) or extractor.get('name') != 'essentia'
            or not isinstance(extractor.get('version'), str)
            or extractor.get('sample_rate') != 44100 or extractor.get('frame_size') != 4096
            or extractor.get('hop_size') != 512 or extractor.get('bass_band_hz') != [30, 250]):
        raise ValueError('Unsupported measurement configuration.')
    duration = document.get('duration_seconds')
    rows = document.get('windows')
    grid = document['grid']
    if not number(duration, grid['start_seconds']+.01, 7200) or not isinstance(rows, list) or not 1 <= len(rows) <= 4000:
        raise ValueError('Invalid analysis extent.')
    for i, row in enumerate(rows):
        start = grid['start_seconds'] + i*4*grid['bar_seconds']
        end = min(duration, start+4*grid['bar_seconds'])
        if (not isinstance(row, dict) or set(row) != {'start_seconds','end_seconds','start_bar','bars',*FEATURES}
                or not all(number(row.get(k)) for k in row)
                or abs(row['start_seconds']-start) > .001 or abs(row['end_seconds']-end) > .001
                or row['start_bar'] != i*4 or not 0 < row['bars'] <= 4.001
                or abs(row['bars']-(end-start)/grid['bar_seconds']) > .001
                or not -120 <= row['energy_dbfs'] <= 6 or not -120 <= row['low_energy_dbfs_estimate'] <= 6
                or row['low_energy_dbfs_estimate'] > row['energy_dbfs']+3
                or not 0 <= row['low_fraction'] <= 1 or not 0 <= row['onsets_per_beat'] <= 100
                or not 0 <= row['flux'] <= 1.001):
            raise ValueError('Invalid timeline window.')
    if abs(rows[-1]['end_seconds']-duration) > .001:
        raise ValueError('Incomplete analysis timeline.')
    body = {k: v for k, v in document.items() if k != 'analysis_id'}
    if document.get('analysis_id') != digest(body):
        raise ValueError('Analysis content checksum mismatch.')


def write_cache(document, cache_dir=DEFAULT_CACHE):
    cache_dir = Path(cache_dir)
    identity = document['track_id']
    if not isinstance(identity, str) or not identity.startswith('t_') or len(identity) != 18 or not all(c in '0123456789abcdef' for c in identity[2:]):
        raise ValueError('Invalid track cache identity.')
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / (identity+'.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(document, allow_nan=False, separators=(',', ':')))
    temporary.replace(target)
    return target


def load_cached(track, path, cache_dir=DEFAULT_CACHE, *, verify_hash=True):
    """Only called offline or once at session startup, never in Runner ticks."""
    try:
        if track['id'] != track_id(track['file']) or Path(track['file']).name != track['file']:
            return None
        target = Path(cache_dir)/(track['id']+'.json')
        if target.stat().st_size > 2_000_000:
            return None
        document = json.loads(target.read_text())
        validate(document, track)
        source = document['source']
        stamp = Path(path).stat()
        if (stamp.st_size, stamp.st_mtime_ns) != (source['bytes'], source['mtime_ns']):
            return None
        if verify_hash and file_hash(path) != source['sha256']:
            return None
        return document
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None


def aggregate(rows):
    bars = sum(row['bars'] for row in rows)
    if not bars:
        return None
    result = {'coverage_bars': round(bars, 3)}
    for feature in FEATURES:
        if 'dbfs' in feature:
            power = sum(10**(row[feature]/10)*row['bars'] for row in rows)/bars
            value = max(-120., 10*math.log10(max(1e-12, power)))
        elif feature == 'low_fraction':
            weights = [10**(row['energy_dbfs']/10)*row['bars'] for row in rows]
            value = sum(row[feature]*weight for row,weight in zip(rows,weights))/sum(weights)
        else:
            value = sum(row[feature]*row['bars'] for row in rows)/bars
        result[feature] = round(value, 4)
    return result


def source_contour(rows):
    """Precompute an ordered measurement overview, never label song sections."""
    # Validated rows cover four bars each except the last. Integer multiples of
    # four rows preserve nominal 16-bar boundaries and bound the live payload.
    rows_per_segment = 4 * max(1, math.ceil(len(rows) / (4 * CONTOUR_MAX_SEGMENTS)))
    segments = []
    for start in range(0, len(rows), rows_per_segment):
        section = rows[start:start+rows_per_segment]
        summary = aggregate(section)
        segments.append({'start_seconds': round(section[0]['start_seconds'], 3),
            'end_seconds': round(section[-1]['end_seconds'], 3),
            'start_bar': section[0]['start_bar'], 'coverage_bars': summary['coverage_bars'],
            **{feature: summary[feature] for feature in CONTOUR_FEATURES}})
    return {'segment_bars': rows_per_segment * 4, 'segments': segments,
            'basis': 'Ordered source-file measurements, not semantic song sections. The zero-based '
                     'position_segment_index locates the current source position; lower indices are '
                     'earlier source positions, not proof they were played or heard. No climax is labeled.'}


def change(previous, current, bar_seconds, position):
    energy = current['energy_dbfs']-previous['energy_dbfs']
    bass = current['low_energy_dbfs_estimate']-previous['low_energy_dbfs_estimate']
    onset = current['onsets_per_beat']-previous['onsets_per_beat']
    flux = current['flux']-previous['flux']
    labels = []
    # Descriptive measurement thresholds only; never select a DJ action.
    if abs(energy) >= 3 and max(current['energy_dbfs'], previous['energy_dbfs']) > -60:
        labels.append('energy_rising' if energy > 0 else 'energy_falling')
    if abs(bass) >= 6 and max(current['low_energy_dbfs_estimate'], previous['low_energy_dbfs_estimate']) > -60:
        labels.append('low_band_rising' if bass > 0 else 'low_band_falling')
    if abs(onset) >= .25:
        labels.append('onset_activity_rising' if onset > 0 else 'onset_activity_falling')
    if flux >= .05:
        labels.append('spectral_change_increasing')
    if not labels:
        return None
    return {'at_seconds': round(current['start_seconds'], 3),
            'in_bars': round((current['start_seconds']-position)/bar_seconds, 2),
            'energy_delta_db': round(energy, 2), 'low_energy_delta_db': round(bass, 2),
            'onsets_per_beat_delta': round(onset, 3), 'flux_delta': round(flux, 4),
            'observations': labels}


class TimelineStore:
    def __init__(self, library=(), cache_dir=DEFAULT_CACHE, music_root=None):
        root = Path(music_root or Path.home()/'Music/Music/26')
        self.timelines = {}
        for track in library:
            doc = load_cached(track, root/track['file'], cache_dir)
            if doc:
                structure = activity_sections(doc['windows'])
                self.timelines[track['id']] = (doc, [row['start_seconds'] for row in doc['windows']],
                                               source_contour(doc['windows']), structure,
                                               first_drop_evidence(doc['windows'], structure))

    def lookup(self, deck):
        identity = deck.get('track_id')
        found = self.timelines.get(identity)
        unavailable = {'status': 'missing_analysis', 'track_id': identity}
        if not found:
            return unavailable
        doc, starts, contour, entry, first_drop = found
        grid = doc['grid']
        position = deck.get('elapsed')
        if not number(position, 0) or position >= doc['duration_seconds']:
            return {**unavailable, 'status': 'position_outside_analysis'}
        # Archived 6.8.7 normal-playback clocks advance at live/original BPM,
        # including 132->127 and 124.98->127. Elapsed is SOURCE time: do not
        # multiply by the pitch ratio again. Reject inconsistent clock modes.
        remaining = deck.get('remaining')
        if not number(remaining, 0) or abs(position+remaining-doc['duration_seconds']) > 1.5:
            return {**unavailable, 'status': 'source_clock_mismatch'}
        if not number(deck.get('bpm'), 60, 200) or not .94 <= deck['bpm']/grid['bpm'] <= 1.06:
            return {**unavailable, 'status': 'unsupported_tempo_ratio'}
        index = bisect_right(starts, position)-1
        rows = doc['windows']
        # All lookaheads begin at the NEXT 4-bar window, not a second pass over
        # the current full window. Coverage is explicit near the track end.
        ahead = rows[index+1:index+9]
        changes = [item for i, row in enumerate(ahead, index+1)
                   if i > 0 and (item := change(rows[i-1], row, grid['bar_seconds'], position))]
        return {'status': 'available', 'track_id': identity, 'analysis_id': doc['analysis_id'],
                'position_seconds': position,
                'position_bar': round((position-grid['start_seconds'])/grid['bar_seconds'], 3),
                'entry_structure': deepcopy(entry),
                'source_grid': deepcopy(grid),
                'first_drop': deepcopy(first_drop),
                'clock_basis': 'rekordbox_6.8.7_elapsed_source_seconds',
                'tempo_ratio': round(deck['bpm']/grid['bpm'], 5),
                'position_region': 'before_first_downbeat' if index < 0 else 'analyzed_window',
                'current_window': {k:round(v,4) for k,v in rows[index].items()} if index >= 0 else None,
                'source_contour': {**deepcopy(contour),
                    'position_segment_index': index // (contour['segment_bars'] // 4) if index >= 0 else None},
                'lookahead': [{'horizon_bars': horizon, 'start_seconds': round(ahead[0]['start_seconds'],3),
                               'end_seconds': round(ahead[min(len(ahead),horizon//4)-1]['end_seconds'],3), **summary}
                              for horizon in (8, 16, 32)
                              if (summary := aggregate(ahead[:horizon//4]))],
                'changes': changes[:4]}

    def snapshot_context(self, snapshot):
        if not snapshot.get('valid'):
            return None
        decks = {name: self.lookup(snapshot['decks'][name]) for name in ('A','B')}
        return decks if any(d['status'] == 'available' for d in decks.values()) else None


def evidence_token(decks):
    """A model answer must not cross a source bar/identity boundary or seek."""
    if not isinstance(decks, dict):
        return None
    return {name: [deck.get('track_id'), deck.get('analysis_id'), math.floor(deck['position_bar'])]
            if deck.get('status') == 'available' and number(deck.get('position_bar'))
            else [deck.get('track_id'), deck.get('status')]
            for name, deck in decks.items()}


def transition_context(decks, transition, audible, snapshot, timing):
    if not decks:
        return None
    roles = {'incoming': None, 'outgoing': None, 'source': 'unknown'}
    if transition:
        roles = {k: transition[k] for k in ('incoming','outgoing')}
        roles['source'] = transition['source']
    elif len(audible) == 1:
        lead = audible[0]
        other = 'B' if lead == 'A' else 'A'
        cross = snapshot['mixer']['cross']
        closed = cross >= .98 if other == 'A' else cross <= .02
        if closed and not snapshot['decks'][other]['playing'] and snapshot['decks'][other]['track_id']:
            roles = {'outgoing': lead, 'incoming': other, 'source': 'one_audible_plus_stopped_closed_successor'}
    overlap = timing.get('confirmed_audible_overlap_seconds')
    bpm = snapshot['decks'].get(roles['outgoing'], {}).get('bpm')
    return {'basis': 'Offline Essentia source-file windows; not live mixer audio.',
            'roles': roles, 'decks': deepcopy(decks),
            'overlap_bars_estimate': round(overlap*bpm/240, 2) if number(overlap,0) and number(bpm,60,200) else None,
            'limitations': '4-bar means blur exact entrances. Change locations are candidate window boundaries, '
                'not detected phrases/drops. Lookaheads start at next window; coverage_bars may be shorter. '
                'Before the first downbeat current_window is unknown; lookahead begins at that first downbeat. '
                'RMS is not perceptual loudness; low-band power is an estimate, not the LOW EQ response. '
                'Onsets are detected attacks, not proof of drums or beat alignment. Mono downmix can cancel stereo content. '
                'Source data excludes EQ, fader curve and processing. No audio evidence overrides physical safety.'}
