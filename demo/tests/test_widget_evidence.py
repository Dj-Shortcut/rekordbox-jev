"""Per-request widget execution evidence, with no API or native controls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_policy import snapshot, response
from djjev import main, policy


class WidgetEvidenceTests(unittest.TestCase):
    def test_answers_and_execution_are_bound_to_their_own_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(main, 'ROOT', root), patch.object(main, 'DISPLAY', root/'widget'):
                display = main.Display('private-fixture-key')
                request = policy.prepare(snapshot())
                for number in (1, 2):
                    display({'event':'request','request_id':number,'request':request})
                    display({'event':'answer','request_id':number,'response':response(request)})
                display({'event':'ignored','request_id':1,'reason':'decision_no_longer_applicable'})
                decision = policy.resolve(request, response(request))
                display({'event':'dispatch','request_id':2,'decision':decision})
                def read(number):
                    return json.loads((root/'widget/jev-events'/f'{display.run_id}-{number}.json').read_text())
                self.assertEqual(read(1)['execution']['status'], 'ignored')
                self.assertEqual(read(2)['execution']['status'], 'dispatch')
                self.assertNotIn('verified', read(2)['execution'])
                display({'event':'verified','request_id':2,'decision':decision,
                         'result':{'verified':True,'dispatched':False}})
                self.assertEqual(read(1)['execution']['status'], 'ignored')
                self.assertTrue(read(2)['execution']['verified'])
                self.assertFalse(read(2)['execution']['dispatched'])
                self.assertEqual(read(2)['result']['payload_id'], read(2)['request']['payload_id'])
                display({'event':'execution_deferred','request_id':1,'message':'private-fixture-key',
                         'commands_sent':False,'dispatched':False})
                self.assertEqual(read(1)['execution']['message'], '[verborgen]')
                self.assertNotIn('private-fixture-key', (root/'widget/jev-events'/f'{display.run_id}-1.json').read_text())

    def test_pending_cancellation_and_terminal_reason_stay_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(main, 'ROOT', root), patch.object(main, 'DISPLAY', root/'widget'):
                display = main.Display('fixture-key')
                display({'event':'request','request_id':1,'request':policy.prepare(snapshot())})
                display({'event':'ignored','request_id':1,'reason':'recovery_requires_fresh_request'})
                record = json.loads((root/'widget/jev-events'/f'{display.run_id}-1.json').read_text())
                self.assertEqual(record['status'],'cancelled')
                display({'event':'error','reason':'tracks_changed','message':'Exacte stopreden','blocked':True})
                display({'event':'error','reason':'observe_failed','message':'Latere leesfout','blocked':True})
                display({'event':'stopped'})
                status = json.loads((root/'widget/dj-session-status.json').read_text())
                self.assertEqual(status['status'],'blocked')
                self.assertIn('Exacte stopreden',status['message'])
                checkpoint = json.loads((display.directory/'session.json').read_text())
                self.assertEqual(checkpoint['last_error']['reason'],'tracks_changed')


if __name__ == '__main__':
    unittest.main()
