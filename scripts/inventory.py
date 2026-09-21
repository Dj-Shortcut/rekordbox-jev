#!/usr/bin/env python3
"""Read local mappings and ID3 metadata only; never open or modify the Rekordbox DB."""
import json
import plistlib
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MUSIC = Path.home() / "Music/Music/26"
SETTINGS = Path.home() / "Library/Application Support/Pioneer/rekordbox6"

def synchsafe(data):
    if len(data) != 4 or any(value & 128 for value in data):
        raise ValueError("Invalid ID3 size")
    return sum(value << shift for value, shift in zip(data, [21, 14, 7, 0]))

def text_frame(data):
    if not data or data[0] not in range(4):
        return None
    encoding = ["latin-1", "utf-16", "utf-16-be", "utf-8"][data[0]]
    return data[1:].decode(encoding, errors="replace").rstrip("\x00")

def id3(path):
    result = {}
    with path.open("rb") as file:
        header = file.read(10)
        if len(header) != 10 or header[:3] != b"ID3" or header[3] not in (3, 4):
            return result
        # Do not misread unsynchronised or extended-header tags.
        if header[5] & 0xC0:
            return result
        size = synchsafe(header[6:10])
        if size > 16_000_000:
            return result
        data = file.read(size)
    offset = 0
    names = {"TIT2": "title", "TPE1": "artist", "TBPM": "bpmTag", "TKEY": "keyTag"}
    while offset + 10 <= len(data):
        tag = data[offset:offset + 4].decode("ascii", errors="replace")
        if not tag.strip("\x00"):
            break
        frame_size = (synchsafe(data[offset+4:offset+8]) if header[3] == 4
                      else struct.unpack(">I", data[offset+4:offset+8])[0])
        end = offset + 10 + frame_size
        if frame_size <= 0 or end > len(data):
            break
        flags = data[offset+9]
        if tag in names and flags == 0:
            value = text_frame(data[offset+10:end])
            if value:
                result[names[tag]] = value
        offset = end
    return result

def inventory():
    root = MUSIC.resolve(strict=True)
    tracks = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() != ".mp3" or not path.is_file():
            continue
        if path.resolve().parent != root:
            continue
        tracks.append({"file": path.name, **id3(path)})
    mapping = SETTINGS / "KeyMappings/rekordbox_0000000000000.mappings"
    keys = [dict(element.attrib) for element in ET.parse(mapping).iter("MAPPING")]
    with Path("/Applications/rekordbox 6/rekordbox.app/Contents/Info.plist").open("rb") as file:
        version = plistlib.load(file)["CFBundleShortVersionString"]
    return {"rekordboxVersion": version, "allowedMusicFolder": str(root), "tracks": tracks,
            "keyboardMappings": keys,
            "notes": ["ID3 BPM/key tags are not live deck values or verified beatgrids.",
                      "Missing metadata remains missing; no guessed keys or tempos.",
                      "This is a saved mapping inventory, not proof that every mapping works."]}

if __name__ == "__main__":
    data = inventory()
    destination = PROJECT / "evidence/inventory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"tracks": len(data["tracks"]), "mappings": len(data["keyboardMappings"]),
                      "bpmTags": sum("bpmTag" in t for t in data["tracks"]),
                      "keyTags": sum("keyTag" in t for t in data["tracks"]),
                      "output": str(destination)}, indent=2))
