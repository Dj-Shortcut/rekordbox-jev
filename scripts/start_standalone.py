#!/usr/bin/env python3
"""Open the official DJ Jev app; its Start button owns the only session flow."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'demo'))


def start(root=ROOT, *, run=subprocess.run, validate=None):
    if validate is None:
        from djjev.build_info import validate_installation
        validate = validate_installation
    manifest = validate(root)
    run(['/usr/bin/open', str(Path(root)/'DJ Jev.app')], check=True)
    return manifest


def main():
    try:
        start()
        print('DJ Jev geopend. Klik op Start set; voorbereiding gebeurt automatisch.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Niet geopend: '+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
