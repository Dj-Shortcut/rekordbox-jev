"""Measure the real read-only native path; never loads, plays or moves controls."""
import asyncio
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.environment import Native, ROOT


async def main():
    native = Native()
    for role in ('observer', 'control'):
        try:
            status = await native.call(role, 'status')
            if status.get('demoRole') == role:
                await native.call(role, 'quit')
        except (OSError, RuntimeError, ValueError):
            pass
    await asyncio.sleep(.4)
    results = []
    try:
        await native.start()
        for fast in (True, False, True, False):
            calls = await asyncio.gather(native.call('observer', 'observe', fast=True),
                                        native.call('control', 'observe', fast=fast))
            for role, frame in zip(('observer', 'control'), calls):
                item = {'role': role, 'fast': True if role == 'observer' else fast,
                        'calibrated': frame.get('layoutCalibrated'),
                        'observationMS': frame.get('observationMS'),
                        'ageMS': (time.monotonic_ns()-frame['sampledAtMonotonicNS'])/1e6,
                        'timing': frame.get('timing'), 'decks': frame.get('decks')}
                results.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
        path = ROOT/'evidence'/'native-read-preflight.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    finally:
        await native.close()


if __name__ == '__main__': asyncio.run(main())
