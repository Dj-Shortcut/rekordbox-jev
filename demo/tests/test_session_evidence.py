"""Persisted files distinguish incomplete/crashed sessions from successful output."""
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from djjev.evidence import SessionEvidence
from djjev.build_info import source_digest, validate_installation
import plistlib


class EvidenceTests(unittest.TestCase):
    def test_initial_result_is_incomplete_and_native_intent_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = SessionEvidence(root, 'fixture', 'private-key')
            self.assertFalse(json.loads((root/'result.json').read_text())['completed'])
            evidence.record({'event':'native_command','phase':'started','command':'mixGesture','command_id':1})
            checkpoint = json.loads((root/'session.json').read_text())
            self.assertIsNone(checkpoint['last_command']['input_sent'])
            self.assertFalse(checkpoint['last_command']['verified'])
            evidence.record({'event':'native_command','phase':'error','command':'mixGesture',
                             'message':'private-key','flags':{'commands_sent':True}})
            evidence.record({'event':'bridge_status','role':'observer','status':{'bridgePID':42}})
            evidence.record({'event':'error','reason':'bridge_dead','message':'private-key','blocked':True})
            evidence.finish({'run_id':'fixture','blocked':True})
            for name in ('session.json','events.jsonl','bridge-status.jsonl','native-commands.jsonl','health.jsonl','result.json'):
                self.assertTrue((root/name).is_file())
                self.assertNotIn('private-key', (root/name).read_text())
            state = json.loads((root/'session.json').read_text())
            self.assertEqual(state['status'],'blocked')
            self.assertEqual(state['processes']['observer']['bridgePID'],42)
            self.assertEqual(state['last_error']['reason'],'bridge_dead')
            self.assertTrue(json.loads((root/'result.json').read_text())['completed'])

    def test_failed_persistence_latches_evidence_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = SessionEvidence(Path(temporary), 'fixture', 'key')
            with patch('djjev.evidence.atomic', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    evidence.heartbeat()
            self.assertTrue(evidence.failed)


class BuildIdentityTests(unittest.TestCase):
    def test_source_bundle_and_mode_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'config').mkdir()
            (root/'config/live_trial.json').write_text('{"implementation":"doom_demo"}')
            manifest = {'files':['config/live_trial.json'], 'commit':'fixture','source_digest':source_digest(root),'protocol':3,'mode':'doom_demo'}
            (root/'build-info.json').write_text(json.dumps(manifest))
            info = {'JevSourceDigest':manifest['source_digest'],'JevSourceCommit':'fixture','JevProtocolVersion':3}
            for app in ('Rekordbox Bridge.app','DJ Jev.app'):
                path = root/app/'Contents/Info.plist';path.parent.mkdir(parents=True)
                path.write_bytes(plistlib.dumps(info))
            self.assertEqual(validate_installation(root),manifest)
            widget = root/'DJ Jev.app/Contents/Info.plist'
            widget.write_bytes(plistlib.dumps({**info,'JevSourceCommit':'old'}))
            with self.assertRaisesRegex(RuntimeError, 'DJ Jev.app'):
                validate_installation(root)
            widget.write_bytes(plistlib.dumps(info))
            (root/'config/live_trial.json').write_text('{"implementation":"doom_demo", "changed":true}')
            with self.assertRaisesRegex(RuntimeError, 'Broncode'):
                validate_installation(root)
            (root/'config/live_trial.json').write_text('{"implementation":"continuous_session"}')
            with self.assertRaisesRegex(RuntimeError, 'doom_demo'):
                validate_installation(root)
