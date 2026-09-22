"""Compact only the representation of contextual track-choice requests.

No candidate shortlist, model call, policy change or DJ action. Real file-based
identities stay in local option_actions/adapted.library; Jev sees t1, t2, ... .
All candidate musical facts are kept. Repeated file identity, scope and export
provenance move to local compaction metadata. Other decision kinds are unchanged.
"""
from copy import deepcopy

from jev_decisions import digest


_SECRET_KEYS = frozenset({"api_key", "typesafe_api_key", "authorization", "password",
                          "secret", "access_token", "refresh_token", "bearer_token"})
_STATE_METADATA = frozenset({"live_evidence", "provenance", "metadata_source", "debug", "diagnostics"})


def _reject_secret_fields(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower().replace("-", "_") in _SECRET_KEYS:
                raise ValueError("Credential fields do not belong in prepared model requests.")
            _reject_secret_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_fields(child)


def _compact_criterion(description, original_id, compact_id):
    canonical = "Select candidate " + original_id + "; use its supplied metadata to judge musical suitability."
    if description == canonical:
        # The unchanged question already asks for a musical metadata comparison.
        return "Select candidate " + compact_id + "."
    if isinstance(description, str):
        return description.replace(original_id, compact_id)
    if isinstance(description, dict):
        return {key: _compact_criterion(value, original_id, compact_id) for key, value in description.items()}
    if isinstance(description, list):
        return [_compact_criterion(value, original_id, compact_id) for value in description]
    return deepcopy(description)


def _compact_state(state, short_ids, expected_ids):
    result = deepcopy(state)
    metadata = {key: result.pop(key) for key in _STATE_METADATA if key in result}
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or [c.get("id") if isinstance(c, dict) else None for c in candidates] != expected_ids:
        raise ValueError("Candidate identities differ between decision and payload.")
    removed_candidates = {}
    folder = result.get("selection_policy", {}).get("folder")
    for candidate in candidates:
        original_id = candidate["id"]
        compact_id = short_ids[original_id]
        removed = {}
        # Keep every other candidate field, including played/energy/genre facts
        # if a later caller has supplied them. Missing facts stay missing/null.
        for key in ("file", "metadata_source"):
            if key in candidate:
                removed[key] = candidate.pop(key)
        if folder is not None and candidate.get("folder") == folder:
            removed["folder"] = candidate.pop("folder")
        candidate["id"] = compact_id
        removed_candidates[compact_id] = removed
    return result, {"state_metadata": metadata, "candidate_metadata": removed_candidates}


def compact_prepared(prepared):
    """Deep-copy a prepared request, shortening track identifiers reversibly.

    No answer is supplied or changed. Returned compaction metadata is local:
    contextual_live.evaluate transmits only returned['payload'].
    """
    if not isinstance(prepared, dict):
        raise ValueError("Expected a prepared request object.")
    _reject_secret_fields(prepared)
    copied = deepcopy(prepared)
    decision = copied.get("decision")
    if not isinstance(decision, dict) or decision.get("source") != "jev":
        return copied
    actions = decision.get("option_actions")
    if (not isinstance(actions, dict) or not actions
            or not all(isinstance(action, dict) and action.get("action") == "SELECT_TRACK"
                       for action in actions.values())):
        return copied
    # Idempotence preserves the first reversible mapping and original payload ID.
    if copied.get("compaction", {}).get("version") == 1:
        if digest(copied.get("payload")) != copied.get("payload_id"):
            raise ValueError("Compacted payload identity does not match its contents.")
        return copied
    if copied.get("execution_enabled") is not False:
        raise ValueError("Only read-only prepared requests can be compacted.")
    payload = copied.get("payload")
    if not isinstance(payload, dict) or digest(payload) != copied.get("payload_id"):
        raise ValueError("Prepared payload identity does not match its contents.")
    question_id = decision.get("question_id")
    questions = payload.get("questions")
    question = decision.get("question")
    if (not isinstance(questions, dict) or set(questions) != {question_id}
            or not isinstance(question, dict) or questions[question_id] != question
            or question.get("type") != "choice"):
        raise ValueError("Track question differs between decision and payload.")
    criteria = question.get("criteria")
    state = payload.get("state")
    if (not isinstance(criteria, dict) or set(criteria) != set(actions) or len(criteria) < 2
            or not isinstance(state, dict) or not isinstance(decision.get("state"), dict)):
        raise ValueError("Expected at least two consistently mapped track choices.")
    candidates = state.get("candidates")
    if not isinstance(candidates, list) or not all(isinstance(candidate, dict) for candidate in candidates):
        raise ValueError("Track candidates are missing.")
    identities = [candidate.get("id") for candidate in candidates]
    if (not all(isinstance(identity, str) and identity for identity in identities)
            or len(set(identities)) != len(identities)):
        raise ValueError("Candidate identities must be unique.")
    option_tracks = {}
    for option, action in actions.items():
        parameters = action.get("parameters")
        identity = parameters.get("track_id") if isinstance(parameters, dict) else None
        if identity not in identities:
            raise ValueError("A track option has no matching candidate.")
        option_tracks[option] = identity
    if len(set(option_tracks.values())) != len(identities) or len(option_tracks) != len(identities):
        raise ValueError("Each candidate must have exactly one track choice.")
    short_ids = {identity: "t" + str(index) for index, identity in enumerate(identities, 1)}
    new_criteria, new_actions, reverse_options = {}, {}, {}
    for option, description in criteria.items():
        identity = option_tracks[option]
        compact_id = short_ids[identity]
        new_criteria[compact_id] = _compact_criterion(description, identity, compact_id)
        # Keep original identity and every action argument locally, unchanged.
        new_actions[compact_id] = deepcopy(actions[option])
        reverse_options[compact_id] = option
    new_question = {**deepcopy(question), "criteria": new_criteria}
    payload["state"], payload_metadata = _compact_state(state, short_ids, identities)
    decision["state"], decision_metadata = _compact_state(decision["state"], short_ids, identities)
    decision["question"] = deepcopy(new_question)
    decision["option_actions"] = new_actions
    payload["questions"][question_id] = deepcopy(new_question)
    copied["compaction"] = {
        "version": 1,
        "original_payload_id": copied["payload_id"],
        "candidate_ids": {short_id: identity for identity, short_id in short_ids.items()},
        "original_options": reverse_options,
        "payload_metadata": payload_metadata,
        "decision_metadata": decision_metadata,
        "candidate_count": len(identities),
        "policy_changed": False,
    }
    copied["payload_id"] = digest(payload)
    return copied
