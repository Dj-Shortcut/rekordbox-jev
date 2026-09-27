"""Exercise the host's pre-created directory and stdin launcher without DJ input."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import main
from djjev.build_info import source_digest


class HostStartupTests(unittest.TestCase):
    def test_host_directory_reaches_session_and_records_native_start_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = str(uuid.uuid4())
            directory = root/'demo/evidence'/run_id
            directory.mkdir(parents=True)  # DJSessionHost.start creates this first.
            (root/'config').mkdir()
            (root/'config/live_trial.json').write_text('{"implementation":"doom_demo"}')
            manifest = {'files':['config/live_trial.json'], 'commit':'fixture',
                        'source_digest':source_digest(root), 'protocol':3, 'mode':'doom_demo'}
            (root/'build-info.json').write_text(json.dumps(manifest))
            for app in ('Rekordbox Bridge.app', 'DJ Jev.app'):
                info = root/app/'Contents/Info.plist'
                info.parent.mkdir(parents=True)
                info.write_bytes(plistlib.dumps({'JevSourceDigest':manifest['source_digest'],
                    'JevSourceCommit':'fixture', 'JevProtocolVersion':3}))
            script = Path(__file__).resolve().parents[2]/'scripts/run_session.py'
            # Run the actual stdin entrypoint, build checks, Display, session and
            # result persistence. Only the native adapter is replaced: no sockets,
            # API requests, app activation or deck input are possible in this test.
            harness = '''
import runpy, sys
from pathlib import Path
entry = runpy.run_path(sys.argv[1])
from djjev import main
root = Path(sys.argv[2])
entry['main'].__globals__['ROOT'] = root
main.ROOT, main.BRIDGE, main.DISPLAY = root/'demo', root, root/'widget'
class UnavailableNative:
    def __init__(self, **kwargs): pass
    async def start(self): raise RuntimeError('fixture_native_unavailable')
    async def stop(self): pass
main.Rekordbox = UnavailableNative
sys.argv = [sys.argv[1], '--key-stdin']
raise SystemExit(entry['main']())
'''
            result = subprocess.run([sys.executable, '-c', harness, str(script), str(root)],
                input='private-fixture-key\n', text=True, capture_output=True, timeout=15,
                env={**os.environ, 'DJ_JEV_SESSION_ID':run_id})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output['error'], 'fixture_native_unavailable')
            self.assertEqual(output['run_id'], run_id)
            persisted = json.loads((directory/'result.json').read_text())
            self.assertTrue(persisted['completed'])
            self.assertTrue(persisted['blocked'])
            self.assertTrue((directory/'session.json').is_file())
            self.assertNotIn('private-fixture-key', result.stdout)

    def test_reused_session_id_does_not_overwrite_previous_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, run_id = Path(temporary), str(uuid.uuid4())
            directory = root/'evidence'/run_id
            directory.mkdir(parents=True)
            previous = directory/'result.json'
            previous.write_text('{"previous":"retain"}')
            with patch.object(main, 'ROOT', root), patch.dict(os.environ, DJ_JEV_SESSION_ID=run_id):
                with self.assertRaises(FileExistsError):
                    main.Display('fixture-key')
            self.assertEqual(previous.read_text(), '{"previous":"retain"}')


if __name__ == '__main__':
    unittest.main()
