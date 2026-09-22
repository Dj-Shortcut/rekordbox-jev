#!/usr/bin/env python3
"""Verified high-level operations for the local bridge. No autonomous DJ policy."""
import argparse
import json
import re
import time
from pathlib import Path
from bridge import request


class NativeCommandError(RuntimeError):
    """A native rejection. Absence of proof of no input never permits retry."""
    def __init__(self, message, *, code=None, commands_sent=None):
        super().__init__(message)
        self.code = code
        self.commands_sent = commands_sent
        self.retryable = False


class PreDispatchRejected(NativeCommandError):
    """A fresh observation may recover this rejection; no input was sent."""
    def __init__(self, message, *, code):
        super().__init__(message, code=code, commands_sent=False)
        self.retryable = True


def _raise_native_error(response):
    message = response.get('error', 'Onbekende fout')
    before_input = (response.get('errorKind') == 'pre_dispatch_guard'
                    and response.get('commandsSent') is False)
    code = response.get('code')
    if (before_input and response.get('retryable') is True
            and code in ('observation_stale', 'alignment_not_confirmed')):
        raise PreDispatchRejected(message, code=code)
    raise NativeCommandError(message, code=code,
                             commands_sent=False if before_input else None)


def _native_mix_guard_available(command):
    # Do not cache across a Bridge restart. This cheap status call replaces the
    # legacy screenshot only when the currently running native protocol proves
    # that it performs the same alignment guard and a stricter freshness check.
    response = request({'command':'status'})
    status = response.get('result', {}) if response.get('ok') is True else {}
    guards = status.get('nativeMixGuards') or {}
    return (type(status.get('protocolVersion')) is int and status['protocolVersion'] >= 3
            and guards.get('version') == 1
            and guards.get('freshAlignmentBeforeInput') is True
            and guards.get('typedPreDispatchRejections') is True
            and guards.get('expectedTracksAndDeadline') is True
            and (command != 'mixStep' or guards.get('mixStep') is True))


def checked(payload):
    command = payload.get('command')
    guarded = command in ('crossfader','fader','eqPair','mixStep')
    native_guard = _native_mix_guard_available(command) if guarded else False
    if guarded and not native_guard and (command == 'mixStep' or
            any(key in payload for key in ('expectedTracks','notAfterMonotonicNS'))):
        raise NativeCommandError('Bijgewerkte Bridge met lokale track- en deadlinecontrole vereist; geen bediening.',
                                 code='native_guard_unavailable', commands_sent=False)
    if command in ('crossfader','fader') and not native_guard:
        # Also protects legacy/demo clients while the native Bridge is being updated.
        import music_context
        from mix_safety import require_aligned
        raw = request({'command':'observe','saveImage':True})
        if not raw.get('ok'):
            _raise_native_error(raw)
        state = normalized_state(raw['result'])
        if not state.get('mixer'):
            state['mixer'] = music_context.read_frame()
        require_aligned(state)
    response = request(payload)
    if not response.get("ok"):
        _raise_native_error(response)
    return response["result"]

def normalized_state(raw):
    decks = []
    for deck in raw.get("decks", []):
        number = deck["deck"]
        bpm = re.search(r"\b(\d{2,3}\.\d+)\b", deck["displayedBPM"])
        times = re.findall(r"-?\d{2}:\d{2}\.\d", deck["metadata"])
        # The musical key follows the original BPM; artist names may contain A–G.
        key = re.search(r"\b\d{2,3}\.\d+\s+([A-G](?:b|#)?m?)\b", deck["metadata"])
        decks.append({"deck": number, "title": deck["title"].strip(" |\n"),
                      "bpm": float(bpm[1]) if bpm else None,
                      "key": key[1] if key else None,
                      "timeDisplays": times,
                      "playingIndicator": raw.get("playingIndicators", {}).get(f"deck{number}"),
                      "fader": raw.get("faders", {}).get(f"deck{number}")})
    state = {"source": "local screenshot and OCR", "observationMS": raw["observationMS"],
            "sampledAtMonotonicNS": raw["sampledAtMonotonicNS"],
            "folder": raw.get("browserHeading"), "decks": decks,
            "layoutCalibrated": raw["layoutCalibrated"]}
    if isinstance(raw.get('mixer'), dict):
        state['mixer'] = raw['mixer']
    return state

def observe():
    return normalized_state(checked({"command": "observe"}))

def deck_state(state, deck):
    if deck not in (1, 2) or not state.get("layoutCalibrated"):
        raise RuntimeError("Deck of vensterindeling is onbekend.")
    item = next((item for item in state["decks"] if item["deck"] == deck), None)
    if item is None:
        raise RuntimeError("Deck kon niet worden afgelezen.")
    return item

def wait_for(predicate, budget=3.0):
    deadline = time.monotonic() + budget
    while True:
        state = observe()
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError("Handeling niet bevestigd. Geen automatische herhaling verstuurd.")

def fader(deck, value):
    if deck not in (1, 2) or not 0 <= value <= 1:
        raise ValueError("Ongeldig deck of faderwaarde.")
    checked({"command": "fader", "deck": deck, "value": float(value)})
    return wait_for(lambda s: deck_state(s, deck)["fader"] is not None and
                    abs(deck_state(s, deck)["fader"]-value) < 0.08)

def playback(deck, play):
    state = observe()
    item = deck_state(state, deck)
    if item["playingIndicator"] is None:
        raise RuntimeError("Afspeelstand is onbekend.")
    if item["playingIndicator"] != play:
        checked({"command": "action", "action": f"deck{deck}.playPause", "expectedTrack": item["title"]})
        state = wait_for(lambda s: deck_state(s, deck)["title"] == item["title"] and
                         deck_state(s, deck)["playingIndicator"] == play)
    return state

def load(deck, filename, *, search=False):
    """Send once, then require the intended title in two successive observations."""
    inventory = json.loads((Path(__file__).resolve().parents[1] / "evidence/inventory.json").read_text())
    track = next((t for t in inventory["tracks"] if t["file"] == filename), None)
    if track is None or Path(filename).name != filename:
        raise ValueError("Bestand staat niet in de inventaris van map 26.")
    state = observe()
    item = deck_state(state, deck)
    if state["folder"] != "26" or item["playingIndicator"] is not False:
        raise RuntimeError("Open map 26 en gebruik een stilstaand doeldeck.")
    expected = track.get("title") or Path(filename).stem
    checked({"command": "loadTrack" if search else "loadVisible", "deck": deck, "file": filename})
    matches = 0
    def confirmed(state):
        nonlocal matches
        item = deck_state(state, deck)
        matches = matches + 1 if item["title"].casefold() == expected.casefold() else 0
        return matches >= 2
    return wait_for(confirmed, budget=5.0)

def same_tracks(before, after):
    return all(deck_state(before, d)["title"] == deck_state(after, d)["title"] for d in (1, 2))

def tempo_step(deck, direction):
    """One configured Rekordbox tempo step, followed by a fresh BPM confirmation."""
    if direction not in ("up", "down"):
        raise ValueError("Verwacht up of down.")
    before = observe()
    item = deck_state(before, deck)
    if item["bpm"] is None:
        raise RuntimeError("Tempo kon niet betrouwbaar worden gelezen.")
    checked({"command": "action", "action": f"deck{deck}.tempo{direction.title()}",
             "expectedTrack": item["title"]})
    def confirmed(after):
        bpm = deck_state(after, deck)["bpm"]
        if not same_tracks(before, after) or bpm is None:
            return False
        return bpm > item["bpm"] if direction == "up" else bpm < item["bpm"]
    return wait_for(confirmed)

def sync_tempo(deck):
    """Match the displayed BPM using Sync; this does not verify audio beat phase."""
    before = observe()
    target = deck_state(before, deck)
    reference = deck_state(before, 3-deck)
    if target["bpm"] is None or reference["bpm"] is None:
        raise RuntimeError("Beide tempi moeten betrouwbaar leesbaar zijn.")
    # Avoid toggling an existing sync state when the requested tempo already matches.
    if abs(target["bpm"] - reference["bpm"]) <= 0.01:
        return before
    checked({"command": "action", "action": f"deck{deck}.sync",
             "expectedTrack": target["title"]})
    def confirmed(after):
        a = deck_state(after, deck)["bpm"]
        b = deck_state(after, 3-deck)["bpm"]
        return (same_tracks(before, after) and a is not None and b is not None and
                abs(a - reference["bpm"]) <= 0.01 and abs(b - reference["bpm"]) <= 0.01)
    return wait_for(confirmed)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["state", "play", "pause", "fader", "load",
                                            "sync-tempo", "tempo-up", "tempo-down"])
    parser.add_argument("--deck", type=int, choices=[1, 2], default=1)
    parser.add_argument("--value", type=float)
    parser.add_argument("--file")
    args = parser.parse_args()
    try:
        if args.command == "state":
            result = observe()
        elif args.command == "sync-tempo":
            result = sync_tempo(args.deck)
        elif args.command.startswith("tempo-"):
            result = tempo_step(args.deck, args.command.removeprefix("tempo-"))
        elif args.command == "load":
            if not args.file:
                parser.error("--file is verplicht")
            result = load(args.deck, args.file)
        elif args.command == "fader":
            if args.value is None or not 0 <= args.value <= 1:
                parser.error("--value moet tussen 0 en 1 liggen")
            result = fader(args.deck, args.value)
        else:
            result = playback(args.deck, args.command == "play")
        print(json.dumps({"ok": True, "state": result}, ensure_ascii=False, indent=2))
    except (RuntimeError, OSError, KeyError, IndexError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
