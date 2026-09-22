"""Pure, isolated dry-run decision planner. No I/O, model calls or DJ controls.

Input uses the hertest-01 current-state schema. ``none`` is known absent;
None/missing essential observation is unknown. Optional session bookkeeping
``pending_action`` defaults to no pending action; any other value blocks reuse
of the observation. Physical routing assumes normal A/center/B crossfader
assignment and enabled master output, as in the declared synthetic test cases.
This is not a screenshot reader or a production executor.

build_decision(current, library) returns either a transparent code action or a
Jev question with at least two available options. No answer is filled in here.
The returned option_actions map tells a future executor what each answer means.
Every actual action would require fresh pre-dispatch checks and confirmation.
"""
from copy import deepcopy
from math import isfinite


GOAL = (
    "Continue an autonomous EDM DJ session using folder 26. Preserve musical "
    "flow and avoid silence. Prefer suitable phrase changes when the supplied "
    "observations identify them. With ample playing time, keep an incoming "
    "track inaudible away from a known phrase boundary; a confirmed phrase "
    "boundary in the outgoing outro is a suitable introduction opportunity. "
    "Prefer a supplied bass-handover boundary for a paired bass transfer; "
    "otherwise preserve the current bass blend while there is ample time. "
    "Avoid competing bass lines and overlapping "
    "lead vocals. Do not invent phrase, vocal, energy or audio-analysis facts. "
    "Decide only the next bounded step, including holding when appropriate."
)
STATE_CONTRACT = {
    "track": "none means confirmed empty; null means unknown.",
    "route": "Normalized mixer path: 0 closed, 1 fully open. This is not measured loudness.",
    "roles": "outgoing/incoming are established session roles, not guessed from left/right position.",
    "low_db": "0 neutral; a negative number reduced bass. Numbers here are synthetic supplied facts.",
    "unknown": "Missing/null musical facts remain unknown. There is no implicit audio analysis.",
    "scope": "All offered actions are physically eligible, but their musical timing is still your choice.",
}


def _number(value, minimum=None, maximum=None):
    return (type(value) in (int, float) and isfinite(value)
            and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum))


def _code(action, reason, **parameters):
    return {"source": "code", "action": action,
            "parameters": parameters, "reason": reason}


def _observe(reason):
    return _code("OBSERVE_AGAIN", reason)


def _route(deck, crossfader, name):
    if deck["channel"] == 0:
        return 0
    if crossfader not in ("A", "B", "center"):
        return None
    return deck["channel"] if crossfader in (name, "center") else 0


def _named(current, name):
    if name not in ("A", "B"):
        return None
    result = deepcopy(current[name])
    result.update(deck=name, route=_route(current[name], current.get("crossfader"), name))
    return result


def _jev(current, phase, instructions, criteria, actions, *, outgoing=None,
         incoming=None, candidates=None, selection_policy=None):
    assert len(criteria) >= 2, "A single permitted action belongs to code, not Jev."
    assert set(criteria) == set(actions)
    context = deepcopy(current.get("musical_context", {}))
    # Preserve supplied facts only; absent cues do not become false or inferred.
    for key in ("phrase_boundary_now", "phrase", "vocals", "energy_goal"):
        if key in current:
            context[key] = deepcopy(current[key])
    state = {
        "goal": GOAL,
        "phase": phase,
        "state_contract": deepcopy(STATE_CONTRACT),
        "current": deepcopy(current),
        "outgoing": _named(current, outgoing),
        "incoming": _named(current, incoming),
        "musical_context": context,
    }
    if candidates is not None:
        state["candidates"] = deepcopy(candidates)
        state["selection_policy"] = deepcopy(selection_policy)
    return {
        "source": "jev", "question_id": phase,
        "question": {"type": "choice", "instructions": instructions,
                     "criteria": deepcopy(criteria)},
        "state": state, "option_actions": deepcopy(actions),
    }


def _select(current, library, outgoing=None, target=None):
    if not isinstance(library, list):
        return _observe("Library unavailable.")
    loaded = {current[d]["track"] for d in ("A", "B")}
    candidates = []
    seen = set()
    for item in library:
        if not isinstance(item, dict):
            return _observe("Malformed library entry.")
        track_id = item.get("id")
        if not isinstance(track_id, str) or not track_id or track_id in seen:
            return _observe("Missing or duplicate library track identity.")
        seen.add(track_id)
        if item.get("folder") == "26" and track_id not in loaded:
            candidates.append(item)
    policy = {"folder": "26", "exclude_currently_loaded": True}
    if outgoing:
        playing = current[outgoing]
        if not _number(playing.get("bpm"), 1) or not isinstance(playing.get("key"), str):
            return _observe("Current tempo/key unknown; cannot check candidate compatibility.")
        # A declared prototype constraint, not a model prediction or scoring trick.
        config = current.get("selection_policy", {})
        if not isinstance(config, dict):
            return _observe("Selection policy invalid.")
        delta = config.get("max_bpm_difference", 2)
        keys = config.get("compatible_keys", [playing["key"]])
        if not _number(delta, 0) or not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
            return _observe("Selection policy invalid.")
        candidates = [c for c in candidates
                      if _number(c.get("bpm"), 1)
                      and abs(c["bpm"] - playing["bpm"]) <= delta
                      and c.get("key") in keys]
        policy.update(max_bpm_difference=delta, compatible_keys=keys,
                      key_policy="Exact listed keys only; no inferred harmonic mapping.")
    if not candidates:
        return _observe("No eligible track in the supplied library.")
    if len(candidates) == 1:
        return _code("SELECT_TRACK", "Only one eligible candidate; no model choice claimed.",
                     track_id=candidates[0]["id"], target_deck=target)
    criteria, actions = {}, {}
    for candidate in candidates:
        option = "TRACK_" + candidate["id"]
        criteria[option] = "Select candidate " + candidate["id"] + "; use its supplied metadata to judge musical suitability."
        actions[option] = {"action": "SELECT_TRACK", "parameters": {"track_id": candidate["id"], "target_deck": target}}
    return _jev(
        current, "next_track" if outgoing else "opening_track",
        ("Which eligible candidate best follows the playing outgoing track for the EDM session goal? "
         if outgoing else "Nothing is playing or ready. Which eligible candidate best opens the EDM session? ")
        + "Compare the supplied candidate metadata and musical context. Choose a track; this does not load or play it.",
        criteria, actions, outgoing=outgoing, incoming=target,
        candidates=candidates, selection_policy=policy,
    )


def build_decision(state, library):
    """Return one code decision or one contextual Jev choice; never execute it."""
    if not isinstance(state, dict):
        return _observe("State unavailable.")
    current = state.get("current", state)
    if not isinstance(current, dict) or current.get("readable") is not True:
        return _observe("Current observation is unreadable.")
    if current.get("pending_action") not in (None, "none") or current.get("action_confirmed", True) is not True:
        return _observe("An earlier action is pending or unconfirmed; do not issue another.")
    for name in ("A", "B"):
        deck = current.get(name)
        if not isinstance(deck, dict):
            return _observe("Deck observation missing.")
        track = deck.get("track")
        if not isinstance(track, str) or not track or type(deck.get("playing")) is not bool:
            return _observe("Track identity or playback state unknown.")
        if not _number(deck.get("channel"), 0, 1):
            return _observe("Channel position unknown or outside normalized bounds.")
        if track == "none" and deck["playing"]:
            return _observe("Contradictory empty-and-playing observation.")
        if "low_db" in deck and deck["low_db"] is not None and not _number(deck["low_db"]):
            return _observe("Bass position invalid.")
        if track != "none" and (not isinstance(library, list) or not any(
                isinstance(t, dict) and t.get("id") == track and t.get("folder") == "26"
                for t in library)):
            return _observe("Loaded track is not confirmed to belong to folder 26.")
    outgoing, incoming = current.get("outgoing_deck"), current.get("incoming_deck")
    role_none = outgoing == incoming == "none"
    roles_valid = outgoing in ("A", "B") and incoming in ("A", "B") and outgoing != incoming
    if not role_none and not roles_valid:
        return _observe("Transition direction unknown or contradictory.")
    selected = current.get("selected")
    if selected != "none" and not isinstance(selected, dict):
        return _observe("Pending track selection unknown.")
    playing_names = [d for d in ("A", "B") if current[d]["playing"]]
    empty_names = [d for d in ("A", "B") if current[d]["track"] == "none"]

    # These are factual next steps, not graded/model-selected musical decisions.
    if roles_valid:
        out, inc = _named(current, outgoing), _named(current, incoming)
        if out["track"] == "none" or inc["track"] == "none":
            return _observe("Established transition has an empty deck.")
        if inc["playing"] and inc["route"] == 1 and out["route"] == 0:
            return _code("FINISH_HANDOFF", "Outgoing route is closed and incoming plays at its fully open route.",
                         outgoing_deck=outgoing, incoming_deck=incoming, restore_eq_decks=[outgoing, incoming])

    if isinstance(selected, dict):
        track_id = selected.get("id")
        authorized = (selected.get("folder") == "26" and isinstance(library, list)
                      and any(isinstance(t, dict) and t.get("id") == track_id and t.get("folder") == "26" for t in library))
        if not authorized or not empty_names or not role_none:
            return _observe("Selected track cannot be confirmed safe to load into an empty deck.")
        target = selected.get("target_deck")
        if target is not None and target not in empty_names:
            return _observe("Selected load target is not empty.")
        target = target or empty_names[0]
        return _code("LOAD_SELECTED", "Confirmed folder-26 selection and empty stopped target.",
                     track_id=track_id, deck=target)

    if not playing_names:
        if len(empty_names) == 2 and role_none:
            return _select(current, library)
        ready = [d for d in ("A", "B") if current[d]["track"] != "none"
                 and _route(current[d], current.get("crossfader"), d) not in (0, None)]
        if len(ready) == 1 and role_none:
            return _code("START_LOADED", "Exactly one stopped loaded deck has a confirmed open mixer path.", deck=ready[0])
        return _observe("Silent configuration has no unique confirmed loaded open-route deck.")

    if role_none and len(playing_names) == 1 and len(empty_names) == 1:
        outgoing = playing_names[0]
        if _route(current[outgoing], current.get("crossfader"), outgoing) in (0, None):
            return _observe("Playing track has no confirmed audible route.")
        return _select(current, library, outgoing=outgoing, target=empty_names[0])
    if not roles_valid:
        return _observe("Current loaded-deck relationship is not supported or confirmed.")

    out, inc = _named(current, outgoing), _named(current, incoming)
    if not out["playing"] or out["route"] in (0, None) or inc["route"] is None:
        return _observe("Outgoing playback or transition routing is not confirmed.")
    parameters = {"outgoing_deck": outgoing, "incoming_deck": incoming}
    hold = {"action": "HOLD", "parameters": {}}
    if not inc["playing"]:
        if inc["route"] != 0:
            return _observe("Starting incoming would be audible before alignment can be checked.")
        return _jev(current, "launch",
                    "The incoming track is loaded and stopped with its route closed. Given the musical context, launch it silently now to prepare alignment, or wait?",
                    {"LAUNCH_INCOMING": "Start the incoming track inaudibly at this musical moment; faders remain unchanged.",
                     "HOLD": "Keep it stopped and preserve outgoing playback for now."},
                    {"LAUNCH_INCOMING": {"action": "LAUNCH_INCOMING", "parameters": parameters}, "HOLD": hold},
                    outgoing=outgoing, incoming=incoming)
    aligned = current.get("red_markers_aligned")
    if aligned is False and inc["route"] == 0:
        return _code("ALIGN_BEATS", "Incoming is playing silently and observed beat groups do not align.", **parameters)
    if aligned is not True:
        return _observe("Alignment is unknown or false; no fader or bass movement is eligible.")
    if not _number(out.get("low_db")) or not _number(inc.get("low_db")):
        return _observe("Bass positions unknown; cannot choose a compatible mixing gesture.")

    if inc["route"] == 0 and out["low_db"] == 0 and inc["low_db"] < 0:
        phase, action = "introduce", "INTRODUCE_INCOMING"
        prompt = "Both tracks are playing with aligned beat groups; incoming is muted with reduced bass. Is this a musically suitable moment to introduce its upper frequencies, or should the outgoing track continue alone?"
        description = "Gradually open incoming enough to introduce its upper frequencies; retain outgoing bass and reduced incoming bass."
    elif inc["route"] > 0 and out["low_db"] == 0 and inc["low_db"] < 0:
        phase, action = "bass_transfer", "TRANSFER_BASS"
        prompt = "Both aligned tracks are audible; outgoing supplies the bass, incoming bass is reduced. Is this a suitable musical moment to transfer bass, or keep the present blend?"
        description = "Reduce outgoing bass while restoring incoming bass with paired complementary adjustments."
    elif inc["route"] > 0 and out["low_db"] < 0 and inc["low_db"] == 0:
        phase, action = "remove_outgoing", "REMOVE_OUTGOING"
        prompt = "Both aligned tracks are audible and incoming now supplies the bass. Should the outgoing track be faded away now, or should this upper-frequency blend continue?"
        description = "Fade the outgoing route toward closed while maintaining the incoming track; do not stop an audible deck."
    else:
        return _observe("No supported bounded mixing gesture matches the confirmed EQ/routing state.")
    return _jev(current, phase, prompt,
                {action: description, "HOLD": "Preserve the current blend and playback for now; wait for a more suitable musical moment."},
                {action: {"action": action, "parameters": parameters}, "HOLD": hold},
                outgoing=outgoing, incoming=incoming)
