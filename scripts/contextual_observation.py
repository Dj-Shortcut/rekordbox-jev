"""Translate one native Bridge frame into contextual-planner facts, without I/O.

This adapter never reads a file, captures a screen, calls Jev or controls a deck.
The caller supplies the observation, exported library and monotonic clock value.
Omitting now_ns leaves freshness unknown, so the observation is not readable.

session=None means a NEW READ-ONLY INSPECTION session: this adapter has selected
no candidate, established no transition and issued no pending command. These
are bookkeeping facts, not recognition of browser selection or transition roles.
An ongoing application must pass its own session values instead.

The adapter reports playback *indicators*, not audio detection. The three
crossfader categories are approximate visual positions under normal assignments;
they do not establish the crossfader curve, master output, or actual loudness.
"""
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from pathlib import PurePosixPath
import re
import unicodedata


CROSSFADER_POSITION_TOLERANCE = 0.01


def _finite(value, minimum=None, maximum=None):
    return (type(value) in (int, float) and isfinite(value)
            and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum))


def _text(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split()) if isinstance(value, str) else None


def _empty_marker(value):
    normalized = _text(value)
    return normalized is not None and normalized.rstrip(". ") == "not loaded"


def _blank(value):
    # None is unknown, including when native optional values become JSON null.
    return isinstance(value, str) and not value.strip()


def _number_text(value):
    if not isinstance(value, str):
        return None
    # The calibrated crop can include the adjacent pitch value, for example
    # "123.00 0.0%". Only that explicitly percent-labelled suffix is allowed;
    # a second unlabelled number or other OCR text leaves tempo unknown.
    match = re.fullmatch(
        r"\s*(\d{2,3}(?:[.,]\d+)?)(?:\s+[+\-\N{MINUS SIGN}]?\d+(?:[.,]\d+)?\s*%)?\s*",
        value)
    if not match:
        return None
    number = float(match[1].replace(",", "."))
    return number if 1 <= number <= 999 else None


def _time_seconds(value):
    match = re.fullmatch(r"(-?)(\d{2,3}):(\d{2})\.(\d)", value)
    if not match or int(match[3]) >= 60:
        return None
    seconds = int(match[2]) * 60 + int(match[3]) + int(match[4]) / 10
    return -seconds if match[1] else seconds


def _library(tracks, missing):
    if not isinstance(tracks, list):
        missing.append("library: no supplied library list")
        return []
    result, duplicates, seen = [], set(), set()
    for track in tracks:
        if not isinstance(track, dict) or track.get("music_scope") != "26":
            continue
        filename = track.get("file")
        if (not isinstance(filename, str) or not filename or filename in (".", "..")
                or PurePosixPath(filename).name != filename or "\\" in filename):
            missing.append("library: folder-26 entry has no valid basename identity")
            continue
        identity = "f26_" + sha256(("26/" + filename).encode("utf-8")).hexdigest()[:20]
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
        title = track.get("title")
        result.append({
            "id": identity, "folder": "26", "file": filename,
            "title": title if isinstance(title, str) and title.strip() else None,
            "artist": track.get("artist") if isinstance(track.get("artist"), str) else None,
            "bpm": track.get("original_bpm") if _finite(track.get("original_bpm"), 1, 999) else None,
            "key": track.get("key") if isinstance(track.get("key"), str) and track["key"].strip() else None,
            "duration_seconds": track.get("duration_seconds") if _finite(track.get("duration_seconds"), 0) else None,
            "metadata_source": track.get("source", "supplied library export"),
        })
    if duplicates:
        missing.append("library: duplicate file identities excluded")
        result = [track for track in result if track["id"] not in duplicates]
    return result


def adapt_observation(raw, library, session=None, now_ns=None, max_age_seconds=3):
    """Return {'current', 'library', 'provenance', 'missing'} from supplied facts."""
    missing, fields = [], {}
    supplied_raw = raw if isinstance(raw, dict) else {}
    envelope = "ok" in supplied_raw or "result" in supplied_raw
    success = not envelope or supplied_raw.get("ok") is True
    body = supplied_raw.get("result") if envelope else supplied_raw
    body = body if isinstance(body, dict) else {}
    calibrated = body.get("layoutCalibrated") is True
    sampled = body.get("sampledAtMonotonicNS")
    age_seconds = None
    if (type(sampled) is int and sampled >= 0 and type(now_ns) is int and now_ns >= 0
            and _finite(max_age_seconds, 0)):
        age_seconds = (now_ns - sampled) / 1_000_000_000
    fresh = age_seconds is not None and 0 <= age_seconds <= max_age_seconds
    readable = success and calibrated and fresh
    if not success:
        missing.append("observation: native request was unsuccessful")
    if not calibrated:
        missing.append("observation: layout not confirmed calibrated")
    if not fresh:
        missing.append("observation: timestamp missing, stale, future or clock comparison unavailable")
    adapted_library = _library(library, missing)
    fields["readable"] = {"source": "native status, layoutCalibrated and caller-supplied monotonic clock",
                          "sampled_at_monotonic_ns": sampled, "age_seconds": age_seconds,
                          "max_age_seconds": max_age_seconds}
    current = {"readable": readable}
    if session is None:
        bookkeeping = {"selected": "none", "outgoing_deck": "none", "incoming_deck": "none",
                       "pending_action": "none", "action_confirmed": True}
        session_source = "new read-only inspection session; no selection, transition or command was issued by this adapter"
    elif isinstance(session, dict):
        # Missing established session facts are unknown, not silently reset.
        bookkeeping = {name: deepcopy(session.get(name)) for name in
                       ("selected", "outgoing_deck", "incoming_deck", "pending_action", "action_confirmed")}
        session_source = "supplied application session bookkeeping; not screenshot-derived"
    else:
        bookkeeping = {name: None for name in
                       ("selected", "outgoing_deck", "incoming_deck", "pending_action", "action_confirmed")}
        session_source = "invalid application session bookkeeping"
        missing.append("session: supplied session is not an object")
    current.update(bookkeeping)
    for name in bookkeeping:
        fields[name] = {"source": session_source}
        if bookkeeping[name] is None:
            missing.append("session." + name + ": unknown")

    mixer = body.get("mixer") if isinstance(body.get("mixer"), dict) else {}
    indicators = body.get("playingIndicators") if isinstance(body.get("playingIndicators"), dict) else {}
    faders = body.get("faders") if isinstance(body.get("faders"), dict) else {}
    eq = mixer.get("eq_neutral") if isinstance(mixer.get("eq_neutral"), dict) else {}
    raw_decks = body.get("decks") if isinstance(body.get("decks"), list) else []
    for number, label in ((1, "A"), (2, "B")):
        observations = [d for d in raw_decks if isinstance(d, dict) and type(d.get("deck")) is int and d["deck"] == number]
        deck = observations[0] if len(observations) == 1 else {}
        indicator = indicators.get("deck" + str(number))
        playing = indicator if type(indicator) is bool else None
        channel = faders.get("deck" + str(number))
        channel = channel if _finite(channel, 0, 1) else None
        title = deck.get("title")
        normalized_title = _text(title)
        candidates = [track for track in adapted_library if normalized_title and normalized_title in
                      (_text(track["title"]), _text(PurePosixPath(track["file"]).stem))]
        empty_shape = (readable and len(observations) == 1 and _empty_marker(title)
                       and _blank(deck.get("metadata")) and _blank(deck.get("displayedBPM"))
                       and playing is False)
        explicit_empty = empty_shape and not candidates
        if explicit_empty:
            track_id = "none"
            track_source = "explicit Not Loaded OCR label, blank present metadata/BPM, false play indicator in a fresh calibrated frame"
        elif len(candidates) == 1 and readable and not empty_shape:
            track_id = candidates[0]["id"]
            track_source = "unique exact normalized OCR title or filename-stem match in supplied folder-26 export"
        else:
            track_id = None
            track_source = "unknown: title absent, ambiguous, unmatched, contradictory empty label, or frame unavailable"
            missing.append(label + ".track: " + track_source)
        neutral_deck = eq.get(str(number)) if isinstance(eq.get(str(number)), dict) else {}
        low_neutral = neutral_deck.get("low")
        low_db = 0 if low_neutral is True and readable else None
        bpm = _number_text(deck.get("displayedBPM")) if readable else None
        metadata = deck.get("metadata") if isinstance(deck.get("metadata"), str) else ""
        key_match = re.search(r"\b\d{2,3}[.,]\d+\s+([A-G](?:b|#)?m?)\b", metadata)
        key = key_match[1] if key_match and readable else None
        current[label] = {"track": track_id, "playing": playing if readable else None,
                          "channel": channel if readable else None, "low_db": low_db,
                          "bpm": bpm, "key": key}
        fields[label + ".track"] = {"source": track_source, "observed_title": title}
        fields[label + ".playing"] = {"source": "native playingIndicators.deck" + str(number),
                                       "limit": "visual play-button indicator only; not detected audio or measured transport movement"}
        fields[label + ".channel"] = {"source": "native faders.deck" + str(number),
                                       "limit": "normalized visual knob position, not loudness"}
        fields[label + ".low_db"] = {"source": "native mixer.eq_neutral low pointer classification",
                                      "observed_neutral": low_neutral,
                                      "limit": "0 denotes visually neutral only; false/null cannot establish attenuation or an exact negative dB value"}
        fields[label + ".bpm"] = {"source": "current displayedBPM OCR only; original export tempo is not substituted"}
        fields[label + ".key"] = {"source": "current metadata OCR after numeric tempo; exported key is not substituted"}
        if current[label]["playing"] is None:
            missing.append(label + ".playing: visual indicator unknown")
        if current[label]["channel"] is None:
            missing.append(label + ".channel: visual position unknown or invalid")
        if low_db is None:
            missing.append(label + ".low_db: only neutral can be represented; attenuation unknown")
        if track_id not in (None, "none"):
            if bpm is None:
                missing.append(label + ".bpm: current displayed tempo unknown")
            if key is None:
                missing.append(label + ".key: current displayed key unknown")
        displays = re.findall(r"-?\d{2,3}:\d{2}\.\d", metadata)
        positive = [_time_seconds(value) for value in displays if not value.startswith("-")]
        negative = [_time_seconds(value) for value in displays if value.startswith("-")]
        if readable and len(positive) == len(negative) == 1 and None not in (positive[0], negative[0]):
            current[label].update(elapsed_s=positive[0], remaining_s=-negative[0])
            fields[label + ".time"] = {"source": "current metadata OCR, exactly one elapsed and one remaining time"}

    position = mixer.get("crossfader_position")
    position = position if _finite(position, 0, 1) and readable else None
    assignments = mixer.get("deck_assignments") if isinstance(mixer.get("deck_assignments"), dict) else {}
    normal_assignments = assignments.get("1") == "left" and assignments.get("2") == "right"
    discrete = None
    if position is not None and normal_assignments:
        if position <= CROSSFADER_POSITION_TOLERANCE:
            discrete = "A"
        elif position >= 1 - CROSSFADER_POSITION_TOLERANCE:
            discrete = "B"
        elif .5 - CROSSFADER_POSITION_TOLERANCE <= position <= .5 + CROSSFADER_POSITION_TOLERANCE:
            discrete = "center"
    current.update(crossfader=discrete, crossfader_position=position)
    fields["crossfader"] = {"source": "native mixer visual position plus deck assignments",
                            "normal_assignments_confirmed": normal_assignments,
                            "category_tolerance": CROSSFADER_POSITION_TOLERANCE,
                            "limit": "approximate endpoint/center category only; curve, routing audibility and master output are not measured"}
    if discrete is None:
        missing.append("crossfader: outside supported endpoint/center categories or normal assignments unknown")
    aligned = mixer.get("red_bar_aligned")
    current["red_markers_aligned"] = aligned if type(aligned) is bool and readable else None
    fields["red_markers_aligned"] = {"source": "native same-frame red marker visual comparison",
                                     "limit": "screen beat-group alignment, not audio analysis"}
    if current["red_markers_aligned"] is None:
        missing.append("red_markers_aligned: unknown")
    # In particular, no phrase, section, vocals or musical-boundary facts are made up.
    return {"current": current, "library": adapted_library,
            "provenance": {"fields": fields,
                           "library": "caller-supplied export entries explicitly scoped to folder 26; stable IDs hash scope and file basename",
                           "musical_analysis": "no phrase, section, vocal, bass amount or audio loudness inference"},
            "missing": missing}
