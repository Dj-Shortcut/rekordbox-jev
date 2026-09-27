#!/usr/bin/env python3
"""Offline folder-26 timeline cache. Never starts Jev or Rekordbox controls."""
import argparse
import json
from pathlib import Path
import sys
from xml.etree.ElementTree import ParseError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from djjev.audio_analysis import analyze_file
from djjev.audio_timeline import DEFAULT_CACHE, load_cached, write_cache
from djjev.state import read_library


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--track-id', action='append', help='Folder-26 track ID; repeat to select several.')
    selection.add_argument('--all', action='store_true', help='Explicitly analyze every supported folder-26 MP3.')
    parser.add_argument('--export', type=Path, default=Path.home() / 'Desktop/rekordbox.xml')
    parser.add_argument('--music-root', type=Path, default=Path.home() / 'Music/Music/26')
    parser.add_argument('--cache-dir', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--force', action='store_true', help='Recompute even a current cache entry.')
    args = parser.parse_args(argv)
    try:
        library = read_library(args.export, args.music_root)
    except (OSError, ValueError, ParseError) as exc:
        parser.error(str(exc))
    chosen = set(args.track_id or [])
    unknown = chosen - {track['id'] for track in library}
    if unknown:
        parser.error('Unknown or out-of-scope folder-26 track IDs: ' + ', '.join(sorted(unknown)))
    tracks = [track for track in library if args.all or track['id'] in chosen]
    failures = 0
    for track in tracks:
        path = args.music_root.resolve() / track['file']
        try:
            cached = None if args.force else load_cached(track, path, args.cache_dir, verify_hash=True)
            if cached is not None:
                print(json.dumps({'track_id': track['id'], 'status': 'cached',
                                  'analysis_id': cached['analysis_id']}), flush=True)
                continue
            document = analyze_file(path, track)
            target = write_cache(document, args.cache_dir)
            print(json.dumps({'track_id': track['id'], 'status': 'analyzed',
                              'windows': len(document['windows']), 'cache': str(target),
                              'analysis_id': document['analysis_id']}), flush=True)
        except (OSError, ValueError, RuntimeError) as exc:
            failures += 1
            print(json.dumps({'track_id': track['id'], 'status': 'unavailable',
                              'reason': str(exc)}), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
