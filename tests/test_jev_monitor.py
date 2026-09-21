"""Widget events are paired with exact requests; secrets never enter event files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jev_monitor import evaluate_recorded

class MonitorEvents(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.request = {'payload_id':'example-id','payload':{'questions':{'mix_plan':{'instructions':'Exacte vraagtekst'}}}}
    def event(self):
        files = list(self.folder.glob('*.json'))
        self.assertEqual(len(files),1)
        return json.loads(files[0].read_text())
    def test_pending_and_answer_share_original_question(self):
        def fake(prepared, api_key):
            event = self.event()
            self.assertEqual(event['status'],'pending')
            self.assertEqual(event['request'],self.request)
            return {'inference_performed':True,'payload_id':'example-id','choice':'defer'}
        result = evaluate_recorded(self.request,'test-secret',directory=self.folder,evaluator=fake)
        event = self.event()
        self.assertEqual(event['status'],'answered')
        self.assertEqual(event['result'],result)
        self.assertNotIn('test-secret',json.dumps(event))
        self.assertFalse(list(self.folder.glob('*.tmp')))
    def test_error_redacts_secret_and_does_not_invent_answer(self):
        def fake(prepared,api_key):
            raise RuntimeError('failed with test-secret')
        with self.assertRaises(RuntimeError):
            evaluate_recorded(self.request,'test-secret',directory=self.folder,evaluator=fake)
        event=self.event()
        self.assertEqual(event['status'],'error')
        self.assertIsNone(event['result'])
        self.assertNotIn('test-secret',event['error'])
    def test_missing_music_data_is_not_labeled_as_inference(self):
        evaluate_recorded(self.request,directory=self.folder,evaluator=lambda *a,**k:{'inference_performed':False,'status':'needs_music_structure'})
        self.assertEqual(self.event()['status'],'not_sent')
    def test_stop_button_cancels_event_without_inventing_answer(self):
        def interrupted(*args,**kwargs):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            evaluate_recorded(self.request,directory=self.folder,evaluator=interrupted)
        event=self.event()
        self.assertEqual(event['status'],'cancelled')
        self.assertIsNone(event['result'])
        self.assertIn('finished_at',event)
    def test_logging_failure_does_not_replace_decision(self):
        expected={'inference_performed':True,'choice':'defer'}
        with patch('jev_monitor.write_event',side_effect=OSError('disk failure')):
            actual=evaluate_recorded(self.request,directory=self.folder,evaluator=lambda *a,**k:expected)
        self.assertEqual(actual,expected)

if __name__=='__main__':unittest.main()
