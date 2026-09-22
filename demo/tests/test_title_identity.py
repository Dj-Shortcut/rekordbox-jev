"""Literal long display prefixes resolve identities without inventing OCR text."""
import copy
import unittest
from test_policy import raw, library
from djjev.state import normalize, track_id


PREFIX = 'Could Heaven Ever Be Like This (Walker & Royce and'
FULL = PREFIX + ' Chris Lorenzo Remix) (Mixed)'


def track(title=FULL, filename=None):
    item = copy.deepcopy(library()[0])
    item.update(title=title, file=filename or f'Idris Muhammad - {title}.mp3',
                artist='Idris Muhammad', bpm=127, duration=317)
    item['id'] = track_id(item['file'])
    return item


def observed(displayed, tracks):
    frame = raw()
    frame['decks'][0].update(title=displayed, displayedBPM='127.00',
        metadata='Idris Muhammad 127.00 Am -05:17.0 00:00.0')
    return normalize(frame, tracks, 1)


class TitleIdentityTests(unittest.TestCase):
    def test_real_long_display_prefix_keeps_raw_title_and_resolves_track_id(self):
        selected = track()
        others = [track('Could Heaven Ever Be Like This', artist+'.mp3')
                  for artist in ('Idris Muhammad', 'Incognito', 'Lance Ferguson')]
        for displayed in (PREFIX, PREFIX+'…', PREFIX+'...', PREFIX+'...  '):
            with self.subTest(displayed=displayed):
                result = observed(displayed, [selected]+others)
                self.assertTrue(result['valid'])
                self.assertEqual(result['decks']['A']['title'], displayed)
                self.assertEqual(result['decks']['A']['track_id'], selected['id'])
                self.assertEqual(result['decks']['A']['identity_match'], 'unique_display_prefix')

    def test_two_remixes_sharing_display_prefix_are_ambiguous(self):
        self.assertFalse(observed(PREFIX, [track(), track(PREFIX+' Another Remix)')])['valid'])

    def test_actual_loaded_title_with_two_dot_truncation_keeps_raw_observation(self):
        displayed = 'Could Heaven Ever Be Like This (Walker & Royce and Chris L..'
        selected = track()
        result = observed(displayed, [selected])
        self.assertTrue(result['valid'])
        self.assertEqual(result['decks']['A']['title'], displayed)
        self.assertEqual(result['decks']['A']['track_id'], selected['id'])
        self.assertEqual(result['decks']['A']['identity_match'], 'unique_display_prefix')

    def test_file_stem_alias_can_make_prefix_ambiguous(self):
        other = track('Different metadata title', PREFIX+' Different.mp3')
        self.assertFalse(observed(PREFIX, [track(), other])['valid'])

    def test_short_unknown_and_typo_prefixes_are_rejected(self):
        for displayed in (FULL[:31], FULL[:31]+'…', PREFIX.replace('Royce','Royee'),
                          'Unknown title with more than thirty two characters', PREFIX+'.'):
            with self.subTest(displayed=displayed):
                self.assertFalse(observed(displayed, [track()])['valid'])
        self.assertTrue(observed(FULL[:32], [track()])['valid'])

    def test_exact_short_titles_and_full_file_stems_keep_working(self):
        selected = track('One', 'Artist - One.mp3')
        for displayed in ('One', 'Artist - One', '  ONE  '):
            with self.subTest(displayed=displayed):
                deck = observed(displayed, [selected])['decks']['A']
                self.assertEqual(deck['track_id'], selected['id'])
                self.assertEqual(deck['identity_match'], 'exact')
                self.assertEqual(deck['title'], displayed)

    def test_exact_match_precedes_prefix_and_duplicate_exact_remains_ambiguous(self):
        exact = track(PREFIX)
        result = observed(PREFIX, [exact, track()])
        self.assertTrue(result['valid'])
        self.assertEqual(result['decks']['A']['track_id'], exact['id'])
        self.assertEqual(result['decks']['A']['identity_match'], 'exact')
        duplicate = track(PREFIX, 'Other file.mp3')
        self.assertFalse(observed(PREFIX, [exact, duplicate])['valid'])


if __name__ == '__main__':
    unittest.main()
