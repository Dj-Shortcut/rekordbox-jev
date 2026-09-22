"""Adapter checks on supplied frames; no live controls, credentials or network."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from contextual_observation import adapt_observation


NOW = 10_000_000_000
LIBRARY = [{"file": "one.mp3", "title": "One", "artist": "Artist", "original_bpm": 124,
            "key": "Abm", "duration_seconds": 240, "music_scope": "26"},
           {"file": "two.mp3", "title": "Two", "original_bpm": 124, "key": "Abm", "music_scope": "26"},
           {"file": "outside.mp3", "title": "Outside", "music_scope": "25"}]


def frame():
    return {"ok": True, "result": {
        "layoutCalibrated": True, "sampledAtMonotonicNS": NOW,
        "decks": [{"deck": 1, "title": "Not Loaded", "displayedBPM": "", "metadata": ""},
                  {"deck": 2, "title": "Not Loaded.", "displayedBPM": "", "metadata": ""}],
        "playingIndicators": {"deck1": False, "deck2": False},
        "faders": {"deck1": 1, "deck2": 1},
        "mixer": {"crossfader_position": .4950495, "deck_assignments": {"1": "left", "2": "right"},
                  "eq_neutral": {"1": {"low": True}, "2": {"low": True}}, "red_bar_aligned": None}}}


class ContextualObservationTests(unittest.TestCase):
    def adapt(self, value=None, **kwargs):
        return adapt_observation(frame() if value is None else value, kwargs.pop("library", LIBRARY),
                                 now_ns=kwargs.pop("now_ns", NOW), **kwargs)

    def test_real_empty_marker_shape_and_explicit_session_provenance(self):
        adapted = self.adapt()
        self.assertTrue(adapted["current"]["readable"])
        for name in ("A", "B"):
            self.assertEqual(adapted["current"][name]["track"], "none")
            self.assertIs(adapted["current"][name]["playing"], False)
        self.assertEqual(adapted["current"]["crossfader"], "center")
        self.assertEqual(adapted["current"]["selected"], "none")
        self.assertIn("read-only inspection", adapted["provenance"]["fields"]["selected"]["source"])

    def test_normalized_empty_marker_does_not_accept_blank_or_null_title(self):
        for title in ("  NOT   LOADED. \n", "Not Loaded"):
            raw = frame(); raw["result"]["decks"][0]["title"] = title
            self.assertEqual(self.adapt(raw)["current"]["A"]["track"], "none")
        for title in ("", "   ", None, "Not", "Not Loaded Track"):
            raw = frame(); raw["result"]["decks"][0]["title"] = title
            self.assertIsNone(self.adapt(raw)["current"]["A"]["track"])

    def test_empty_requires_blank_present_metadata_bpm_and_false_indicator(self):
        for field, value in (("metadata", None), ("metadata", "124.00 Abm"),
                             ("displayedBPM", None), ("displayedBPM", "124.00")):
            raw = frame(); raw["result"]["decks"][0][field] = value
            self.assertIsNone(self.adapt(raw)["current"]["A"]["track"])
        for indicator in (None, True, 0):
            raw = frame(); raw["result"]["playingIndicators"]["deck1"] = indicator
            self.assertIsNone(self.adapt(raw)["current"]["A"]["track"])

    def test_a_library_track_named_not_loaded_must_not_be_mistaken_for_empty(self):
        library = LIBRARY + [{"file": "not-loaded.mp3", "title": "Not Loaded", "music_scope": "26"}]
        adapted = self.adapt(library=library)
        self.assertIsNone(adapted["current"]["A"]["track"])

    def test_freshness_boundaries_missing_clock_future_and_failed_request(self):
        self.assertTrue(self.adapt(now_ns=NOW + 3_000_000_000)["current"]["readable"])
        for now in (None, NOW - 1, NOW + 3_000_000_001, True):
            result = self.adapt(now_ns=now)
            self.assertFalse(result["current"]["readable"])
            self.assertIsNone(result["current"]["A"]["track"])
        for change in ({"layoutCalibrated": False}, {"sampledAtMonotonicNS": None}):
            raw = frame(); raw["result"].update(change)
            self.assertFalse(self.adapt(raw)["current"]["readable"])
        raw = frame(); raw["ok"] = False
        self.assertFalse(self.adapt(raw)["current"]["readable"])

    def test_unique_title_match_and_current_bpm_are_distinct_from_export(self):
        raw = frame(); raw["result"]["decks"][0].update(
            title=" ONE ", displayedBPM="126.00", metadata="Artist 124.00 Abm -03:15.0 00:45.0")
        adapted = self.adapt(raw)
        self.assertEqual(adapted["current"]["A"]["track"], adapted["library"][0]["id"])
        self.assertEqual(adapted["current"]["A"]["bpm"], 126)
        self.assertEqual(adapted["library"][0]["bpm"], 124)
        self.assertEqual(adapted["current"]["A"]["key"], "Abm")
        self.assertEqual(adapted["current"]["A"]["remaining_s"], 195)
        raw["result"]["decks"][0]["displayedBPM"] = None
        raw["result"]["decks"][0]["metadata"] = ""
        unknown = self.adapt(raw)["current"]["A"]
        self.assertIsNone(unknown["bpm"])
        self.assertIsNone(unknown["key"])

    def test_ambiguous_title_missing_deck_and_duplicate_deck_do_not_guess(self):
        raw = frame(); raw["result"]["decks"][0]["title"] = "One"
        library = LIBRARY + [{"file": "other.mp3", "title": "One", "music_scope": "26"}]
        self.assertIsNone(self.adapt(raw, library=library)["current"]["A"]["track"])
        raw = frame(); raw["result"]["decks"].pop(0)
        self.assertIsNone(self.adapt(raw)["current"]["A"]["track"])
        raw = frame(); raw["result"]["decks"].append(copy.deepcopy(raw["result"]["decks"][0]))
        self.assertIsNone(self.adapt(raw)["current"]["A"]["track"])

    def test_real_bpm_crop_with_pitch_reads_current_tempo_without_export_fallback(self):
        tracks = copy.deepcopy(LIBRARY)
        tracks[0]["title"] = "Attract"
        for displayed in ("123.00 0.0%", "123.00 +1.5%", "123.00 -1.5%",
                          "123,00 +1,5%", "123.00 \u22121.5%", " 123.00 0.0 % "):
            with self.subTest(displayed=displayed):
                raw = frame()
                raw["result"]["decks"][0].update(
                    title="Attract", displayedBPM=displayed,
                    metadata="Artist 124.00 Abm -03:15.0 00:45.0")
                adapted = self.adapt(raw, library=tracks)
                self.assertEqual(adapted["current"]["A"]["bpm"], 123)
                self.assertEqual(adapted["library"][0]["bpm"], 124)
                self.assertEqual(adapted["current"]["A"]["track"], adapted["library"][0]["id"])
                raw["result"]["decks"][0]["title"] = "Attract ("
                self.assertIsNone(self.adapt(raw, library=tracks)["current"]["A"]["track"])

    def test_bpm_crop_rejects_extra_tempi_and_unlabelled_or_malformed_numeric_text(self):
        for displayed in (None, "", "123.00 124.00", "123.00 0.0",
                          "123.00 0.0% 124.00", "123.00 +0.0% extra", "BPM 123.00",
                          "123.00 + 0.0%", "123.00 +-0.0%", "123.00 0.0%%"):
            with self.subTest(displayed=displayed):
                raw = frame()
                raw["result"]["decks"][0].update(
                    title="One", displayedBPM=displayed, metadata="Artist 124.00 Abm")
                self.assertIsNone(self.adapt(raw)["current"]["A"]["bpm"])
    def test_false_or_unknown_neutral_is_not_a_guessed_negative_bass_value(self):
        for low in (False, None, 1):
            raw = frame(); raw["result"]["mixer"]["eq_neutral"]["1"]["low"] = low
            self.assertIsNone(self.adapt(raw)["current"]["A"]["low_db"])
        adapted = self.adapt()
        self.assertEqual(adapted["current"]["A"]["low_db"], 0)
        self.assertIn("visually neutral", adapted["provenance"]["fields"]["A.low_db"]["limit"])

    def test_crossfader_needs_normal_assignments_and_only_supported_positions(self):
        for position, expected in ((0, "A"), (.01, "A"), (.49, "center"), (.495, "center"), (.51, "center"), (1, "B"), (.99, "B"), (.3, None), (-.1, None), (None, None)):
            raw = frame(); raw["result"]["mixer"]["crossfader_position"] = position
            self.assertEqual(self.adapt(raw)["current"]["crossfader"], expected)
        raw = frame(); raw["result"]["mixer"]["deck_assignments"]["1"] = "through"
        adapted = self.adapt(raw)
        self.assertIsNone(adapted["current"]["crossfader"])
        self.assertEqual(adapted["current"]["crossfader_position"], .4950495)

    def test_library_scope_ids_and_invalid_or_duplicate_file_identity(self):
        original = copy.deepcopy(LIBRARY)
        one = self.adapt()["library"]
        reordered = self.adapt(library=list(reversed(LIBRARY)))["library"]
        self.assertEqual({t["file"]: t["id"] for t in one}, {t["file"]: t["id"] for t in reordered})
        self.assertTrue(all(t["folder"] == "26" and t["id"].startswith("f26_") for t in one))
        self.assertEqual(len(one), 2)
        duplicate = self.adapt(library=LIBRARY + [LIBRARY[0], {"file": "../escape.mp3", "music_scope": "26"}])
        self.assertEqual([t["file"] for t in duplicate["library"]], ["two.mp3"])
        self.assertEqual(LIBRARY, original)

    def test_native_nulls_remain_unknown_and_musical_analysis_is_absent(self):
        raw = frame(); raw["result"]["playingIndicators"]["deck1"] = None
        raw["result"]["faders"]["deck1"] = None
        adapted = self.adapt(raw)
        self.assertIsNone(adapted["current"]["A"]["playing"])
        self.assertIsNone(adapted["current"]["A"]["channel"])
        self.assertIsNone(adapted["current"]["red_markers_aligned"])
        for field in ("phrase_boundary_now", "musical_context", "outgoing_section", "vocals"):
            self.assertNotIn(field, adapted["current"])

    def test_session_is_explicit_and_never_backfilled_from_screen(self):
        session = {"selected": {"id": "chosen"}, "outgoing_deck": "B", "incoming_deck": "A",
                   "pending_action": "LOAD_SELECTED", "action_confirmed": False}
        original = copy.deepcopy(session)
        adapted = self.adapt(session=session)
        for field, value in session.items():
            self.assertEqual(adapted["current"][field], value)
        self.assertEqual(session, original)
        partial = self.adapt(session={})
        self.assertIsNone(partial["current"]["selected"])
        self.assertIsNone(partial["current"]["outgoing_deck"])


if __name__ == "__main__":
    unittest.main()
