"""Verify the deployed source and both native bundles before any session input."""
import hashlib
import json
from pathlib import Path
import plistlib

PROTOCOL = 3
MODE = 'doom_demo'


def source_files(root):
    paths = []
    for pattern in ('Sources/*.swift', 'demo/djjev/*.py', 'scripts/*.py', 'config/*.json'):
        paths.extend(Path(root).glob(pattern))
    return sorted(paths)


def source_digest(root, files=None):
    root = Path(root)
    digest = hashlib.sha256()
    for path in (source_files(root) if files is None else [root/name for name in files]):
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def validate_installation(root):
    """Require matching source, configuration and compiled-bundle identities."""
    root = Path(root)
    config = json.loads((root/'config/live_trial.json').read_text())
    if config.get('implementation') != MODE:
        raise RuntimeError('Alleen de actieve doom_demo-kern mag een set starten.')
    manifest = json.loads((root/'build-info.json').read_text())
    if (manifest.get('protocol') != PROTOCOL or manifest.get('mode') != MODE
            or not isinstance(manifest.get('files'), list)
            or any(not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts
                   for name in manifest['files'])
            or manifest.get('source_digest') != source_digest(root, manifest['files'])):
        raise RuntimeError('Broncode en build verschillen; bouw beide apps opnieuw.')
    for name in ('Rekordbox Bridge.app', 'DJ Jev.app'):
        info = plistlib.loads((root/name/'Contents/Info.plist').read_bytes())
        if (info.get('JevSourceDigest') != manifest['source_digest']
                or info.get('JevSourceCommit') != manifest.get('commit')
                or info.get('JevProtocolVersion') != PROTOCOL):
            raise RuntimeError(f'{name} hoort niet bij deze broncode; bouw beide apps opnieuw.')
    return manifest
