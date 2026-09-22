"""Representation/coverage tests, not evidence of model speed or accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from contextual_observation import adapt_observation
from contextual_planner import build_decision
from contextual_payload import compact_prepared
from jev_decisions import digest, MODEL


def prepared_tracks(count=127):
    raw = {"layoutCalibrated": True, "sampledAtMonotonicNS": 100,
           "decks": [{"deck": n, "title": "Not Loaded", "metadata": "", "displayedBPM": ""} for n in (1, 2)],
           "playingIndicators": {"deck1": False, "deck2": False},
           "faders": {"deck1": 1, "deck2": 1},
           "mixer": {"crossfader_position": .5, "deck_assignments": {"1": "left", "2": "right"},
                     "eq_neutral": {"1": {"low": True}, "2": {"low": True}}, "red_bar_aligned": None}}
    library = [{"file": "track-" + str(n) + ".mp3", "music_scope": "26",
                "title": "Track " + str(n), "artist": "Artist " + str(n),
                "original_bpm": 124 + n % 3, "key": "Abm", "duration_seconds": 200 + n}
               for n in range(count)]
    # Build the pre-compaction DTO with the real adapter/planner. This fixture
    # must remain independent of whether contextual_live.prepare integrates
    # compact_prepared before or after these representation tests are written.
    adapted = adapt_observation(raw, library, now_ns=100)
    decision = build_decision(adapted["current"], adapted["library"])
    state = deepcopy(decision["state"])
    state["live_evidence"] = deepcopy(adapted["provenance"])
    payload = {"model": MODEL, "state": state,
               "questions": {decision["question_id"]: deepcopy(decision["question"])}}
    return {"kind": "jev_contextual_request", "provenance": "observed", "execution_enabled": False,
            "adapted": adapted, "decision": decision, "payload": payload,
            "payload_id": digest(payload), "context_id": digest(adapted["current"])}


class ContextualPayloadTests(unittest.TestCase):
    def test_all_127_choices_survive_with_reversible_original_actions(self):
        original = prepared_tracks()
        result = compact_prepared(original)
        before, after = original["decision"], result["decision"]
        self.assertEqual(len(after["question"]["criteria"]), 127)
        self.assertEqual(set(after["question"]["criteria"]), {"t" + str(n) for n in range(1, 128)})
        self.assertEqual(result["payload"]["questions"][after["question_id"]], after["question"])
        self.assertEqual(set(after["option_actions"]), set(after["question"]["criteria"]))
        for short_id, original_option in result["compaction"]["original_options"].items():
            self.assertEqual(after["option_actions"][short_id], before["option_actions"][original_option])
            self.assertEqual(after["option_actions"][short_id]["parameters"]["track_id"],
                             result["compaction"]["candidate_ids"][short_id])

    def test_useful_facts_policy_and_unknown_values_are_unchanged(self):
        original = prepared_tracks()
        for state in (original["payload"]["state"], original["decision"]["state"]):
            state["candidates"][0].update(energy=None, played=False, genre="EDM")
            state["musical_context"] = {"unknown_phrase": None}
        original["payload_id"] = digest(original["payload"])
        result = compact_prepared(original)
        for name in ("payload", "decision"):
            before, after = original[name]["state"], result[name]["state"]
            for key in ("goal", "current", "outgoing", "incoming", "musical_context", "selection_policy", "state_contract"):
                self.assertEqual(after[key], before[key])
            for source, compact in zip(before["candidates"], after["candidates"]):
                for key, value in source.items():
                    if key not in ("id", "folder", "file", "metadata_source"):
                        self.assertEqual(compact[key], value)
        self.assertEqual(result["adapted"]["library"], original["adapted"]["library"])

    def test_provenance_stays_local_and_context_identity_is_preserved(self):
        original = prepared_tracks()
        result = compact_prepared(original)
        self.assertNotIn("live_evidence", result["payload"]["state"])
        self.assertEqual(result["compaction"]["payload_metadata"]["state_metadata"]["live_evidence"],
                         original["payload"]["state"]["live_evidence"])
        self.assertEqual(result["payload_id"], digest(result["payload"]))
        self.assertEqual(result["context_id"], original["context_id"])
        self.assertNotEqual(result["payload_id"], original["payload_id"])
        self.assertIs(result["execution_enabled"], False)
        self.assertNotIn("compaction", result["payload"])

    def test_input_unchanged_idempotent_and_no_option_shortlist(self):
        original = prepared_tracks()
        saved = deepcopy(original)
        compact = compact_prepared(original)
        self.assertEqual(original, saved)
        self.assertEqual(compact_prepared(compact), compact)
        self.assertEqual([c["id"] for c in compact["payload"]["state"]["candidates"]],
                         ["t" + str(n) for n in range(1, 128)])
        self.assertEqual(compact["compaction"]["candidate_count"], len(saved["payload"]["state"]["candidates"]))

    def test_nontrack_and_code_decisions_are_deepcopies_without_any_change(self):
        for original in ({"execution_enabled": False, "decision": {"source": "code", "action": "OBSERVE_AGAIN"}},
                         {"execution_enabled": False, "decision": {"source": "jev", "option_actions": {
                             "HOLD": {"action": "HOLD"}, "TRANSFER_BASS": {"action": "TRANSFER_BASS"}}}}):
            copied = compact_prepared(original)
            self.assertEqual(copied, original)
            self.assertIsNot(copied, original)
            self.assertIsNot(copied["decision"], original["decision"])

    def test_custom_criterion_meaning_is_not_dropped(self):
        original = prepared_tracks(2)
        question = original["decision"]["question"]
        option = next(iter(question["criteria"]))
        identity = original["decision"]["option_actions"][option]["parameters"]["track_id"]
        description = {"choose": "Prefer " + identity + " if a restrained opening suits the goal.", "energy": "low"}
        question["criteria"][option] = description
        original["payload"]["questions"][original["decision"]["question_id"]] = deepcopy(question)
        original["payload_id"] = digest(original["payload"])
        result = compact_prepared(original)
        self.assertEqual(result["decision"]["question"]["criteria"]["t1"],
                         {"choose": "Prefer t1 if a restrained opening suits the goal.", "energy": "low"})

    def test_inconsistent_candidate_option_or_payload_identity_is_rejected(self):
        original = prepared_tracks(2)
        bad = deepcopy(original); bad["payload_id"] = "wrong"
        with self.assertRaises(ValueError): compact_prepared(bad)
        bad = deepcopy(original)
        first = next(iter(bad["decision"]["option_actions"]))
        bad["decision"]["option_actions"][first]["parameters"]["track_id"] = "missing"
        with self.assertRaises(ValueError): compact_prepared(bad)
        bad = deepcopy(original); bad["decision"]["state"]["candidates"].reverse()
        with self.assertRaises(ValueError): compact_prepared(bad)

    def test_credential_fields_rejected_without_echoing_values(self):
        original = prepared_tracks(2)
        original["payload"]["state"]["api_key"] = "private-test-sentinel"
        with self.assertRaises(ValueError) as caught:
            compact_prepared(original)
        self.assertNotIn("private-test-sentinel", str(caught.exception))

    def test_bytes_decrease_without_claiming_latency_or_model_quality(self):
        original = prepared_tracks()
        compact = compact_prepared(original)
        before = len(json.dumps(original["payload"], allow_nan=False).encode())
        after = len(json.dumps(compact["payload"], allow_nan=False).encode())
        self.assertLess(after, before)
        self.assertGreaterEqual(len(compact["decision"]["question"]["criteria"]), 2)


if __name__ == "__main__":
    unittest.main()
