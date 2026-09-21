"""Offline contract checks with an explicit fake transport, not live Jev inference."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_decisions as dj


class DecisionContract(unittest.TestCase):
    def test_key_prompt_requires_interactive_terminal(self):
        with patch.object(dj.sys.stdin, 'isatty', return_value=False), patch.object(dj.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(RuntimeError, 'eigen Terminal'):
                dj.prompt_api_key()
            prompt.assert_not_called()

    def test_key_prompt_rejects_visible_fallback(self):
        with patch.object(dj.sys.stdin, 'isatty', return_value=True), patch.object(dj.getpass, 'getpass', side_effect=dj.getpass.GetPassWarning('fallback')):
            with self.assertRaisesRegex(RuntimeError, 'Verborgen invoer'):
                dj.prompt_api_key()

    def setUp(self):
        self.context = dj.read_json(dj.ROOT/'examples/jev-context.json')
        self.plans = dj.read_json(dj.ROOT/'examples/jev-plans.json')
        self.request = dj.make_request(self.context, self.plans)
        self.reply = {'model': 'jev-test-fixture', 'answers': {'mix_plan': {
            'type': 'choice', 'choice': 'bass_at_phrase', 'confidence': .8,
            'probabilities': {'defer': .05, 'bass_at_phrase': .9, 'swap_at_drop': .05}}},
            'usage': {'input_tokens': 0, 'output_tokens': 0}}

    def test_tutorial_is_authority_not_inferred_user_taste(self):
        state = self.request['payload']['state']
        self.assertNotIn('preferences', state)
        self.assertEqual(state['tutorial_guidance']['primary_source'], 'https://wearecrossfader.co.uk/blog/how-to-mix-edm/')
        self.assertIn('defer', self.request['payload']['questions']['mix_plan']['criteria'])

    def test_complete_instructions_are_in_the_actual_http_body(self):
        def fake(request, timeout):
            payload = json.loads(request.data)
            sent = payload['state']['tutorial_guidance']
            self.assertEqual(sent, dj.read_json(dj.ROOT/'config/mixing_guidance.json'))
            self.assertIn('selection_procedure', sent)
            self.assertIn('timing_contract', sent)
            self.assertIn('control_meanings', sent)
            for technique in sent['techniques'].values():
                self.assertTrue(technique['choose_when'])
                self.assertTrue(technique['reject_when'])
                self.assertGreaterEqual(len(technique['steps']), 4)
                self.assertTrue(all(step['action'] for step in technique['steps']))
            return io.BytesIO(json.dumps(self.reply).encode())
        dj.evaluate(self.request, api_key='test-only', transport=fake)

    def test_every_plan_points_to_an_embedded_technique(self):
        payload = self.request['payload']
        for choice, option in payload['questions']['mix_plan']['criteria'].items():
            if choice != dj.DEFER:
                self.assertIn(option['technique_id'], payload['state']['tutorial_guidance']['techniques'])

    def test_unknown_or_out_of_scope_track_is_rejected(self):
        self.context['tracks']['incoming']['music_scope'] = 'other'
        with self.assertRaises(ValueError):
            dj.make_request(self.context, self.plans)

    def test_marker_for_wrong_track_is_rejected(self):
        self.plans[0]['timing']['incoming_takeover_id'] = 'a_phrase'
        with self.assertRaises(ValueError):
            dj.make_request(self.context, self.plans)

    def test_synthetic_markers_cannot_be_labeled_observed(self):
        self.context['provenance'] = 'observed'
        with self.assertRaises(ValueError):
            dj.make_request(self.context, self.plans)

    def test_no_music_structure_does_not_call_api(self):
        request = dj.make_request(self.context, [])
        def forbidden(*a, **k):
            self.fail('No API call should be made')
        result = dj.evaluate(request, api_key='test-only', transport=forbidden)
        self.assertEqual(result['status'], 'needs_music_structure')
        self.assertFalse(result['inference_performed'])

    def test_missing_key_is_explicit_and_sends_nothing(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(dj, 'urlopen') as send:
            with self.assertRaisesRegex(RuntimeError, 'TYPESAFE_API_KEY ontbreekt'):
                dj.evaluate(self.request)
            send.assert_not_called()

    def test_unknown_choice_rejected(self):
        self.reply['answers']['mix_plan']['choice'] = 'invented_action'
        with self.assertRaises(ValueError):
            dj.validate_answer(self.request, self.reply)

    def test_nan_confidence_rejected(self):
        self.reply['answers']['mix_plan']['confidence'] = float('nan')
        with self.assertRaises(ValueError):
            dj.validate_answer(self.request, self.reply)

    def test_modified_request_rejected(self):
        self.request['payload']['model'] = 'changed'
        with self.assertRaises(ValueError):
            dj.evaluate(self.request, api_key='test-only')

    def test_late_answer_is_not_used(self):
        fake = lambda *a, **k: io.BytesIO(json.dumps(self.reply).encode())
        with patch.object(dj.time, 'monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, 'niet tijdig'):
                dj.evaluate(self.request, api_key='test-only', transport=fake, timeout=5)

    def test_provider_error_does_not_expose_body_or_retry(self):
        calls = []
        def fake(request, timeout):
            calls.append(request)
            raise HTTPError(dj.ENDPOINT, 429, 'test-secret', {}, io.BytesIO(b'test-secret'))
        with self.assertRaisesRegex(RuntimeError, 'HTTP 429') as error:
            dj.evaluate(self.request, api_key='test-secret', transport=fake)
        self.assertNotIn('test-secret', str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_valid_fixture_returns_bound_plan_without_execution(self):
        def fake(request, timeout):
            self.assertEqual(request.full_url, dj.ENDPOINT)
            self.assertEqual(json.loads(request.data)['model'], 'jev-latest')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-only')
            return io.BytesIO(json.dumps(self.reply).encode())
        result = dj.evaluate(self.request, api_key='test-only', transport=fake)
        self.assertEqual(result['plan']['id'], 'bass_at_phrase')
        self.assertEqual(result['context_id'], self.request['context_id'])
        self.assertEqual(result['provenance'], 'synthetic_example')
        self.assertFalse(result['execution_enabled'])

if __name__ == '__main__':
    unittest.main()
