#!/usr/bin/env python3
"""Start the signed apps without Codex or handling an API credential here."""
import errno
import hashlib
import json
import math
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

from bridge import request

ROOT = Path(__file__).resolve().parents[1]
CODEX_BUNDLE_ID = 'com.openai.codex'
HASH_FILES = ('scripts/start_standalone.py', 'scripts/dj_session.py',
              'scripts/controller.py', 'scripts/mix_safety.py',
              'scripts/playground_live.py', 'scripts/jev_reactive.py',
              'scripts/bridge.py', 'scripts/music_context.py', 'scripts/jev_monitor.py',
              'config/live_trial.json', 'config/mixing_guidance.json',
              'Rekordbox Bridge.app/Contents/MacOS/rekordbox-bridge',
              'DJ Jev.app/Contents/MacOS/jev-widget')


class LaunchError(RuntimeError):
    pass


class StartUnconfirmed(LaunchError):
    pass


def codex_running(run=subprocess.run):
    # A sandbox-hidden NSRunningApplication list can falsely report "closed".
    # ps failure is therefore a refusal, never evidence that Codex is absent.
    try:
        processes = run(['/bin/ps', '-axo', 'comm='], check=True,
                        capture_output=True, text=True, timeout=5).stdout
        if not processes.strip():
            raise ValueError('empty process list')
        for executable in processes.splitlines():
            app, separator, binary = executable.strip().rpartition('.app/Contents/MacOS/')
            if not separator:
                continue
            try:
                metadata = plistlib.loads(Path(app + '.app/Contents/Info.plist').read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                if binary in ('Codex', 'ChatGPT'):
                    raise LaunchError('Codex kon niet worden gecontroleerd. Start deze knop rechtstreeks vanuit Finder.')
                continue
            if metadata.get('CFBundleIdentifier') == CODEX_BUNDLE_ID:
                return True
        return False
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise LaunchError('Codex kon niet worden gecontroleerd. Start deze knop rechtstreeks vanuit Finder.') from error


def require_codex_closed(check):
    if check():
        raise LaunchError('Sluit Codex met ⌘Q en dubbelklik daarna opnieuw op Start Jev-proef.command.')


def native_call(native, command):
    response = native({'command': command})
    if response.get('ok') is not True or not isinstance(response.get('result'), dict):
        raise LaunchError(response.get('error', 'De lokale Bridge gaf geen geldig antwoord.'))
    return response['result']


def validate_initial_state(raw, inventory):
    if raw.get('layoutCalibrated') is not True or raw.get('browserHeading') != '26':
        raise LaunchError('Open map 26 in Rekordbox, met de indeling 2Deck Horizontal.')
    decks = raw.get('decks', [])
    if len(decks) != 2 or {d.get('deck') for d in decks} != {1, 2}:
        raise LaunchError('Laad eerst op beide decks een nummer uit map 26.')
    tracks = inventory.get('tracks', [])
    for deck in decks:
        title = deck.get('title', '').strip(' |\n').casefold()
        matches = [track for track in tracks if title and title in (
            str(track.get('title', '')).casefold(), Path(track.get('file', '')).stem.casefold())]
        if len(matches) != 1:
            raise LaunchError('Laad eerst op beide decks een herkenbaar nummer uit map 26.')
    indicators = raw.get('playingIndicators', {})
    playing = [indicators.get('deck' + str(deck)) for deck in (1, 2)]
    if any(type(value) is not bool for value in playing) or all(playing):
        raise LaunchError('Er is geen vrij deck: pauzeer beide decks of laat slechts één deck hoorbaar spelen.')
    mixer = raw.get('mixer') or {}
    if mixer.get('deck_assignments') != {'1': 'left', '2': 'right'}:
        raise LaunchError('De crossfadertoewijzing moet deck 1 links en deck 2 rechts zijn.')
    if any(playing):
        position = mixer.get('crossfader_position')
        active = playing.index(True)
        if type(position) not in (int, float) or not math.isfinite(position) or abs(position-active) >= .04:
            raise LaunchError('Zet de crossfader helemaal naar het spelende deck, of pauzeer beide decks.')
        if any(type(raw.get('faders', {}).get('deck' + str(deck))) not in (int, float)
               or not math.isfinite(raw['faders']['deck' + str(deck)])
               or raw['faders']['deck' + str(deck)] < .9 for deck in (1, 2)):
            raise LaunchError('Zet beide kanaalfaders bovenaan vóór deze proef, of pauzeer beide decks.')


def start(root=ROOT, *, native=request, run=subprocess.run, check_codex=codex_running,
          sleep=time.sleep, clock=time.monotonic):
    require_codex_closed(check_codex)
    for relative in ('Rekordbox Bridge.app', 'DJ Jev.app'):
        if not (root / relative).is_dir():
            raise LaunchError('De app ' + relative + ' ontbreekt naast de startknop.')
    config = json.loads((root / 'config/live_trial.json').read_text())
    if config.get('implementation') != 'continuous_session':
        raise LaunchError('De doorlopende DJ-sessie is niet ingesteld; er is niets gestart.')
    try:
        status = native_call(native, 'status')
    except OSError as error:
        if error.errno not in (errno.ENOENT, errno.ECONNREFUSED):
            raise
        status = None
    if status and status.get('autonomousMixing') is True:
        raise LaunchError('DJ Jev draait al. De bestaande sessie blijft ongemoeid.')
    run(['/usr/bin/open', '-g', str(root / 'Rekordbox Bridge.app')], check=True)
    deadline = clock() + 8
    while status is None:
        try:
            status = native_call(native, 'status')
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ECONNREFUSED) or clock() >= deadline:
                raise LaunchError('De Bridge werd niet bereikbaar; er is geen DJ-sessie gestart.') from error
            sleep(.2)
    if status.get('autonomousMixing') is True:
        raise LaunchError('DJ Jev draait al. De bestaande sessie blijft ongemoeid.')
    if type(status.get('protocolVersion')) is not int or status['protocolVersion'] < 3:
        raise LaunchError('Er draait nog een oudere Bridge. De nieuwe versie moet eerst worden geopend; er is niets gestart.')
    if not status.get('accessibility') or not status.get('screenRecording'):
        raise LaunchError('De bestaande macOS-rechten van de Bridge zijn niet beschikbaar. Er is niets gestart.')
    if status.get('rekordboxRunning') is not True:
        raise LaunchError('Open eerst Rekordbox en laad op beide decks een nummer uit map 26.')
    raw = native_call(native, 'observe')
    inventory = json.loads((root / 'evidence/inventory.json').read_text())
    validate_initial_state(raw, inventory)
    # djReady is explicitly noninteractive; only the signed native Bridge reads
    # its already stored Keychain item. Never prompt for or copy a key here.
    if native_call(native, 'djReady').get('credentialAvailable') is not True:
        raise LaunchError('De bewaarde sleutel is niet beschikbaar; er is geen nieuwe sleutel gevraagd.')
    require_codex_closed(check_codex)
    if native_call(native, 'status').get('autonomousMixing') is True:
        raise LaunchError('DJ Jev draait al. De bestaande sessie blijft ongemoeid.')
    folder = root / 'evidence/standalone-runs' / str(uuid.uuid4())
    folder.mkdir(parents=True)
    manifest = {'started_at': time.time(), 'launcher_pid': os.getpid(),
                'codex_bundle_id': CODEX_BUNDLE_ID, 'codex_gui_running': False,
                'bridge_protocol_version': status['protocolVersion'], 'bridge_pid': status.get('bridgePID'),
                'hash_scope': 'Files on disk at launch; not proof of a completed musical transition.',
                'sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                           for name in HASH_FILES if (root / name).is_file()},
                'status': 'ready_to_start'}
    manifest_path = folder / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    start_requested = False
    try:
        run(['/usr/bin/open', '-g', str(root / 'DJ Jev.app')], check=True)
        manifest['status'] = 'start_requested'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        start_requested = True
        if native_call(native, 'djStart').get('started') is not True:
            raise LaunchError('De Bridge heeft de DJ-sessie niet gestart.')
        manifest['status'] = 'start_accepted'
    except Exception as error:
        manifest['status'] = 'start_unconfirmed' if start_requested else 'start_failed'
        if start_requested:
            raise StartUnconfirmed('Het startantwoord is niet bevestigd. DJ Jev kan al draaien; '
                                   'de start is niet herhaald. Kijk eerst naar Rekordbox.') from error
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest_path


def main():
    try:
        manifest = start()
        print('DJ Jev is gestart zonder Codex. Je kunt dit Terminal-venster sluiten.')
        print('De vragen en antwoorden verschijnen in DJ Jev.')
        print('Startbewijs: ' + str(manifest))
        return 0
    except (LaunchError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        prefix = 'Start niet bevestigd: ' if isinstance(error, StartUnconfirmed) else 'Niet gestart: '
        print(prefix + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
