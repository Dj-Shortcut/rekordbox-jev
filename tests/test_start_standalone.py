import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import start_standalone as launcher


class StandaloneLauncher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('Rekordbox Bridge.app', 'DJ Jev.app', 'config', 'evidence'):
            (self.root / name).mkdir()
        (self.root / 'config/live_trial.json').write_text('{"implementation":"continuous_session"}')
        (self.root / 'evidence/inventory.json').write_text(json.dumps({'tracks': [
            {'file': 'A.mp3', 'title': 'A'}, {'file': 'B.mp3', 'title': 'B'}]}))
        self.status = {'protocolVersion': 3, 'bridgePID': 123, 'autonomousMixing': False,
                       'accessibility': True, 'screenRecording': True, 'rekordboxRunning': True}
        self.raw = {'layoutCalibrated': True, 'browserHeading': '26',
                    'decks': [{'deck': 1, 'title': 'A'}, {'deck': 2, 'title': 'B'}],
                    'playingIndicators': {'deck1': False, 'deck2': False},
                    'mixer': {'deck_assignments': {'1': 'left', '2': 'right'}}}
        self.commands = []
        self.run = Mock()

    def native(self, payload):
        command = payload['command']
        self.commands.append(command)
        responses = {'status': self.status, 'observe': self.raw,
                     'djReady': {'credentialAvailable': True}, 'djStart': {'started': True}}
        return {'ok': True, 'result': responses[command]}

    def launch(self, **overrides):
        options = {'native': self.native, 'run': self.run, 'check_codex': lambda: False}
        options.update(overrides)
        return launcher.start(self.root, **options)

    def test_one_start_with_saved_native_credential_and_manifest(self):
        path = self.launch()
        manifest = json.loads(path.read_text())
        self.assertEqual(self.commands, ['status', 'observe', 'djReady', 'status', 'djStart'])
        self.assertEqual(manifest['status'], 'start_accepted')
        self.assertFalse(manifest['codex_gui_running'])
        self.assertEqual(manifest['bridge_protocol_version'], 3)
        self.assertIn('config/live_trial.json', manifest['sha256'])
        self.assertEqual([c.args[0] for c in self.run.call_args_list], [
            ['/usr/bin/open', '-g', str(self.root / 'Rekordbox Bridge.app')],
            ['/usr/bin/open', '-g', str(self.root / 'DJ Jev.app')]])
        self.assertNotIn('djAuthorize', self.commands)

    def test_codex_open_does_not_open_apps_or_contact_bridge(self):
        with self.assertRaisesRegex(launcher.LaunchError, '⌘Q'):
            self.launch(check_codex=lambda: True)
        self.assertEqual(self.commands, [])
        self.run.assert_not_called()

    def test_existing_session_is_never_stopped_or_restarted(self):
        self.status['autonomousMixing'] = True
        with self.assertRaisesRegex(launcher.LaunchError, 'draait al'):
            self.launch()
        self.assertEqual(self.commands, ['status'])
        self.run.assert_not_called()

    def test_old_bridge_and_empty_deck_fail_before_readiness_or_start(self):
        self.status['protocolVersion'] = 2
        with self.assertRaisesRegex(launcher.LaunchError, 'oudere Bridge'):
            self.launch()
        self.assertEqual(self.commands, ['status'])
        self.commands.clear()
        self.status['protocolVersion'] = 3
        self.raw['decks'][1]['title'] = ''
        with self.assertRaisesRegex(launcher.LaunchError, 'beide decks'):
            self.launch()
        self.assertEqual(self.commands, ['status', 'observe'])

    def test_keychain_timeout_is_not_retried_and_never_starts(self):
        def native(payload):
            if payload['command'] == 'djReady':
                self.commands.append('djReady')
                raise TimeoutError('timed out')
            return self.native(payload)
        with self.assertRaises(TimeoutError):
            self.launch(native=native)
        self.assertEqual(self.commands, ['status', 'observe', 'djReady'])

    def test_start_timeout_is_unconfirmed_and_never_retried(self):
        def native(payload):
            if payload['command'] == 'djStart':
                self.commands.append('djStart')
                raise TimeoutError('reply lost after dispatch')
            return self.native(payload)
        with self.assertRaisesRegex(launcher.StartUnconfirmed, 'kan al draaien'):
            self.launch(native=native)
        self.assertEqual(self.commands.count('djStart'), 1)
        manifests = list((self.root / 'evidence/standalone-runs').glob('*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        self.assertEqual(json.loads(manifests[0].read_text())['status'], 'start_unconfirmed')

    def test_process_check_uses_bundle_id_not_gui_executable_name(self):
        app = self.root / 'ChatGPT.app/Contents'
        app.mkdir(parents=True)
        (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.openai.codex'}))
        run = Mock(return_value=Mock(stdout=str(app / 'MacOS/ChatGPT') + '\n'))
        self.assertTrue(launcher.codex_running(run))
        run.assert_called_once_with(['/bin/ps', '-axo', 'comm='], check=True,
                                    capture_output=True, text=True, timeout=5)

    def test_hidden_or_unreadable_process_list_is_not_treated_as_closed(self):
        for run in (Mock(return_value=Mock(stdout='')),
                    Mock(side_effect=subprocess.CalledProcessError(1, '/bin/ps'))):
            with self.assertRaisesRegex(launcher.LaunchError, 'Finder'):
                launcher.codex_running(run)


if __name__ == '__main__':
    unittest.main()
