"""Native worker isolation checks with mocked sockets/processes only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import dj_transport as transport


def status(compatible=True, worker=False):
    return {'ok': True, 'result': {'protocolVersion': 3,
            'nativeMixGuards': {'mixGesture': compatible},
            'contextualTransport': {'replaceStopped': compatible},
            'controlWorker': worker}}


class DJTransportTests(unittest.TestCase):
    def environment(self, directory):
        root = Path(directory)/'runtime'; root.mkdir()
        socket_dir = Path(directory)/'socket'; socket_dir.mkdir()
        executable = root/'Rekordbox Bridge.app/Contents/MacOS/rekordbox-bridge'
        executable.parent.mkdir(parents=True); executable.write_text('fixture only')
        return root, socket_dir

    def test_compatible_primary_is_reused_without_spawn_or_credential_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            calls = []
            def exchange(path, payload):
                calls.append((path, deepcopy(payload)))
                return status() if payload['command'] == 'status' else {'ok': True, 'result': {}}
            with patch.object(transport, 'exchange', side_effect=exchange), patch.object(transport.subprocess, 'Popen') as popen:
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                native({'command': 'observe', 'fast': True})
                native.close()
                popen.assert_not_called()
            self.assertEqual([p['command'] for _, p in calls], ['status', 'observe'])
            self.assertTrue(all(path == sockets/'control.sock' for path, _ in calls))
            self.assertTrue(native.cancel.exists())

    def test_attached_existing_worker_is_not_quit_when_transport_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            worker_path = sockets/'control-worker.sock'; worker_path.touch()
            calls = []
            def exchange(path, payload):
                calls.append((path, payload['command']))
                return status(compatible=path == worker_path, worker=path == worker_path)
            with patch.object(transport, 'exchange', side_effect=exchange), patch.object(transport.subprocess, 'Popen') as popen:
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                native({'command': 'observe'})
                native.close()
                popen.assert_not_called()
            self.assertEqual(calls, [(sockets/'control.sock', 'status'), (worker_path, 'status'), (worker_path, 'observe')])

    def test_spawned_control_worker_receives_no_api_key_and_primary_host_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            worker_path = sockets/'control-worker.sock'
            process = Mock(); process.poll.return_value = None
            calls = []
            def spawn(*args, **kwargs):
                worker_path.touch()
                return process
            def exchange(path, payload):
                calls.append((path, deepcopy(payload)))
                return status(compatible=path == worker_path, worker=path == worker_path)
            with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'do-not-copy', 'ANOTHER_API_KEY': 'also-secret', 'SAFE_SETTING': 'yes'}), \
                 patch.object(transport.subprocess, 'Popen', side_effect=spawn) as popen, \
                 patch.object(transport, 'exchange', side_effect=exchange):
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                native({'command': 'observe'})
                native.close()
                args, kwargs = popen.call_args
            self.assertEqual(args[0][1:], ['--control-worker'])
            self.assertNotIn('TYPESAFE_API_KEY', kwargs['env'])
            self.assertNotIn('ANOTHER_API_KEY', kwargs['env'])
            self.assertEqual(kwargs['env']['SAFE_SETTING'], 'yes')
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertNotIn('do-not-copy', repr((args, kwargs)))
            self.assertNotIn('also-secret', repr((args, kwargs)))
            self.assertEqual([(path, p['command']) for path, p in calls if path == sockets/'control.sock'],
                             [(sockets/'control.sock', 'status')])
            self.assertEqual(calls[-1], (worker_path, {'command': 'quit'}))
            process.kill.assert_not_called(); process.terminate.assert_not_called()

    def test_credential_and_session_commands_are_rejected_before_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            with patch.object(transport, 'exchange') as exchange, patch.object(transport.subprocess, 'Popen') as popen:
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                for command in ('djReady', 'djAuthorize', 'djStart', 'djStop'):
                    with self.assertRaises(ValueError):
                        native({'command': command})
                exchange.assert_not_called(); popen.assert_not_called()

    def test_failed_physical_command_is_not_replayed_on_another_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            with patch.object(transport, 'exchange', side_effect=[status(), RuntimeError('Reply lost')]) as exchange:
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                with self.assertRaises(RuntimeError):
                    native({'command': 'mixGesture', 'bassPixels': 2})
                self.assertEqual(exchange.call_count, 2)  # one status, one mutation
                self.assertEqual(exchange.call_args_list[-1].args[1]['command'], 'mixGesture')

    def test_worker_start_failure_does_not_restart_or_stop_credential_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root, sockets = self.environment(directory)
            process = Mock(); process.poll.return_value = 1
            with patch.object(transport, 'exchange', return_value=status(False)) as exchange, \
                 patch.object(transport.subprocess, 'Popen', return_value=process) as popen:
                native = transport.NativeTransport(root=root, socket_dir=sockets)
                with self.assertRaises(RuntimeError):
                    native({'command': 'observe'})
                self.assertEqual(exchange.call_count, 1)
                self.assertEqual(exchange.call_args.args[1], {'command': 'status'})
                self.assertEqual(popen.call_args.args[0][1:], ['--control-worker'])
                process.kill.assert_not_called(); process.terminate.assert_not_called()

    def test_exchange_adds_client_identity_and_does_not_retry_incomplete_reply(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.recv.side_effect = [b'']
        with patch.object(transport.socket, 'socket', return_value=connection) as socket:
            with self.assertRaises(RuntimeError):
                transport.exchange('/fixture/control.sock', {'command': 'setPlayback', 'deck': 1, 'playing': True})
            socket.assert_called_once()
            connection.sendall.assert_called_once()
        sent = json.loads(connection.sendall.call_args.args[0])
        self.assertEqual(sent['clientPID'], os.getpid())
        self.assertEqual(sent['command'], 'setPlayback')


if __name__ == '__main__':
    unittest.main()
