"""Stdlib-only, read-only parsing of green memory-cue POSITION_MARK data.

Reads a caller-supplied, already-parsed rekordbox XML <TRACK> element (via
xml.etree.ElementTree). This module never opens a file, never talks to a
running Rekordbox library, and performs no network or filesystem I/O at
import time or call time; a full exported XML document, if used, is parsed
from bytes/text the caller already has.

A green POSITION_MARK is an operator-chosen cue (Type="0") carrying an
explicit Red/Green/Blue color whose green channel clearly dominates. This
covers both an unassigned memory cue (Num == -1) and an assigned hot cue
(Num >= 0, a lettered pad) identically: color alone decides "green", never
the Num slot or the Name. Loops (Type="4"), fade markers and uncolored cues
are never treated as green. Rekordbox's exact memory/hot-cue color palette
is not established here from primary evidence, so this module intentionally
uses an approximate channel-dominance rule instead of an unverified
hardcoded RGB triple; see is_green's docstring and demo/CUE-BAND-DATA.md for
that limitation. A cue whose Start is beyond the TRACK's own TotalTime is
rejected only when TotalTime itself parses to a valid, finite duration.
"""
import math
import xml.etree.ElementTree as ET

MAX_POSITION_MARKS = 256
MAX_START_SECONDS = 24 * 3600
# TotalTime is exported in whole seconds while POSITION_MARK Start is
# fractional, so a valid cue can sit up to one second past TotalTime.
DURATION_PRECISION_SECONDS = 1.0
CUE_TYPE = '0'
GREEN_CHANNEL_MARGIN = 20
GREEN_MIN_LEVEL = 80


def is_green(red, green, blue):
    """Approximate green-swatch classification by channel dominance.

    Requires three ints in 0..255. Returns True only when the green channel
    is at least GREEN_MIN_LEVEL and exceeds both red and blue by at least
    GREEN_CHANNEL_MARGIN. This is a deliberate approximation: no primary
    evidence establishing Rekordbox's exact color palette was available, so
    no single unverified (R, G, B) triple is hardcoded as "the" green.
    """
    if not all(isinstance(channel, int) and 0 <= channel <= 255 for channel in (red, green, blue)):
        raise ValueError('Color channels must be integers in 0..255.')
    return green >= GREEN_MIN_LEVEL and green - red >= GREEN_CHANNEL_MARGIN and green - blue >= GREEN_CHANNEL_MARGIN


def _parse_color(mark):
    raw = (mark.get('Red'), mark.get('Green'), mark.get('Blue'))
    if any(value is None for value in raw):
        return None
    try:
        red, green, blue = (int(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if not all(0 <= channel <= 255 for channel in (red, green, blue)):
        return None
    return red, green, blue


def _parse_start(value):
    if value is None:
        return None
    try:
        start = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or start < 0 or start > MAX_START_SECONDS:
        return None
    return start


def _parse_num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cue_kind(num):
    """Distinguish a hot cue (assigned pad, Num >= 0) from a memory cue (Num == -1).

    Both kinds can be colored green identically; this label is identity
    metadata for the caller, never an input to is_green() or to whether a
    mark is treated as a cue at all.
    """
    if num is None:
        return 'unknown'
    if num == -1:
        return 'memory_cue'
    if num >= 0:
        return 'hot_cue'
    return 'unknown'


def _parse_duration(track_element):
    """Read the TRACK's own TotalTime (seconds), explicit about absence/invalidity.

    Returns (duration_seconds_or_None, status) where status is 'known'
    (a finite, nonnegative, plausible value), 'missing' (no TotalTime
    attribute), or 'invalid' (present but unusable). Callers must not
    assume a duration bound was enforced unless status == 'known'.
    """
    value = track_element.get('TotalTime')
    if value is None:
        return None, 'missing'
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None, 'invalid'
    if not math.isfinite(duration) or duration < 0 or duration > MAX_START_SECONDS:
        return None, 'invalid'
    return duration, 'known'


def green_entry_cues(track_element):
    """Return ordered green memory-cue records from one rekordbox <TRACK> element.

    Contract:
      - track_element must be an xml.etree.ElementTree Element with tag
        'TRACK'; anything else raises ValueError. No XML text is parsed
        here; the caller already parsed it (see load_rekordbox_xml below).
      - Only direct POSITION_MARK children with Type == '0' (a cue, not a
        loop/fade/load marker) and an explicit, valid Red/Green/Blue color
        classified green by is_green() are included. Color, Type and Start
        are all read from the element's own attributes; hot-cue slot (Num)
        and Name are carried through as identity metadata only and never
        used to decide greenness.
      - A green cue with Num == -1 is a memory cue; Num >= 0 is a hot cue
        (an assigned pad). Both are equally valid green entry points and are
        classified identically by color; 'cue_kind' only labels which one it
        is for the caller, and is never itself a criterion for greenness.
      - The TRACK element's own TotalTime attribute (seconds) is read once
        via track_element.get('TotalTime'). When it parses to a finite,
        nonnegative, plausible value, any cue whose Start exceeds it by
        more than DURATION_PRECISION_SECONDS (TotalTime is whole seconds)
        is rejected (reason 'start_beyond_track_duration') rather than kept as
        if it were still inside the track. When TotalTime is absent or
        unusable, no such rejection happens; the returned 'duration_status'
        ('known'/'missing'/'invalid') makes that explicit so a caller never
        mistakes silence for a validated duration.
      - Every included record is a dict: {'index' (original child position,
        0-based), 'start_seconds' (float, source time, finite, 0..24h),
        'name' (str, possibly empty), 'num' (int or None), 'cue_kind'
        ('hot_cue'/'memory_cue'/'unknown'), 'type': 'cue', 'type_code': '0',
        'color': {'red','green','blue'}}. Records are ordered by
        start_seconds, ties broken by original index.
      - Return value is {'status', 'cues', 'selected', 'ignored',
        'duration_seconds', 'duration_status'}:
          'status' is 'none' (no green cues), 'single' (exactly one), or
          'ambiguous' (more than one green cue found in this track).
          'selected' is the one record when status == 'single', else None.
          Callers must not silently pick cues[0] when status == 'ambiguous';
          that decision belongs to the caller's entry-timing policy, not to
          this data-extraction module.
          'ignored' lists skipped POSITION_MARK children with a 'reason'
          ('not_a_cue_type', 'uncolored_or_invalid_color', 'not_green',
          'invalid_or_missing_start', 'start_beyond_track_duration') for
          diagnostics; it is not an error.
      - More than MAX_POSITION_MARKS children raises ValueError as a bounded
        defensive limit; this is not expected in a real exported track.
      - This function only reports where an operator placed a green cue in
        SOURCE time. It makes no claim about when audio becomes audible on
        a deck, about mixer/EQ state, or about any arrangement content at
        that position.
    """
    if track_element is None or getattr(track_element, 'tag', None) != 'TRACK':
        raise ValueError('Expected a rekordbox XML TRACK element.')
    marks = track_element.findall('POSITION_MARK')
    if len(marks) > MAX_POSITION_MARKS:
        raise ValueError('Too many POSITION_MARK children to process safely.')
    duration, duration_status = _parse_duration(track_element)
    cues, ignored = [], []
    for index, mark in enumerate(marks):
        type_code = mark.get('Type')
        if type_code != CUE_TYPE:
            ignored.append({'index': index, 'reason': 'not_a_cue_type', 'type_code': type_code})
            continue
        color = _parse_color(mark)
        if color is None:
            ignored.append({'index': index, 'reason': 'uncolored_or_invalid_color'})
            continue
        red, green, blue = color
        if not is_green(red, green, blue):
            ignored.append({'index': index, 'reason': 'not_green',
                             'color': {'red': red, 'green': green, 'blue': blue}})
            continue
        start = _parse_start(mark.get('Start'))
        if start is None:
            ignored.append({'index': index, 'reason': 'invalid_or_missing_start'})
            continue
        if duration_status == 'known' and start > duration + DURATION_PRECISION_SECONDS:
            ignored.append({'index': index, 'reason': 'start_beyond_track_duration',
                             'start_seconds': start, 'duration_seconds': duration})
            continue
        num = _parse_num(mark.get('Num'))
        cues.append({'index': index, 'start_seconds': start, 'name': mark.get('Name') or '',
                     'num': num, 'cue_kind': _cue_kind(num), 'type': 'cue', 'type_code': type_code,
                     'color': {'red': red, 'green': green, 'blue': blue}})
    cues.sort(key=lambda cue: (cue['start_seconds'], cue['index']))
    status = 'none' if not cues else ('single' if len(cues) == 1 else 'ambiguous')
    return {'status': status, 'cues': cues, 'selected': cues[0] if status == 'single' else None,
            'ignored': ignored, 'duration_seconds': duration, 'duration_status': duration_status}


def load_rekordbox_xml(data):
    """Parse a full rekordbox XML export read-only, from bytes/str already in memory.

    Rejects any DOCTYPE/ENTITY declaration before parsing to avoid entity
    expansion (XXE/billion-laughs) in stdlib ElementTree; this is a simple
    textual guard, not a general-purpose XML security review. Raises
    ValueError for a disallowed declaration or malformed XML. Never reads a
    file, never contacts a live Rekordbox library.
    """
    text = data.decode('utf-8') if isinstance(data, (bytes, bytearray)) else data
    if '<!DOCTYPE' in text or '<!ENTITY' in text:
        raise ValueError('DOCTYPE/ENTITY declarations are not supported for read-only XML parsing.')
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f'Malformed rekordbox XML: {exc}') from exc


def collection_tracks(root):
    """Return the list of <TRACK> elements under the exported <COLLECTION>.

    Deliberately excludes any TRACK-like references under PLAYLISTS, which
    in a rekordbox export are Key-only pointers, not full track records.
    Raises ValueError if root has no COLLECTION child.
    """
    collection = root.find('COLLECTION') if root is not None else None
    if collection is None:
        raise ValueError('Expected a COLLECTION element in the rekordbox XML root.')
    return collection.findall('TRACK')
