"""Native control worker separate from the already-authorized credential host.

Updating controls does not restart the Bridge which holds the API credential.
The worker receives no key, and cannot service credential/session commands.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SOCKET_DIR = Path(f'/private/tmp/rekordbox-bridge-{os.getuid()}')


def exchange(path, payload):
    payload = {**payload, 'clientPID': os.getpid()}
    timeout = 60 if payload.get('command') in ('loadChosenTrack', 'loadTrack') else 20
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(json.dumps(payload, allow_nan=False).encode()+b'\n')
        content = bytearray()
        while b'\n' not in content:
            part = connection.recv(65536)
            if not part:
                raise RuntimeError('Lokale bediening gaf geen volledig antwoord; handeling niet herhaald.')
            content.extend(part)
            if len(content) > 4_000_000:
                raise RuntimeError('Lokale antwoordlimiet overschreden.')
        return json.loads(content.split(b'\n', 1)[0])


class NativeTransport:
    def __init__(self, *, root=ROOT, socket_dir=SOCKET_DIR):
        self.root, self.socket_dir = Path(root), Path(socket_dir)
        self.path = None
        self.worker = None
        self.cancel = self.socket_dir/f'stop-{os.getpid()}'
        self.cancel.unlink(missing_ok=True)

    @staticmethod
    def compatible(reply):
        return (reply.get('ok') is True
                and reply.get('result', {}).get('nativeMixGuards', {}).get('mixGesture') is True
                and reply.get('result', {}).get('contextualTransport', {}).get('replaceStopped') is True)

    def connect(self):
        primary = self.socket_dir/'control.sock'
        host = exchange(primary, {'command': 'status'})
        if self.compatible(host):
            self.path = primary
            return
        worker_path = self.socket_dir/'control-worker.sock'
        if worker_path.exists():
            try:
                response = exchange(worker_path, {'command': 'status'})
                if self.compatible(response) and response['result'].get('controlWorker') is True:
                    self.path = worker_path
                    return
            except (OSError, ValueError, RuntimeError):
                pass
        executable = self.root/'Rekordbox Bridge.app/Contents/MacOS/rekordbox-bridge'
        if not executable.is_file():
            raise RuntimeError('Bijgewerkte lokale bediening ontbreekt.')
        # The existing host keeps its in-memory key. Neither command arguments,
        # environment nor worker stdin contain a provider credential.
        environment = {k: v for k, v in os.environ.items()
                       if not ('TYPESAFE' in k.upper() or k.upper().endswith('API_KEY'))}
        self.worker = subprocess.Popen([str(executable), '--control-worker'],
            cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        until = time.monotonic()+8
        while time.monotonic() < until:
            if self.worker.poll() is not None:
                raise RuntimeError('Bijgewerkte lokale bediening kon niet starten.')
            if worker_path.exists():
                try:
                    response = exchange(worker_path, {'command': 'status'})
                    if self.compatible(response) and response['result'].get('controlWorker') is True:
                        self.path = worker_path
                        return
                except (OSError, ValueError, RuntimeError):
                    pass
            time.sleep(.1)
        raise RuntimeError('Lokale bediening is niet tijdig bereikbaar; bestaande sleutelhost blijft draaien.')

    def __call__(self, payload):
        if payload.get('command') in ('djReady', 'djAuthorize', 'djStart', 'djStop'):
            raise ValueError('Sleutel- en sessietoegang behoort uitsluitend bij de bestaande host.')
        if self.path is None:
            self.connect()
        return exchange(self.path, payload)

    def close(self):
        # Native gestures check this before their next bounded pointer input.
        try:
            self.cancel.touch(mode=0o600, exist_ok=True)
        except FileNotFoundError:
            # No native server directory means there is no gesture to cancel.
            pass
        if self.worker is not None and self.worker.poll() is None:
            try:
                exchange(self.socket_dir/'control-worker.sock', {'command': 'quit'})
            except (OSError, ValueError, RuntimeError):
                pass
            # Never kill a process during a mouse-down. Native cancellation
            # releases its pointer and quits through its ordinary event loop.
        self.path = None
