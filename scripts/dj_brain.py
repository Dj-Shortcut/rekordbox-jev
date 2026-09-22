"""Pure Jev DJ action menu: observations in, typed choices out; no controls or I/O.

Jev chooses the next available action and its musical timing. This module only
bounds physical eligibility, candidate scope and typed answer validation. It
never selects an action, invents an audio/phrase fact, or schedules a whole mix.
"""
from copy import deepcopy
import math
import unicodedata

from contextual_observation import adapt_observation
from jev_decisions import MODEL, digest

DECKS = ("A", "B")
BANDS = ("trim", "high", "mid", "low")
GESTURES = {"beats2": 2, "beats4": 4, "beats8": 8, "beats16": 16}
PITCH = {"C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
         "E": 4, "Fb": 4, "F": 5, "E#": 5, "F#": 6, "Gb": 6,
         "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10,
         "B": 11, "Cb": 11}
GOAL = (
    "You are DJ Jev, playing an uninterrupted autonomous EDM set from folder 26. "
    "Choose the next actual action, not approval for a predetermined sequence. "
    "When nothing plays, start suitable music promptly. While a track plays, "
    "select and prepare its successor early; do not wait until the ending to load it. "
    "Let the currently audible track develop while ample playing time remains; "
    "early preparation does not mean immediately cutting away from it. "
    "Use observed remaining time and recent actions to progress the set and avoid silence. "
    "Choose when to launch, blend, exchange bass and complete the transition. "
    "Keep bass complementary: as one loses bass the other can gain it; restore "
    "neutral EQ on the sole audible track after a handoff. Keep the set moving beyond "
    "one transition. HOLD is a brief musical wait, not a request to stop the set. "
    "The local bridge executes your chosen bounded control gesture, reads the new "
    "state and immediately asks again. No person chooses on your behalf."
)
CONTRACT = {
    "evidence": "Deck titles, transport lights, current tempo, time displays, mixer pointers and red beat-group alignment are visual observations. Export metadata is labelled separately.",
    "unknown": "No audio listening, phrase boundary, vocals, drop, energy or song structure has been measured. Do not infer these facts from track titles or elapsed time.",
    "eq": "eq_position is signed visual pointer angle divided by 135 degrees, not dB or loudness. 0 means observed neutral; negative means left of neutral. eq_neutral overrides tiny pixel-angle offsets.",
    "route": "closed/open/partial describe the observed mixer path under normal crossfader assignments, not measured audio loudness or crossfader gain.",
    "alignment": "No fader or audible bass movement is eligible unless both playing decks have aligned red four-beat markers and matching displayed tempo. PREPARE can wait locally for alignment before closing a stopped deck route.",
    "parallel_questions": "Questions in this request are independent. Track and gesture answers are speculative and are consumed only when the corresponding action is chosen.",
    "key": "current_key comes from the visible track metadata label. It does not prove the sounding transposed key or that master tempo is enabled. No sounding-key shift is inferred.",
}
TECHNIQUE_GUIDANCE = (
    "Beat blending: prepare the successor inaudibly, align its beat groups, then "
    "introduce it gradually. Avoid two full bass parts competing: exchange bass "
    "complementarily while keeping the beat running. Remove the old track after "
    "the new contribution is established and restore the new sole audible deck's "
    "EQ to neutral. If actual phrase/drop landmarks are supplied, use them for a "
    "musical handoff; no such landmarks are currently measured, so do not claim "
    "a phrase-perfect or drop-swap transition. No loop is mandatory. These are "
    "technique guidelines distilled from the project's Crossfader tutorial notes, "
    "not a fixed order of actions or a predetermined mix length."
)


def _number(value, low=None, high=None):
    return (type(value) in (int, float) and math.isfinite(value)
            and (low is None or value >= low) and (high is None or value <= high))


def _body(raw):
    if not isinstance(raw, dict):
        return {}
    body = raw.get("result") if "ok" in raw or "result" in raw else raw
    return body if isinstance(body, dict) else {}


def _title(value):
    return " ".join(unicodedata.normalize("NFC", value or "").casefold().split())


def _key(value):
    if not isinstance(value, str):
        return None
    value = value.strip().replace("♭", "b").replace("♯", "#")
    minor = value.endswith("m")
    pitch = PITCH.get(value[:-1] if minor else value)
    return (pitch, minor) if pitch is not None else None


def _compatible(left, right):
    """Same key, adjacent fifth in same mode, or the relative major/minor."""
    a, b = _key(left), _key(right)
    if a is None or b is None:
        return False
    if a[1] == b[1]:
        return (b[0] - a[0]) % 12 in (0, 5, 7)
    return (a[0] + (3 if a[1] else 9)) % 12 == b[0]


def _cue(track, current_bpm):
    """Calculate an exported grid downbeat, never a phrase or audio landmark."""
    grid = track.get("beatgrid") if isinstance(track, dict) else None
    first = grid[0] if isinstance(grid, list) and grid and isinstance(grid[0], dict) else {}
    original_bpm = track.get("original_bpm") if isinstance(track, dict) else None
    position, bpm, beat = first.get("position_seconds"), first.get("bpm"), first.get("beat_in_bar")
    usable = (_number(position, 0) and _number(bpm, 1) and _number(beat, 1, 4)
              and int(beat) == beat and first.get("meter") == "4/4"
              and _number(original_bpm, 1) and _number(current_bpm, 1))
    downbeat = position + ((1-int(beat)) % 4)*60/bpm if usable else None
    offset = downbeat*original_bpm/current_bpm if usable else None
    return {"ready": _number(offset, 0, 2), "offset_seconds": offset,
            "track_downbeat_seconds": downbeat, "original_bpm": original_bpm,
            "source": "first exported 4/4 beatgrid entry plus next beat 1; track seconds scaled by original/current BPM, not a phrase"}


def _route(channel, position, name):
    if channel <= .01 or (name == "A" and position >= .99) or (name == "B" and position <= .01):
        return "closed"
    if channel >= .9 and ((name == "A" and position <= .01) or (name == "B" and position >= .99) or abs(position-.5) <= .01):
        return "open"
    return "partial"


def _history(session, library):
    identities = {track["id"]: track["id"] for track in library}
    identities.update({track["file"]: track["id"] for track in library})
    for track in library:
        if track.get("title") and sum(_title(t.get("title")) == _title(track["title"]) for t in library) == 1:
            identities[track["title"]] = track["id"]
    recent = []
    for item in session.get("recent_tracks", [])[-12:]:
        identity = item.get("id", item.get("track_id", item.get("file"))) if isinstance(item, dict) else item
        if isinstance(identity, str) and identity in identities:
            recent.append(identities[identity])
    actions = []
    for item in session.get("last_actions", session.get("history", []))[-12:]:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if isinstance(action, dict):
            item, action = action, action.get("action")
        if isinstance(action, str):
            params = item.get("parameters", {})
            params = params if isinstance(params, dict) else {}
            actions.append({"action": action, "parameters": {k: v for k, v in params.items()
                if k in ("deck", "outgoing", "incoming", "target", "track_id", "duration_beats")
                and (v is None or type(v) in (str, int, float, bool))}})
    return recent, actions


def _unavailable(reason, adapted=None):
    return {"kind": "jev_dj_request", "execution_enabled": False,
            "observation_required": True, "reason": reason,
            "decision": {"source": "code", "action": "OBSERVE_AGAIN", "reason": reason},
            "adapted": adapted or {}, "option_actions": {},
            "payload": {"model": MODEL, "state": {}, "questions": {}}}


def prepare(raw, tracks, session=None, now_ns=None):
    """Build all eligible choices from this fresh frame. Never choose one.

    A Jev request is compatible with contextual_live.evaluate/evaluate_recorded.
    An observation_required result must only cause another read, never a physical
    fallback action. Session defaults describe a new session, not inferred roles.
    """
    session = session if isinstance(session, dict) else {}
    if session.get("pending_action", "none") not in (None, "none") or session.get("action_confirmed", True) is not True:
        return _unavailable("An earlier control is pending or unconfirmed.")
    adapted = adapt_observation(raw, tracks, now_ns=now_ns)
    current, library = adapted["current"], adapted["library"]
    if current.get("readable") is not True:
        return _unavailable("Current calibrated observation is missing or stale.", adapted)
    body = _body(raw)
    mixer = body.get("mixer") if isinstance(body.get("mixer"), dict) else {}
    position = mixer.get("crossfader_position")
    assignments = mixer.get("deck_assignments", {})
    if not _number(position, 0, 1) or assignments.get("1") != "left" or assignments.get("2") != "right":
        return _unavailable("Crossfader position or normal deck assignments are unknown.", adapted)
    eq = mixer.get("eq_neutral", {})
    eq_positions = mixer.get("eq_position", {})
    sync = mixer.get("beat_sync_lit", {})
    master = mixer.get("master_lit", {})
    decks = {}
    by_id = {track["id"]: track for track in library}
    originals = {track.get("file"): track for track in tracks if isinstance(track, dict)} if isinstance(tracks, list) else {}
    for index, name in enumerate(DECKS, 1):
        deck = current[name]
        if (not isinstance(deck.get("track"), str) or type(deck.get("playing")) is not bool
                or not _number(deck.get("channel"), 0, 1)):
            return _unavailable(f"Deck {name} identity, playback or fader position is unknown.", adapted)
        if deck["track"] == "none" and deck["playing"]:
            return _unavailable(f"Deck {name} has contradictory empty and playing observations.", adapted)
        neutrals = eq.get(str(index), {})
        angles = eq_positions.get(str(index), {})
        observed_eq = {band: (0 if neutrals.get(band) is True else angles.get(band)
                             if _number(angles.get(band), -1, 1) else None) for band in BANDS}
        decks[name] = {"track": deck["track"], "playing": deck["playing"],
            "channel_position": deck["channel"], "route": _route(deck["channel"], position, name),
            "current_bpm": deck["bpm"], "current_key": deck["key"],
            "elapsed_s": deck.get("elapsed_s"), "remaining_s": deck.get("remaining_s"),
            "eq_position": observed_eq, "eq_neutral": {band: neutrals.get(band) if type(neutrals.get(band)) is bool else None for band in BANDS},
            "sync": sync.get(str(index)) if type(sync.get(str(index))) is bool else None,
            "master": master.get(str(index)) if type(master.get(str(index))) is bool else None,
            "original": {k: by_id[deck["track"]][k] for k in ("title", "artist", "bpm", "key", "duration_seconds")}
                if deck["track"] in by_id else None}
        original = originals.get(by_id.get(deck["track"], {}).get("file"), {})
        decks[name]["aligned_start_cue"] = _cue(original, deck["bpm"])
    playing = [d for d in DECKS if decks[d]["playing"]]
    audible = [d for d in playing if decks[d]["route"] != "closed"]
    loaded = {decks[d]["track"] for d in DECKS}
    recent, last_actions = _history(session, library)
    reference = audible[0] if len(audible) == 1 else None
    policy = session.get("selection_policy", {})
    if not isinstance(policy, dict):
        return _unavailable("Track selection policy must be an object.", adapted)
    tempo_percent = policy.get("max_tempo_change_percent", 6)
    delta = policy.get("max_bpm_difference")
    keys = policy.get("compatible_keys")
    if (not _number(tempo_percent, 0, 50) or (delta is not None and not _number(delta, 0))
            or (keys is not None and (not isinstance(keys, list) or not keys or not all(isinstance(k, str) and _key(k) for k in keys)))):
        return _unavailable("Track selection policy has invalid tempo/key limits.", adapted)
    candidates = []
    reference_valid = reference is None or (_number(decks[reference]["current_bpm"], 1, 999) and _key(decks[reference]["current_key"]))
    # Titles must be unique because this installation verifies load identity by OCR.
    title_counts = {}
    for track in library:
        title = _title(track.get("title"))
        title_counts[title] = title_counts.get(title, 0) + 1
    for track in library:
        if (track["id"] in loaded or track["id"] in recent or not _number(track.get("bpm"), 1, 999)
                or _key(track.get("key")) is None or not track.get("title")
                or title_counts[_title(track["title"])] != 1):
            continue
        if reference:
            if not reference_valid:
                continue
            ref = decks[reference]
            if abs((ref["current_bpm"] / track["bpm"] - 1) * 100) > tempo_percent:
                continue
            if delta is not None and abs(track["bpm"]-ref["current_bpm"]) > delta:
                continue
            if (track["key"] not in keys if keys is not None else not _compatible(ref["current_key"], track["key"])):
                continue
            if not _cue(originals.get(track["file"], {}), ref["current_bpm"])["ready"]:
                continue
        candidates.append(track)
    actions, descriptions = {}, {}
    def offer(option, description, action, **parameters):
        actions[option] = {"action": action, "parameters": parameters}
        descriptions[option] = description
    for name in DECKS:
        deck, other_name = decks[name], "B" if name == "A" else "A"
        other = decks[other_name]
        # Reusing a previously played stopped deck is essential for a whole set.
        # When both transports are stopped, replacing the open deck cannot
        # interrupt playing music and lets a new Start recover from ended tracks.
        can_load = not deck["playing"] and (deck["track"] == "none" or deck["route"] == "closed" or not playing)
        if can_load and candidates:
            offer("load_"+name, f"Load the separately selected next track onto stopped deck {name}; its route is {deck['route']}. It does not start yet. Prepare a successor early while music continues. When nothing plays, prefer the open-route deck so new music can start promptly.", "LOAD_TRACK", deck=name)
        if deck["track"] == "none":
            continue
        neutral = all(deck["eq_neutral"][band] is True for band in BANDS)
        non_neutral = any(deck["eq_neutral"][band] is False for band in BANDS)
        only_audible = deck["route"] != "closed" and (not other["playing"] or other["route"] == "closed")
        if non_neutral and (deck["route"] == "closed" or only_audible):
            offer("reset_"+name, f"Restore EQ/trim of deck {name} to neutral. Use after a completed handoff or while this deck is inaudible.", "RESET_EQ", deck=name)
        if deck["playing"]:
            if deck["route"] == "closed" and other["playing"] and other["route"] != "closed":
                offer("stop_"+name, f"Stop inaudible deck {name} while the other deck keeps playing. Frees it for the next track.", "STOP", deck=name)
            continue
        at_end = _number(deck["remaining_s"], 0, .5)
        if not playing and deck["route"] == "open" and neutral and not at_end:
            offer("play_"+name, f"Start ready deck {name} immediately to open or resume the set.", "PLAY", deck=name)
        if other["playing"] and other["route"] != "closed":
            bpm_match = (_number(deck["current_bpm"], 1) and _number(other["current_bpm"], 1)
                         and abs(deck["current_bpm"]-other["current_bpm"]) <= .02)
            prepared = (deck["route"] == "closed" and deck["sync"] is True and other["master"] is True
                        and bpm_match and _number(deck["eq_position"]["low"], -1, -.05))
            cue_ready = _cue(originals.get(by_id[deck["track"]]["file"], {}), other["current_bpm"])["ready"]
            if prepared and cue_ready and not at_end:
                offer("play_"+name, f"Launch prepared deck {name} inaudibly, aligned to playing deck {other_name}. Choose this before bringing it into the mix.", "PLAY", deck=name)
            elif not prepared:
                offer("prepare_"+name, f"Prepare stopped deck {name} for mixing with {other_name}: establish sync, lower its bass and close its route after checking beat alignment. The playing deck continues.", "PREPARE", deck=name, outgoing=other_name)
    both = len(playing) == 2
    tempo_aligned = (both and all(_number(decks[d]["current_bpm"], 1) for d in DECKS)
                     and abs(decks["A"]["current_bpm"]-decks["B"]["current_bpm"]) <= .02)
    aligned = current.get("red_markers_aligned") is True and tempo_aligned
    if both:
        for name in DECKS:
            other = "B" if name == "A" else "A"
            if not aligned and decks[name]["route"] == "closed" and decks[other]["route"] != "closed":
                offer("align_"+name, f"Correct beat alignment of inaudible deck {name} against {other}; keep the audible track running.", "ALIGN", incoming=name, outgoing=other)
        if aligned and all(decks[d]["channel_position"] >= .9 for d in DECKS):
            for target, target_position in (("A", 0), ("center", .5), ("B", 1)):
                if abs(position-target_position) > .01:
                    offer("mix_"+target, f"Move crossfader to {target} over the separately chosen gesture duration. Center blends both; A/B completes the handoff toward that deck. Beats are aligned now.", "MIX", target=target)
            bass = {d: decks[d]["eq_position"]["low"] for d in DECKS}
            if all(_number(v, -1, 0) for v in bass.values()):
                targets = {"A": {"A": 0, "B": -.6}, "B": {"A": -.6, "B": 0},
                           "balanced": {"A": -.3, "B": -.3}}
                for target, angles in targets.items():
                    if any(abs(bass[d]-angles[d]) > .06 for d in DECKS):
                        offer("bass_"+target, f"Exchange bass complementarily toward {target}: A/B gives that deck neutral bass while reducing the other; balanced shares the reduction. Do not boost above neutral.", "BASS", target=target)
    # A brief wait remains an actual Jev choice alongside every actionable state.
    offer("hold", "Keep the current music and controls briefly, then reconsider fresh state. Do not postpone preparation until the song ends or wait repeatedly while silent.", "HOLD")
    if len(actions) == 1:
        reason = "No safe executable control is currently observable."
        if reference and not reference_valid:
            reason = "The playing deck tempo/key is unknown; track compatibility cannot be checked."
        elif not candidates:
            reason = "No safe control or unused compatible folder-26 track is currently available."
        return _unavailable(reason, adapted)
    load_offered = any(a["action"] == "LOAD_TRACK" for a in actions.values())
    if load_offered and len(candidates) > 255:
        return _unavailable("Eligible track count exceeds the Choice limit of 255; no candidates were silently removed.", adapted)
    questions = {"dj_action": {"type": "choice", "instructions":
        "What should you do now to continue this autonomous EDM set? Consider the actual decks, "
        "remaining time and recent actions. Choose among all offered controls; there is no fixed phase "
        "sequence. Prepare music early, perform and complete transitions in time, and then reuse the freed deck. "
        "Loading and gesture parameters are answered independently in this same request when relevant.",
        "criteria": descriptions}}
    candidate_map = {}
    state_candidates = {}
    if load_offered:
        candidate_map = {"t"+str(i): track["id"] for i, track in enumerate(candidates, 1)}
        state_candidates = {option: {k: by_id[track_id][k] for k in ("title", "artist", "bpm", "key", "duration_seconds")}
                            for option, track_id in candidate_map.items()}
        questions["next_track"] = {"type": "choice", "instructions":
            "If you choose to load a track now, which candidate best opens or continues this EDM set? "
            "Compare all supplied eligible candidates, the current tracks and recent selections. "
            "Use key and tempo compatibility and variety; titles do not prove phrase, vocal or energy content. "
            "This answer is used only if the independently chosen action is LOAD_TRACK.",
            "criteria": {option: "Choose candidate "+option+" from state.candidates." for option in candidate_map}}
    gesture_offered = any(a["action"] in ("MIX", "BASS") for a in actions.values())
    if gesture_offered:
        questions["gesture"] = {"type": "choice", "instructions":
            "If you make one offered crossfader or bass gesture now, how many beats should that "
            "single gesture take? This is not the duration of the whole transition. Choose using current "
            "remaining time and mixer state; use only when MIX or BASS is selected.",
            "criteria": {key: f"Perform this single control gesture over {beats} beats." for key, beats in GESTURES.items()}}
    state = {"goal": GOAL, "state_contract": CONTRACT, "technique_guidance": TECHNIQUE_GUIDANCE, "decks": decks,
             "crossfader_position": position, "red_markers_aligned": current.get("red_markers_aligned"),
             "displayed_tempos_match": tempo_aligned,
             "recent_tracks": [{"id": identity, **{k: by_id[identity][k] for k in ("title", "artist", "bpm", "key")}} for identity in recent],
             "last_actions": last_actions,
             "selection_policy": {"folder": "26", "max_tempo_change_percent": tempo_percent,
                  "max_bpm_difference": delta, "compatible_keys": keys,
                  "default_key_rule": "same key, adjacent fifth in the same mode, or relative major/minor",
                  "successor_start_requirement": "exported 4/4 beatgrid gives an aligned starting downbeat within 2 seconds at current tempo",
                  "exclude_loaded_and_last_12_tracks": True},
             "candidates": state_candidates}
    payload = {"model": MODEL, "state": state, "questions": questions}
    token_state = {"decks": {d: {k: v for k, v in decks[d].items() if k not in ("elapsed_s", "remaining_s", "original")} for d in DECKS},
                   "crossfader_position": position, "aligned": current.get("red_markers_aligned")}
    return {"kind": "jev_dj_request", "provenance": "observed", "execution_enabled": False,
            "observation_required": False, "question_id": "dj_action",
            "track_question_id": "next_track" if load_offered else None,
            "gesture_question_id": "gesture" if gesture_offered else None,
            "decision": {"source": "jev", "question_id": "dj_action", "option_actions": actions},
            "option_actions": actions, "candidate_map": candidate_map, "library": by_id,
            "adapted": adapted, "payload": payload, "payload_id": digest(payload),
            "context_id": digest(token_state), "state_token": digest(token_state),
            "expected_tracks": {d: decks[d]["track"] for d in DECKS}}


def resolve(prepared, result):
    """Consume the provider's actual answer; reject malformed or foreign answers."""
    if (not isinstance(prepared, dict) or prepared.get("observation_required") is not False
            or prepared.get("execution_enabled") is not False
            or digest(prepared["payload"]) != prepared.get("payload_id")):
        raise ValueError("No valid Jev DJ request.")
    if not isinstance(result, dict) or not isinstance(result.get("model"), str) or not result["model"]:
        raise ValueError("No valid model response.")
    for field in ("payload_id", "context_id"):
        if field in result and result[field] != prepared[field]:
            raise ValueError("Jev answer belongs to a different request.")
    questions, answers = prepared["payload"]["questions"], result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("Jev answer does not contain exactly the requested questions.")
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict):
            raise ValueError("Invalid Jev Choice answer.")
        probabilities = answer.get("probabilities")
        if (answer.get("type") != "choice" or answer.get("choice") not in question["criteria"]
                or not _number(answer.get("confidence"), 0, 1)
                or not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"])
                or not all(_number(v, 0, 1) for v in probabilities.values())
                or abs(sum(probabilities.values())-1) > .03
                or probabilities[answer["choice"]]+1e-6 < max(probabilities.values())):
            raise ValueError("Invalid Jev Choice distribution or selection.")
    action = deepcopy(prepared["option_actions"][answers["dj_action"]["choice"]])
    if action["action"] == "LOAD_TRACK":
        action["parameters"]["track_id"] = prepared["candidate_map"][answers["next_track"]["choice"]]
    if action["action"] in ("MIX", "BASS"):
        action["parameters"]["duration_beats"] = GESTURES[answers["gesture"]["choice"]]
    action["expected_tracks"] = deepcopy(prepared["expected_tracks"])
    return action


def applicable(action, prepared):
    """Revalidate an already resolved answer against a newly observed menu."""
    if (not isinstance(action, dict) or prepared.get("observation_required") is not False
            or prepared.get("decision", {}).get("source") != "jev"
            or action.get("expected_tracks") != prepared.get("expected_tracks")):
        return False
    parameters = deepcopy(action.get("parameters"))
    if not isinstance(parameters, dict):
        return False
    kind = action.get("action")
    if kind == "LOAD_TRACK":
        track_id = parameters.pop("track_id", None)
        if track_id not in prepared["candidate_map"].values():
            return False
    if kind in ("MIX", "BASS"):
        beats = parameters.pop("duration_beats", None)
        if type(beats) is not int or beats not in GESTURES.values():
            return False
    return any(candidate == {"action": kind, "parameters": parameters}
               for candidate in prepared["option_actions"].values())
