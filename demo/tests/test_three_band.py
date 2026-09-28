"""Fixture tests for ANLZ 3-band tag parsing. Synthetic bytes only, no real ANLZ files."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import three_band
from djjev.three_band import parse_three_band


def build_pwv6(values):
    payload = struct.pack('>II', 3, len(values)) + b''.join(bytes(v) for v in values)
    tag_len = 12 + len(payload)
    return b'PWV6' + struct.pack('>II', 0x14, tag_len) + payload


def build_pwv7(values, reserved=b'\x00\x00\x00\x00'):
    payload = struct.pack('>II', 3, len(values)) + reserved + b''.join(bytes(v) for v in values)
    tag_len = 12 + len(payload)
    return b'PWV7' + struct.pack('>II', 0x18, tag_len) + payload


def build_generic_tag(fourcc, header_len=12, tag_len=12, extra=b''):
    return fourcc + struct.pack('>II', header_len, tag_len) + extra


def build_file(tags, file_header_len=0x1C, file_len=None):
    body = b''.join(tags)
    if file_len is None:
        file_len = file_header_len + len(body)
    header_fields = b'\x00' * max(0, file_header_len - 12)
    return b'PMAI' + struct.pack('>II', file_header_len, file_len) + header_fields + body


class ParseThreeBandTests(unittest.TestCase):
    def test_rejects_non_bytes_input(self):
        with self.assertRaises(TypeError):
            parse_three_band('not-bytes')

    def test_rejects_implausibly_large_buffer(self):
        with patch.object(three_band, 'MAX_FILE_BYTES', 8):
            with self.assertRaises(ValueError):
                parse_three_band(b'0123456789')

    def test_missing_magic_is_unsupported(self):
        result = parse_three_band(b'not-an-anlz-file-------')
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'missing_anlz_file_magic'})

    def test_short_buffer_is_unsupported(self):
        result = parse_three_band(b'PMAI')
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'missing_anlz_file_magic')

    def test_header_length_beyond_buffer_is_unsupported(self):
        data = b'PMAI' + struct.pack('>II', 1000, 1000)
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'malformed_file_header'})

    def test_file_length_shorter_than_header_is_unsupported(self):
        data = b'PMAI' + struct.pack('>II', 20, 10)
        result = parse_three_band(data + b'\x00' * 20)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'malformed_file_header'})

    def test_no_recognized_tag_is_unsupported(self):
        data = build_file([build_generic_tag(b'PWV3')])
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'no_recognized_3band_tag'})

    def test_parses_pwv6_preview_entries_in_declared_mid_high_low_order(self):
        data = build_file([build_pwv6([(10, 20, 30), (40, 50, 60), (0, 255, 128)])])
        result = parse_three_band(data)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['format'], 'PWV6')
        self.assertEqual(result['kind'], 'preview')
        self.assertEqual(result['entries'], 3)
        self.assertEqual(result['mid'], [10, 40, 0])
        self.assertEqual(result['high'], [20, 50, 255])
        self.assertEqual(result['low'], [30, 60, 128])
        self.assertEqual(result['entry_order_assumption'], ('mid', 'high', 'low'))
        self.assertIn('not calibrated dB', result['limitations'])

    def test_parses_pwv7_detail_entries(self):
        values = [(i % 256, (i * 2) % 256, (i * 3) % 256) for i in range(10)]
        data = build_file([build_pwv7(values)])
        result = parse_three_band(data)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['format'], 'PWV7')
        self.assertEqual(result['kind'], 'detail')
        self.assertEqual(result['entries'], 10)
        self.assertEqual(result['mid'][3], 3)
        self.assertEqual(result['high'][3], 6)
        self.assertEqual(result['low'][3], 9)

    def test_two_supported_tags_are_explicitly_ambiguous(self):
        data = build_file([build_pwv6([(1, 2, 3)]), build_pwv7([(4, 5, 6)])])
        result = parse_three_band(data)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['reason'], 'multiple_3band_tags_present')
        self.assertEqual(result['formats_found'], ['PWV6', 'PWV7'])

    def test_unexpected_entry_width_is_unsupported(self):
        payload = struct.pack('>II', 2, 1) + b'\x00\x00'
        tag = b'PWV6' + struct.pack('>II', 0x14, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'unexpected_entry_width')
        self.assertEqual(result['entry_bytes'], 2)

    def test_zero_entries_is_implausible(self):
        payload = struct.pack('>II', 3, 0)
        tag = b'PWV6' + struct.pack('>II', 0x14, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'implausible_entry_count')

    def test_entry_count_over_bound_is_implausible(self):
        payload = struct.pack('>II', 3, three_band.MAX_ENTRIES + 1)
        tag = b'PWV6' + struct.pack('>II', 0x14, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'implausible_entry_count')

    def test_entries_exceeding_declared_tag_length_is_unsupported(self):
        payload = struct.pack('>II', 3, 5) + b'\x00' * 3  # claims 5 entries, only holds 1
        tag = b'PWV6' + struct.pack('>II', 0x14, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'entries_exceed_container')

    def test_entry_count_leaving_payload_bytes_unparsed_is_unsupported(self):
        payload = struct.pack('>II', 3, 1) + b'\x01\x02\x03' * 2  # claims 1 entry, holds 2
        tag = b'PWV6' + struct.pack('>II', 0x14, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'entry_count_mismatch')

    def test_incomplete_pmai_file_header_is_unsupported(self):
        data = build_file([build_pwv6([(1, 2, 3)])], file_header_len=12)
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'malformed_file_header'})

    def test_unexpected_tag_header_length_is_unsupported(self):
        payload = struct.pack('>II', 3, 1) + b'\x01\x02\x03'
        tag = b'PWV6' + struct.pack('>II', 16, 12 + len(payload)) + payload
        result = parse_three_band(build_file([tag]))
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'unexpected_tag_header_length')

    def test_malformed_trailing_tag_after_valid_tag_makes_container_unsupported(self):
        good = build_pwv6([(1, 2, 3)])
        corrupt = b'PWV7' + struct.pack('>II', 0x18, 10_000)  # declares far more than remains
        data = build_file([good, corrupt])
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'tag_exceeds_container'})

    def test_malformed_tag_before_a_valid_tag_also_makes_container_unsupported(self):
        corrupt = b'PWV7' + struct.pack('>II', 0x18, 10_000)
        good = build_pwv6([(1, 2, 3)])
        data = build_file([corrupt, good])
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'tag_exceeds_container'})

    def test_unknown_tag_with_header_longer_than_its_own_tag_length_is_unsupported(self):
        data = build_file([build_generic_tag(b'PWV3', header_len=50, tag_len=12)])
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'tag_header_length_out_of_bounds'})

    def test_declared_tag_length_below_minimum_header_is_unsupported(self):
        data = build_file([build_generic_tag(b'PWV3', header_len=4, tag_len=4)])
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'tag_length_too_small'})

    def test_truncated_trailing_tag_header_is_unsupported(self):
        good = build_pwv6([(1, 2, 3)])
        leftover = b'\x01\x02\x03\x04\x05'  # fewer than 12 bytes: not a full tag header
        body = good + leftover
        data = b'PMAI' + struct.pack('>II', 0x1C, 0x1C + len(body)) + b'\x00' * 16 + body
        result = parse_three_band(data)
        self.assertEqual(result, {'status': 'unsupported', 'reason': 'truncated_tag_header'})

    def test_never_reads_past_the_declared_file_length(self):
        good = build_pwv6([(9, 9, 9)])
        trailing_garbage = b'\xff' * 32
        data = build_file([good], file_len=0x1C + len(good))
        result = parse_three_band(data + trailing_garbage)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['entries'], 1)


if __name__ == '__main__':
    unittest.main()
