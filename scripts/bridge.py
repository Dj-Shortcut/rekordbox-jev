#!/usr/bin/env python3
"""Small client for the local Rekordbox bridge. No network or third-party libraries."""
import argparse
import json
import os
import socket
import sys

def request(payload):
    payload = {**payload, "clientPID": os.getpid()}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(60 if payload.get("command") in ("loadTrack", "loadChosenTrack", "djAuthorize") else 15)
        connection.connect(f"/private/tmp/rekordbox-bridge-{os.getuid()}/control.sock")
        connection.sendall(json.dumps(payload).encode() + b"\n")
        chunks = bytearray()
        while b"\n" not in chunks:
            data = connection.recv(65536)
            if not data:
                raise RuntimeError("De lokale module verbrak de verbinding.")
            chunks.extend(data)
            if len(chunks) > 4_000_000:
                raise RuntimeError("Antwoord is te groot.")
        return json.loads(chunks.split(b"\n", 1)[0])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "observe", "capabilities", "action"])
    parser.add_argument("action", nargs="?")
    parser.add_argument("--expected-track")
    args = parser.parse_args()
    payload = {"command": args.command}
    if args.command == "action":
        if not args.action or not args.expected_track:
            parser.error("action vereist een actienaam en --expected-track")
        payload.update(action=args.action, expectedTrack=args.expected_track)
    try:
        result = request(payload)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1

if __name__ == "__main__":
    sys.exit(main())
