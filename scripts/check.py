#!/usr/bin/env python3
"""Read-only smoke checks. Never send a successful Rekordbox action."""
import json
from pathlib import Path
from bridge import request

project = Path(__file__).resolve().parents[1]
checks = []
def check(name, passed):
    checks.append({"check": name, "passed": bool(passed)})
    if not passed:
        raise AssertionError(name)

status = request({"command": "status"})
check("Local transport returns status", status.get("ok"))
check("Folder scope is 26", status["result"]["allowedMusicFolder"] == str(Path.home()/"Music/Music/26"))
check("No autonomous-mixing claim", status["result"]["autonomousMixing"] is False)
capabilities = request({"command": "capabilities"})
check("Keyboard mappings parsed", len(capabilities["result"]["actions"]) == 20)
check("Saved mappings are not claimed as native proof",
      all(a["nativeTransportVerified"] is False for a in capabilities["result"]["actions"]))
check("Unknown command rejected", request({"command": "unknown"}).get("ok") is False)
check("Unknown action rejected", request({"command": "action", "action": "not-an-action"}).get("ok") is False)
check("Action without expected track rejected",
      request({"command": "action", "action": "deck1.playPause"}).get("ok") is False)
inventory = json.loads((project/"evidence/inventory.json").read_text())
check("Only MP3 filenames in inventory", all(Path(t["file"]).name == t["file"] and t["file"].lower().endswith(".mp3")
                                          for t in inventory["tracks"]))
output = {"checks": checks, "nativeStatus": status["result"],
          "note": "Read-only checks; no proof of live native playback, timing, or mixing."}
(project/"evidence/checks.json").write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps(output, indent=2))
