#!/usr/bin/env python3
"""Stamp native bundles from the same checkout; sign only after stamping."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'demo'))
from djjev.build_info import source_digest, source_files, PROTOCOL, MODE


def stamp(root, bundle):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    manifest = {'files':[p.relative_to(root).as_posix() for p in source_files(root)], 'commit': commit, 'source_digest': source_digest(root), 'protocol': PROTOCOL, 'mode': MODE}
    path = root/'build-info.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, indent=2)+'\n')
    temporary.replace(path)
    info_path = bundle/'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info.update(JevSourceCommit=commit, JevSourceDigest=manifest['source_digest'], JevProtocolVersion=PROTOCOL)
    info_path.write_bytes(plistlib.dumps(info))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    print(json.dumps(stamp(ROOT, args.bundle)))
