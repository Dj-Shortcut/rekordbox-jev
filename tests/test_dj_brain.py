"""Pure full-set action-menu checks: no API, credentials, screenshots or controls."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import dj_brain as brain
from contextual_live import evaluate
from jev_decisions import digest

NOW = 20_000_000_000
TRACKS = [
    {"file": "one.mp3", "title": "One", "artist": "Artist 1", "original_bpm": 124,
     "key": "Am", "duration_seconds": 300, "music_scope": "26"},
    {"file": "two.mp3", "title": "Two", "artist": "Artist 2", "original_bpm": 124,
     "key": "Em", "duration_seconds": 300, "music_scope": "26"},
    {"file": "three.mp3", "title": "Three", "artist": "Artist 3", "original_bpm": 126,
     "key": "C", "duration_seconds": 280, "music_scope": "26"},
    {"file": "four.mp3", "title": "Four", "artist": "Artist 4", "original_bpm": 124,
     "key": "Bbm", "duration_seconds": 300, "music_scope": "26"},
    {"file": "too-fast.mp3", "title": "Too Fast", "original_bpm": 150,
     "key": "Am", "music_scope": "26"},
    {"file": "outside.mp3", "title": "Outside", "original_bpm": 124,
     "key": "Am", "music_scope": "25"},
]
for track in TRACKS:
    track["beatgrid"] = [{"position_seconds": .1, "bpm": track["original_bpm"], "beat_in_bar": 1, "meter": "4/4"}]


def frame():
    return {"layoutCalibrated": True, "sampledAtMonotonicNS": NOW,
        "decks": [{"deck": n, "title": "Not Loaded", "displayedBPM": "", "metadata": ""} for n in (1, 2)],
        "playingIndicators": {"deck1": False, "deck2": False},
        "faders": {"deck1": 1, "deck2": 1},
        "mixer": {"crossfader_position": .5, "deck_assignments": {"1": "left", "2": "right"},
                  "eq_neutral": {str(n): {b: True for b in brain.BANDS} for n in (1, 2)},
                  "eq_position": {str(n): {b: 0 for b in brain.BANDS} for n in (1, 2)},
                  "red_bar_aligned": False, "master_lit": {"1": True, "2": False},
                  "beat_sync_lit": {"1": False, "2": True}}}


def load(raw, name, track=0, *, playing=False, bpm=124):
    n = 1 if name == "A" else 2
    item = TRACKS[track]
    raw["decks"][n-1].update(title=item["title"], displayedBPM=f"{bpm:.2f} 0.0%",
        metadata=f"{item.get('artist', '')} {bpm:.2f} {item['key']} -04:00.0 01:00.0")
    raw["playingIndicators"]["deck"+str(n)] = playing
    return raw


def bass(raw, name, angle, neutral=False):
    n = "1" if name == "A" else "2"
    raw["mixer"]["eq_position"][n]["low"] = angle
    raw["mixer"]["eq_neutral"][n]["low"] = neutral
    return raw


def answer(prepared, action="hold", track=None, gesture="beats8"):
    selected = {"dj_action": action, "next_track": track, "gesture": gesture}
    result = {"model": "jev-test", "answers": {}}
    for name, question in prepared["payload"]["questions"].items():
        choice = selected[name] or next(iter(question["criteria"]))
        result["answers"][name] = {"type": "choice", "choice": choice, "confidence": 1,
            "probabilities": {k: int(k == choice) for k in question["criteria"]}}
    return result


class DJBrainTests(unittest.TestCase):
    def prepare(self, raw=None, **kwargs):
        return brain.prepare(frame() if raw is None else raw, kwargs.pop("tracks", TRACKS),
                             kwargs.pop("session", {}), now_ns=kwargs.pop("now_ns", NOW), **kwargs)

    def test_empty_decks_offer_actual_load_choice_and_speculative_track_in_one_request(self):
        prepared = self.prepare()
        self.assertEqual(set(prepared["option_actions"]), {"load_A", "load_B", "hold"})
        self.assertEqual(set(prepared["payload"]["questions"]), {"dj_action", "next_track"})
        self.assertEqual(len(prepared["candidate_map"]), 5)
        selected = list(prepared["candidate_map"])[2]
        action = brain.resolve(prepared, answer(prepared, "load_B", selected))
        self.assertEqual(action["action"], "LOAD_TRACK")
        self.assertEqual(action["parameters"], {"deck": "B", "track_id": prepared["candidate_map"][selected]})
        self.assertTrue(brain.applicable(action, prepared))

    def test_all_127_candidates_retained_without_ranked_shortlist(self):
        tracks = [{**TRACKS[0], "file": f"{i}.mp3", "title": f"Track {i}"} for i in range(127)]
        prepared = self.prepare(tracks=tracks)
        self.assertEqual(len(prepared["candidate_map"]), 127)
        self.assertEqual(len(prepared["payload"]["questions"]["next_track"]["criteria"]), 127)
        self.assertEqual(len(prepared["library"]), 127)

    def test_reading_only_metadata_does_not_invent_audio_or_phrase_evidence(self):
        prepared = self.prepare(load(frame(), "A", playing=True))
        state = prepared["payload"]["state"]
        self.assertEqual(state["decks"]["A"]["elapsed_s"], 60)
        self.assertEqual(state["decks"]["A"]["remaining_s"], 240)
        self.assertEqual(state["decks"]["A"]["current_bpm"], 124)
        self.assertEqual(state["decks"]["A"]["original"]["bpm"], 124)
        self.assertNotIn("phrase_boundary", state)
        self.assertIn("No audio", state["state_contract"]["unknown"])

    def test_running_track_never_becomes_the_proposed_next_track(self):
        raw = load(frame(), "A", playing=True)
        prepared = self.prepare(raw)
        self.assertEqual(set(prepared["option_actions"]), {"load_B", "hold"})
        titles = {t["title"] for t in prepared["payload"]["state"]["candidates"].values()}
        self.assertEqual(titles, {"Two", "Three"})  # fifth and relative major, within tempo range
        self.assertNotIn("One", titles)
        self.assertNotIn("gesture", prepared["payload"]["questions"])

    def test_recent_titles_files_and_ids_exclude_repeats(self):
        raw = load(frame(), "A", playing=True)
        first = self.prepare(raw)
        two_id = next(t["id"] for t in first["library"].values() if t["title"] == "Two")
        for remembered in ("Two", "two.mp3", two_id, {"track_id": two_id}):
            with self.subTest(remembered=remembered):
                prepared = self.prepare(raw, session={"recent_tracks": [remembered]})
                self.assertEqual([t["title"] for t in prepared["payload"]["state"]["candidates"].values()], ["Three"])
                self.assertEqual(prepared["payload"]["state"]["recent_tracks"][0]["title"], "Two")

    def test_opening_play_is_jev_choice_even_when_track_already_loaded(self):
        prepared = self.prepare(load(frame(), "A"))
        self.assertIn("play_A", prepared["option_actions"])
        self.assertIn("hold", prepared["option_actions"])
        self.assertIn("load_B", prepared["option_actions"])
        self.assertNotIn("prepare_A", prepared["option_actions"])

    def test_all_stopped_ended_tracks_offer_fresh_load_on_open_deck_without_false_play(self):
        raw = load(load(frame(), "A"), "B", 1)
        raw["mixer"]["crossfader_position"] = 1
        for deck in raw["decks"]:
            key = "Am" if deck["deck"] == 1 else "Em"
            deck["metadata"] = f"Artist 124.00 {key} -00:00.0 05:00.0"
        prepared = self.prepare(raw)
        self.assertEqual(set(prepared["option_actions"]), {"load_A", "load_B", "hold"})
        self.assertIn("route is open", prepared["payload"]["questions"]["dj_action"]["criteria"]["load_B"])
        action = brain.resolve(prepared, answer(prepared, "load_B"))
        self.assertEqual(action["parameters"]["deck"], "B")
        self.assertTrue(brain.applicable(action, prepared))

    def test_known_end_never_offers_play_but_ready_open_deck_remains_playable(self):
        raw = load(frame(), "A")
        raw["mixer"]["crossfader_position"] = 0
        ready = self.prepare(raw)
        action = brain.resolve(ready, answer(ready, "play_A"))
        for remaining in ("00:00.0", "00:00.5"):
            raw["decks"][0]["metadata"] = f"Artist 124.00 Am -{remaining} 05:00.0"
            ended = self.prepare(raw)
            self.assertNotIn("play_A", ended["option_actions"])
            self.assertIn("load_A", ended["option_actions"])
            self.assertFalse(brain.applicable(action, ended))
        raw["decks"][0]["metadata"] = "Artist 124.00 Am -04:59.0 00:01.0"
        self.assertIn("play_A", self.prepare(raw)["option_actions"])

    def test_occupied_open_deck_is_not_replaceable_when_the_other_deck_plays(self):
        raw = load(load(frame(), "A", playing=True), "B", 1)
        prepared = self.prepare(raw)
        self.assertNotIn("load_B", prepared["option_actions"])
        self.assertIn("prepare_B", prepared["option_actions"])

    def test_prepare_stopped_incoming_can_wait_for_alignment_but_play_cannot_skip_staging(self):
        raw = load(load(frame(), "A", playing=True), "B", 1)
        prepared = self.prepare(raw)
        self.assertIn("prepare_B", prepared["option_actions"])
        self.assertNotIn("play_B", prepared["option_actions"])
        self.assertNotIn("mix_center", prepared["option_actions"])
        raw["mixer"]["crossfader_position"] = 0
        bass(raw, "B", -.6)
        prepared = self.prepare(raw)
        self.assertIn("play_B", prepared["option_actions"])
        self.assertNotIn("prepare_B", prepared["option_actions"])
        raw["mixer"]["beat_sync_lit"]["2"] = False
        self.assertNotIn("play_B", self.prepare(raw)["option_actions"])

    def test_silent_launch_uses_derived_exported_downbeat_and_rejects_missing_grid(self):
        raw = load(load(frame(), "A", playing=True), "B", 1)
        raw["mixer"]["crossfader_position"] = 0
        bass(raw, "B", -.6)
        tracks = copy.deepcopy(TRACKS)
        tracks[1]["beatgrid"][0].update(position_seconds=.05, beat_in_bar=3)
        prepared = self.prepare(raw, tracks=tracks)
        self.assertIn("play_B", prepared["option_actions"])
        cue = prepared["payload"]["state"]["decks"]["B"]["aligned_start_cue"]
        self.assertAlmostEqual(cue["offset_seconds"], .05+2*60/124)
        self.assertIn("not a phrase", cue["source"])
        tracks[1]["beatgrid"] = []
        prepared = self.prepare(raw, tracks=tracks)
        self.assertNotIn("play_B", prepared["option_actions"])
        self.assertIn("load_B", prepared["option_actions"])

    def test_successor_candidates_must_have_supported_grid_but_opening_need_not(self):
        tracks = copy.deepcopy(TRACKS)
        tracks[1]["beatgrid"][0]["position_seconds"] = 40
        opening = self.prepare(tracks=tracks)
        self.assertIn("Two", {t["title"] for t in opening["payload"]["state"]["candidates"].values()})
        playing = self.prepare(load(frame(), "A", playing=True), tracks=tracks)
        self.assertEqual({t["title"] for t in playing["payload"]["state"]["candidates"].values()}, {"Three"})

    def test_both_playing_aligned_offers_independent_musical_controls_not_phase_ladder(self):
        raw = load(load(frame(), "A", playing=True), "B", 1, playing=True)
        raw["mixer"]["red_bar_aligned"] = True
        prepared = self.prepare(raw)
        self.assertTrue({"mix_A", "mix_B", "bass_A", "bass_B", "bass_balanced", "hold"} <= set(prepared["option_actions"]))
        self.assertEqual(set(prepared["payload"]["questions"]), {"dj_action", "gesture"})
        action = brain.resolve(prepared, answer(prepared, "bass_B", gesture="beats4"))
        self.assertEqual(action["parameters"], {"target": "B", "duration_beats": 4})
        self.assertTrue(brain.applicable(action, prepared))

    def test_unaligned_beats_never_offer_faders_or_bass_and_alignment_targets_only_silent_deck(self):
        raw = load(load(frame(), "A", playing=True), "B", 1, playing=True)
        raw["mixer"]["crossfader_position"] = 0
        prepared = self.prepare(raw)
        self.assertIn("align_B", prepared["option_actions"])
        self.assertNotIn("align_A", prepared["option_actions"])
        self.assertTrue(all(a["action"] not in ("MIX", "BASS") for a in prepared["option_actions"].values()))
        raw["mixer"]["red_bar_aligned"] = True
        raw["decks"][1]["displayedBPM"] = "125.00"
        self.assertTrue(all(a["action"] not in ("MIX", "BASS") for a in self.prepare(raw)["option_actions"].values()))

    def test_stop_is_never_offered_for_only_audible_track(self):
        raw = load(load(frame(), "A", playing=True), "B", 1, playing=True)
        raw["mixer"]["crossfader_position"] = 1
        prepared = self.prepare(raw)
        self.assertIn("stop_A", prepared["option_actions"])
        self.assertNotIn("stop_B", prepared["option_actions"])
        raw["playingIndicators"]["deck1"] = False
        tracks = TRACKS + [{**TRACKS[0], "file": "next.mp3", "title": "Next", "key": "G"}]
        prepared = self.prepare(raw, tracks=tracks)
        self.assertNotIn("stop_B", prepared["option_actions"])
        self.assertIn("load_A", prepared["option_actions"])  # deck reuse, not empty-only prototype

    def test_eq_restore_safe_when_inaudible_or_sole_audible_and_neutral_precedes_pixel_angle(self):
        raw = load(load(frame(), "A", playing=True), "B", 1, playing=True)
        raw["mixer"]["red_bar_aligned"] = True
        bass(raw, "B", -.3)
        prepared = self.prepare(raw)
        self.assertNotIn("reset_B", prepared["option_actions"])
        raw["mixer"]["crossfader_position"] = 1
        self.assertIn("reset_B", self.prepare(raw)["option_actions"])
        bass(raw, "B", -.03, neutral=True)
        prepared = self.prepare(raw)
        self.assertEqual(prepared["payload"]["state"]["decks"]["B"]["eq_position"]["low"], 0)
        self.assertNotIn("reset_B", prepared["option_actions"])

    def test_unknown_and_stale_state_propose_observation_only(self):
        for raw, kwargs in ((frame(), {"now_ns": NOW+4_000_000_000}),
                            (frame(), {"session": {"pending_action": "LOAD_TRACK"}})):
            prepared = self.prepare(raw, **kwargs)
            self.assertTrue(prepared["observation_required"])
            self.assertEqual(prepared["decision"]["action"], "OBSERVE_AGAIN")
            self.assertEqual(prepared["payload"]["questions"], {})
        raw = frame(); raw["playingIndicators"]["deck1"] = None
        self.assertTrue(self.prepare(raw)["observation_required"])
        raw = frame(); raw["mixer"]["deck_assignments"]["1"] = "thru"
        self.assertTrue(self.prepare(raw)["observation_required"])

    def test_more_than_255_candidates_is_explicitly_blocked_without_silent_truncation(self):
        tracks = [{**TRACKS[0], "file": f"{i}.mp3", "title": f"Track {i}"} for i in range(256)]
        prepared = self.prepare(tracks=tracks)
        self.assertTrue(prepared["observation_required"])
        self.assertIn("255", prepared["reason"])

    def test_stale_answers_rejected_when_tracks_routes_or_alignment_change(self):
        raw = load(load(frame(), "A", playing=True), "B", 1, playing=True)
        raw["mixer"]["red_bar_aligned"] = True
        prepared = self.prepare(raw)
        action = brain.resolve(prepared, answer(prepared, "mix_B"))
        raw["mixer"]["red_bar_aligned"] = False
        self.assertFalse(brain.applicable(action, self.prepare(raw)))
        raw["mixer"]["red_bar_aligned"] = True
        load(raw, "B", 2, playing=True)
        self.assertFalse(brain.applicable(action, self.prepare(raw)))
        raw = frame()
        prepared = self.prepare(raw)
        action = brain.resolve(prepared, answer(prepared, "load_B"))
        load(raw, "B", 1, playing=True)
        self.assertFalse(brain.applicable(action, self.prepare(raw)))

    def test_ordinary_elapsed_time_progression_does_not_invalidate_answer(self):
        raw = load(frame(), "A", playing=True)
        prepared = self.prepare(raw)
        action = brain.resolve(prepared, answer(prepared, "load_B"))
        raw["decks"][0]["metadata"] = "Artist 1 124.00 Am -03:59.0 01:01.0"
        raw["sampledAtMonotonicNS"] = NOW+1_000_000_000
        fresh = self.prepare(raw, now_ns=NOW+1_000_000_000)
        self.assertEqual(prepared["state_token"], fresh["state_token"])
        self.assertTrue(brain.applicable(action, fresh))

    def test_resolve_strictly_rejects_missing_foreign_or_fabricated_choices(self):
        prepared = self.prepare()
        good = answer(prepared, "load_A")
        invalid = []
        r = copy.deepcopy(good); r["answers"].pop("next_track"); invalid.append(r)
        r = copy.deepcopy(good); r["answers"]["extra"] = {}; invalid.append(r)
        r = copy.deepcopy(good); r["answers"]["dj_action"]["choice"] = "mix_B"; invalid.append(r)
        r = copy.deepcopy(good); r["answers"]["dj_action"]["probabilities"]["load_A"] = float("nan"); invalid.append(r)
        r = copy.deepcopy(good); r["payload_id"] = "different"; invalid.append(r)
        r = copy.deepcopy(good); r["answers"]["dj_action"]["confidence"] = True; invalid.append(r)
        r = copy.deepcopy(good); r["answers"]["next_track"]["probabilities"]["t1"] = .3; invalid.append(r)
        for bad in invalid:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    brain.resolve(prepared, bad)

    def test_unused_speculative_answers_do_not_change_the_selected_action(self):
        prepared = self.prepare()
        action = brain.resolve(prepared, answer(prepared, "hold", track="t3"))
        self.assertEqual(action["action"], "HOLD")
        self.assertEqual(action["parameters"], {})

    def test_current_http_evaluator_accepts_prepared_multiquestion_request(self):
        prepared = self.prepare()
        payload = answer(prepared, "load_A")
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return json.dumps(payload).encode()
        captured = []
        def transport(request, timeout):
            captured.append(json.loads(request.data))
            return Response()
        result = evaluate(prepared, api_key="test-only", transport=transport)
        self.assertEqual(captured[0], prepared["payload"])
        self.assertEqual(result["payload_id"], digest(prepared["payload"]))
        self.assertEqual(brain.resolve(prepared, result)["action"], "LOAD_TRACK")


if __name__ == "__main__":
    unittest.main()
