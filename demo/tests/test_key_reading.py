"""Observed sharp keys must not silently become their natural equivalents."""
import unittest
from test_policy import raw, library
from djjev.state import normalize


class KeyReadingTests(unittest.TestCase):
    def test_metadata_preserves_complete_tonality(self):
        for key in ('F#', 'C#', 'Ab', 'D#m', 'Bbm', 'Am', 'C'):
            with self.subTest(key=key):
                frame = raw()
                frame['decks'][0].update(title='One', displayedBPM='124.00',
                    metadata=f'Artist 124.00 {key} -04:00.0 01:00.0')
                result = normalize(frame, library(), 1)
                self.assertTrue(result['valid'])
                self.assertEqual(result['decks']['A']['key'], key)

    def test_unrecognized_key_suffix_is_not_truncated(self):
        frame = raw()
        frame['decks'][0].update(title='One', displayedBPM='124.00',
            metadata='Artist 124.00 F#noise -04:00.0 01:00.0')
        self.assertIsNone(normalize(frame, library(), 1)['decks']['A']['key'])
