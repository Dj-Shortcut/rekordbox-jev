"""Crash-readable session checkpoints and append-only diagnostic evidence."""
import json
import os
from pathlib import Path
import time


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class SessionEvidence:
    def __init__(self, directory, run_id, key, build=None):
        self.directory, self.key = Path(directory), key
        self.directory.mkdir(parents=True, exist_ok=True)
        self.failed = False
        self.state = {'run_id': run_id, 'runner_pid': os.getpid(), 'started_at': time.time(),
                      'status': 'preparing', 'build': build or {}, 'last_observation': None,
                      'last_error': None, 'last_request': None, 'last_command': None, 'processes': {}}
        for name in ('events.jsonl', 'bridge-status.jsonl', 'native-commands.jsonl', 'health.jsonl'):
            (self.directory/name).touch(exist_ok=True)
        self.heartbeat()
        atomic(self.directory/'result.json', {'run_id': run_id, 'status': 'incomplete',
                                            'completed': False, 'runner_pid': os.getpid()})

    def clean(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[verborgen]') if self.key else value
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return value

    def heartbeat(self):
        self.state['heartbeat_at'] = time.time()
        try:
            atomic(self.directory/'session.json', self.clean(self.state))
        except OSError:
            self.failed = True
            raise

    def record(self, event):
        event = self.clean(event)
        name = event['event']
        targets = ['events.jsonl']
        if name == 'bridge_status':
            targets.append('bridge-status.jsonl')
            self.state['processes'][event['role']] = event['status']
        elif name in ('health', 'error', 'execution_reconciling', 'execution_reconciled'):
            targets.append('health.jsonl')
        elif name == 'native_command':
            targets.append('native-commands.jsonl')
            # No answer yet explicitly means unknown, never no input.
            summary = event.get('result', {})
            self.state['last_command'] = {**event, 'input_sent': summary.get('commandsSent', summary.get('dispatched', event.get('flags', {}).get('commands_sent'))),
                'partial': summary.get('partial'),
                'possibly_partial': summary.get('verified') is not True,
                'verified': summary.get('verified') is True}
        if name == 'snapshot' and event.get('valid') is True:
            self.state['last_observation'] = {'time': event.get('time', time.time()),
                'captured_ns': event['snapshot'].get('captured_ns'), 'sequence': event.get('snapshot_seq')}
        if name == 'request':
            self.state['last_request'] = {'id': event['request_id'], 'time': event.get('time', time.time())}
        if name == 'error' and self.state['status'] != 'blocked':
            self.state['last_error'] = {k: event.get(k) for k in ('stage', 'reason', 'error_type', 'message')}
            if event.get('blocked') is True:
                self.state['status'] = 'blocked'
        if name == 'started':
            self.state['status'] = 'running'
        try:
            for target in targets:
                with (self.directory/target).open('a') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
                    # Persist command intent before native input can occur.
                    if name == 'native_command':
                        stream.flush()
                        os.fsync(stream.fileno())
            if name != 'snapshot':
                self.heartbeat()
        except OSError:
            self.failed = True
            raise

    def finish(self, result):
        self.state['status'] = 'blocked' if result.get('blocked') else 'stopped'
        self.heartbeat()
        atomic(self.directory/'result.json', self.clean({**result, 'completed': True,
                                                        'status': self.state['status']}))
