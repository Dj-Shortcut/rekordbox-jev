#!/usr/bin/env python3
"""Keep the app's designated requirement stable across rebuilds; no ad-hoc fallback."""
import subprocess
import sys
from setup_signing import identities

if len(sys.argv)!=3:
    raise SystemExit('Gebruik: sign_app.py APP_PATH BUNDLE_ID')
found=identities()
if len(found)!=1:
    raise SystemExit('Vaste ondertekening ontbreekt. Voer eenmalig scripts/setup_signing.py uit; er wordt niet ad hoc ondertekend.')
identity=next(iter(found))
bundle_id=sys.argv[2]
if bundle_id not in ('local.rekordbox.bridge','local.rekordbox.jev-widget'):
    raise SystemExit('Onbekende app-identiteit.')
requirement=f'designated => identifier "{bundle_id}" and certificate leaf = H"{identity}"'
subprocess.run(['/usr/bin/codesign','--force','--sign',identity,'--timestamp=none',
                '--identifier',bundle_id,'--requirements','='+requirement,sys.argv[1]],check=True)
subprocess.run(['/usr/bin/codesign','--verify','--strict',sys.argv[1]],check=True)
print('Ondertekend met vaste certificaatgebonden app-identiteit.')
