#!/usr/bin/env python3
"""Canonical credential-host entrypoint; no legacy control route or key prompt."""
import fcntl
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'demo'))


def main():
    from djjev.build_info import validate_installation
    validate_installation(ROOT)
    if sys.argv[1:] != ['--key-stdin']:
        raise RuntimeError('Start DJ Jev via de app; geen sleutel in argumenten of omgeving.')
    (ROOT/'evidence').mkdir(exist_ok=True)
    with (ROOT/'evidence/jev-reactive.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        key = sys.stdin.readline(4098).rstrip('\r\n')
        if not key or len(key) > 4096 or any(ord(c) < 32 for c in key):
            raise RuntimeError('De bestaande sleutel is niet beschikbaar.')
        from djjev.main import run
        result = run(key)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1 if result.get('blocked') else 0


if __name__ == '__main__':
    raise SystemExit(main())
