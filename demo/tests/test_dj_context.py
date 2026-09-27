"""Real request integration; no network, keys, audio playback or native input."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_policy import library, loaded, raw, response, snapshot
from djjev import policy
from djjev.state import read_library


class DJContextTests(unittest.TestCase):
    def test_exported_genre_reaches_candidates_and_loaded_decks_without_guessing(self):
        tracks = library()
        tracks[0]['genre'] = 'Rap/Hip Hop'
        tracks[1]['genre'] = 'Jazz'
        request = policy.prepare(snapshot(loaded(raw(), playing=True), tracks))
        context = request['state']['dj_context']
        self.assertEqual(context['decks']['A']['genre'], 'Rap/Hip Hop')
        self.assertEqual(context['decks']['A']['genre_source'], 'rekordbox_export')
        self.assertIsNone(context['decks']['B']['genre'])
        self.assertEqual(request['state']['candidates'][tracks[1]['id']]['genre'], 'Jazz')
        self.assertIsNone(request['state']['candidates'][tracks[2]['id']]['genre'])
        self.assertNotIn('continuous EDM set', request['state']['goal'])
        self.assertIsNone(context['decks']['A']['vocal_activity'])
        self.assertIsNone(context['decks']['A']['semantic_section'])

    def test_genre_does_not_change_physical_eligibility_or_invent_supported_actions(self):
        state = snapshot(loaded(raw(), playing=True))
        original = policy.prepare(state)
        tagged = deepcopy(state)
        for track in tagged['library']:
            track['genre'] = 'Jazz / Hip Hop / unknown style'
        request = policy.prepare(tagged)
        self.assertEqual(set(request['questions']), set(original['questions']))
        for name in request['questions']:
            self.assertEqual(request['questions'][name]['criteria'], original['questions'][name]['criteria'])
        for forbidden in ('cut', 'loop', 'effect', 'seek'):
            self.assertFalse(any(forbidden in q['criteria'] for q in request['questions'].values()))

    def test_scope_excludes_outside_tracks_even_when_genre_matches(self):
        tracks = library()
        tracks[2]['folder'] = 'other'
        tracks[2]['genre'] = 'House'
        request = policy.prepare(snapshot(tracks=tracks))
        self.assertNotIn(tracks[2]['id'], request['state']['candidates'])
        self.assertNotIn(tracks[2]['id'], request['questions']['next_track']['criteria'])
        self.assertEqual(request['state']['dj_context']['scope'], 'folder_26_only')

    def test_live_situation_changes_checklist_and_keeps_branch_semantics(self):
        empty = policy.prepare(snapshot())
        self.assertEqual(empty['state']['dj_context']['situation'], 'opening_or_recovery')
        frame = loaded(loaded(raw(), playing=True), 'B', 1, True)
        frame['mixer']['red_bar_aligned'] = True
        state = snapshot(frame)
        request = policy.prepare(state)
        self.assertEqual(request['state']['dj_context']['situation'], 'overlap')
        self.assertIn(16, [q['order'] for q in request['state']['dj_context']['checklist']])
        decision = policy.resolve(request, response(request, transport='hold', crossfader='B', bass='B'))
        self.assertEqual((decision['crossfader'], decision['bass'], decision['duration_beats']), ('hold','hold',0))
        self.assertTrue(policy.applicable(decision, state))
        state['mixer']['aligned'] = False
        unaligned = policy.prepare(state)
        self.assertEqual(unaligned['state']['dj_context']['situation'], 'alignment_needed')
        self.assertNotIn('mix', unaligned['questions']['transport']['criteria'])
        self.assertNotIn('crossfader', unaligned['state']['dj_context']['available_answers'])

    def test_busy_never_creates_an_extra_request_and_context_does_not_mutate_snapshot(self):
        state = snapshot()
        before = deepcopy(state)
        request = policy.prepare(state, busy=True)
        self.assertEqual(request['questions'], {})
        self.assertEqual(request['state']['dj_context']['checklist'], [])
        self.assertEqual(state, before)

    def test_library_genre_is_literal_and_symlinks_outside_26_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'26'
            root.mkdir()
            (root/'one.mp3').write_bytes(b'fixture')
            (root/'two.mp3').write_bytes(b'fixture')
            other = Path(tmp)/'outside.mp3'
            other.write_bytes(b'fixture')
            (root/'link.mp3').symlink_to(other)
            export = Path(tmp)/'rekordbox.xml'
            export.write_text(f'''<DJ_PLAYLISTS><COLLECTION>
              <TRACK Name="House in the title" Genre="  Jazz  " Location="{(root/'one.mp3').as_uri()}"/>
              <TRACK Name="Techno in the title" Genre="  " Location="{(root/'two.mp3').as_uri()}"/>
              <TRACK Name="Outside" Genre="Jazz" Location="{(root/'link.mp3').as_uri()}"/>
            </COLLECTION></DJ_PLAYLISTS>''')
            tracks = read_library(export, root)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]['genre'], 'Jazz')
            self.assertIsNone(tracks[1]['genre'])


if __name__ == '__main__':
    unittest.main()
