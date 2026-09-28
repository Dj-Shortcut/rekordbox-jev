"""Stdlib-only parsing of Rekordbox ANLZ 3-band waveform tags from supplied bytes.

Interprets the PWV6 (color waveform preview) and PWV7 (color waveform detail)
tags described at
https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/anlz.html:
PWV6 has a 0x14-byte tag header and ~1200 preview columns; PWV7 has a
0x18-byte tag header and ~150 detail samples per source second. Both encode
each column/sample as 3 raw bytes, taken here in (mid, high, low) order per
that reference. This module performs no file, network or live-library I/O;
it only reads bytes the caller already has, and only within the container's
own declared header/tag/entry-count boundaries. No other waveform/color tag
(e.g. PWAV, PWV3-5) is interpreted.

The returned mid/high/low values are raw 0-255 magnitude codes on Rekordbox's
own undocumented scale. They are NOT dBFS, NOT normalized amplitude, NOT
post-EQ/post-fader output, and NOT evidence of vocals, a drop, or any other
arrangement section. The common display convention (blue=low, orange=mid,
white=high) describes Rekordbox's own waveform rendering, not a calibration
verified by this module. Byte-order and exact palette/format details rest on
the single cited reference and are not independently re-derived here from a
captured file; treat them as a documented assumption, not a certainty.
"""
import struct

FILE_MAGIC = b'PMAI'
# A PMAI file header is 0x1c bytes: magic, header length, file length and
# four further header fields; anything shorter is not a complete ANLZ header.
MIN_FILE_HEADER = 0x1C
TAG_HEADER_PREFIX = 12
ENTRY_BYTES = 3
MAX_ENTRIES = 500_000
MAX_FILE_BYTES = 64 * 1024 * 1024
SUPPORTED_TAGS = {
    b'PWV6': {'declared_header': 0x14, 'kind': 'preview', 'expected_entries_reference': 1200},
    b'PWV7': {'declared_header': 0x18, 'kind': 'detail', 'expected_entries_reference': None},
}


class _MalformedContainer(Exception):
    """Internal signal: the container has invalid/truncated tag data.

    Raised (never silently swallowed) so a malformed or truncated tag,
    whether recognized or not and whether before or after a valid 3-band
    tag, turns the whole parse_three_band result into an explicit
    'unsupported' status instead of silently returning an earlier tag as
    if the rest of the container were fine.
    """
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read_u32(data, offset):
    return struct.unpack_from('>I', data, offset)[0]


def _iter_tags(data, header_len, file_len):
    offset = header_len
    while offset < file_len:
        if offset + TAG_HEADER_PREFIX > file_len:
            raise _MalformedContainer('truncated_tag_header')
        fourcc = bytes(data[offset:offset + 4])
        tag_header_len = _read_u32(data, offset + 4)
        tag_len = _read_u32(data, offset + 8)
        if tag_len < TAG_HEADER_PREFIX:
            raise _MalformedContainer('tag_length_too_small')
        if offset + tag_len > file_len:
            raise _MalformedContainer('tag_exceeds_container')
        # Bounds-checked for every tag, including ones this module does not
        # decode, so an unrecognized tag's garbage header cannot hide a
        # truncated/corrupt container behind a later valid 3-band tag.
        if tag_header_len < TAG_HEADER_PREFIX or tag_header_len > tag_len:
            raise _MalformedContainer('tag_header_length_out_of_bounds')
        yield offset, fourcc, tag_header_len, tag_len
        offset += tag_len


def _decode_tag(data, offset, fourcc, tag_header_len, tag_len):
    spec = SUPPORTED_TAGS[fourcc]
    name = fourcc.decode('ascii')
    if tag_header_len != spec['declared_header'] or tag_header_len > tag_len:
        return {'status': 'unsupported', 'format': name, 'reason': 'unexpected_tag_header_length'}
    entry_bytes = _read_u32(data, offset + 12)
    entry_count = _read_u32(data, offset + 16)
    if entry_bytes != ENTRY_BYTES:
        return {'status': 'unsupported', 'format': name, 'reason': 'unexpected_entry_width',
                'entry_bytes': entry_bytes}
    if not 0 < entry_count <= MAX_ENTRIES:
        return {'status': 'unsupported', 'format': name, 'reason': 'implausible_entry_count',
                'entry_count': entry_count}
    data_start = offset + tag_header_len
    data_end = data_start + entry_count * ENTRY_BYTES
    if data_end > offset + tag_len or data_end > len(data):
        return {'status': 'unsupported', 'format': name, 'reason': 'entries_exceed_container'}
    # PWV6/PWV7 payloads are exactly entry_count * 3 bytes, so a count that
    # leaves declared payload bytes unread is corrupt, not a shorter series.
    if data_end != offset + tag_len:
        return {'status': 'unsupported', 'format': name, 'reason': 'entry_count_mismatch'}
    mid, high, low = [], [], []
    for i in range(entry_count):
        base = data_start + i * ENTRY_BYTES
        mid.append(data[base])
        high.append(data[base + 1])
        low.append(data[base + 2])
    return {'status': 'parsed', 'format': name, 'kind': spec['kind'], 'entries': entry_count,
            'mid': mid, 'high': high, 'low': low, 'entry_order_assumption': ('mid', 'high', 'low'),
            'declared_tag_header_bytes': tag_header_len,
            'expected_entries_reference': spec['expected_entries_reference']}


def parse_three_band(data):
    """Parse the 3-band waveform tag from one supplied ANLZ (.DAT/.EXT) buffer.

    Contract:
      - data must be bytes or bytearray, the exact contents of one ANLZ
        container; a TypeError is raised otherwise. No I/O happens here.
      - Returns {'status': 'unsupported', 'reason': ...} when the file
        magic, declared header/tag lengths, entry byte width, or entry
        count are missing or inconsistent with the buffer, rather than
        guessing a layout. Reasons include 'missing_anlz_file_magic',
        'malformed_file_header', 'no_recognized_3band_tag',
        'unexpected_tag_header_length', 'unexpected_entry_width',
        'implausible_entry_count', 'entries_exceed_container',
        'entry_count_mismatch',
        'truncated_tag_header', 'tag_length_too_small',
        'tag_exceeds_container' and 'tag_header_length_out_of_bounds'. The
        last four are container-wide: a single malformed or truncated tag
        anywhere in the buffer (recognized or not, before or after a valid
        3-band tag) makes the whole result 'unsupported' rather than
        silently keeping an earlier valid tag.
      - Returns {'status': 'ambiguous', 'reason': 'multiple_3band_tags_present',
        'formats_found': [...]} when more than one PWV6/PWV7 tag is present
        in the same buffer; this module does not choose one silently.
      - On {'status': 'parsed', ...}: 'format' is 'PWV6' or 'PWV7', 'kind'
        is 'preview' or 'detail', 'entries' is the validated column/sample
        count, and 'mid'/'high'/'low' are equal-length lists of raw 0-255
        integers, one per column/sample, in source order. 'limitations'
        restates that these are not calibrated levels and not arrangement
        evidence. All container/entry bounds are validated against the
        buffer before any value is read.
      - Every read stays within the file's own declared header/tag/entry
        boundaries; nothing is read past len(data) or past a tag's own
        declared length.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError('parse_three_band requires raw bytes.')
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Refusing to parse an implausibly large ANLZ buffer.')
    if len(data) < TAG_HEADER_PREFIX or bytes(data[:4]) != FILE_MAGIC:
        return {'status': 'unsupported', 'reason': 'missing_anlz_file_magic'}
    header_len = _read_u32(data, 4)
    file_len = _read_u32(data, 8)
    if header_len < MIN_FILE_HEADER or header_len > len(data) or file_len > len(data) or file_len < header_len:
        return {'status': 'unsupported', 'reason': 'malformed_file_header'}
    try:
        found = [_decode_tag(data, offset, fourcc, tag_header_len, tag_len)
                 for offset, fourcc, tag_header_len, tag_len in _iter_tags(data, header_len, file_len)
                 if fourcc in SUPPORTED_TAGS]
    except _MalformedContainer as exc:
        return {'status': 'unsupported', 'reason': exc.reason}
    if not found:
        return {'status': 'unsupported', 'reason': 'no_recognized_3band_tag'}
    if len(found) > 1:
        return {'status': 'ambiguous', 'reason': 'multiple_3band_tags_present',
                'formats_found': [tag['format'] for tag in found]}
    result = found[0]
    if result['status'] == 'parsed':
        result['limitations'] = (
            'Raw per-column/sample byte magnitudes from one ANLZ tag; not calibrated dB, not '
            'post-EQ/post-fader output, and not evidence of vocals, a drop or any other '
            'arrangement section. blue=low/orange=mid/white=high is the rekordbox display '
            'convention for these bands, not a calibration verified by this parser.')
    return result
