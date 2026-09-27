"""Synthetic evidence only: no API, audio decoding, sockets or native app."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/summarize_run.py'
SPEC = importlib.util.spec_from_file_location('audio_replay_summary', SCRIPT)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def request(identifier=1, *, audio=True):
    row = {'start_seconds': 64., 'end_seconds': 72., 'start_bar': 32., 'bars': 4,
        'energy_dbfs': -12., 'low_energy_dbfs_estimate': -16., 'low_fraction': .4,
        'onsets_per_beat': 1.2, 'flux': .03}
    deck = {'status': 'available', 'track_id': 't_1234567890123456', 'analysis_id': 'a' * 64,
        'position_seconds': 66., 'position_bar': 33., 'current_window': row,
        'lookahead': [{'horizon_bars': 8, 'coverage_bars': 8, 'energy_dbfs': -10.,
            'low_energy_dbfs_estimate': -13., 'low_fraction': .5, 'onsets_per_beat': 1.4, 'flux': .05}],
        'changes': [{'at_seconds': 72., 'in_bars': 3., 'energy_delta_db': 2.,
            'low_energy_delta_db': 3., 'onsets_per_beat_delta': .2, 'flux_delta': .02,
            'observations': ['low_band_rising']}],
        'source_contour': {'segment_bars':16, 'position_segment_index':2,
            'basis':'Earlier source positions are not proof they were heard.',
            'segments':[{'start_seconds':32.*i,'end_seconds':32.*(i+1),'start_bar':16*i,
                'coverage_bars':16.,'energy_dbfs':level,'low_energy_dbfs_estimate':level-4,
                'onsets_per_beat':1.2,'flux':.03} for i,level in enumerate((-18.,-8.,-13.))]}}
    state = {'snapshot_version': 2, 'captured_ns': 100, 'mixer': {'cross': .5, 'aligned': True},
        'decks': {n: {'title': n, 'elapsed': 66., 'remaining': 60., 'playing': True,
            'bass': -.3, 'eq_neutral': {'low': False, 'mid': True, 'high': True, 'trim': True}}
            for n in ('A', 'B')},
        'musical_timing': {'preferred_overlap_bars': 32, 'confirmed_audible_overlap_seconds': 20.},
        'transition': {'incoming': 'B', 'outgoing': 'A', 'source': 'verified_silent_successor_start'}}
    if audio:
        state['audio_context'] = {'basis': 'offline source audio', 'roles': {'incoming': 'B', 'outgoing': 'A',
            'source': 'verified_transition'}, 'decks': {'A': deepcopy(deck), 'B': deepcopy(deck)},
            'overlap_bars_estimate': 10., 'limitations': ['Source content, not live output loudness.']}
    return {'event': 'request', 'request_id': identifier, 'request': {'state': state, 'questions': {
        name: {'type': 'choice', 'instructions': 'Fixture question.', 'criteria': criteria}
        for name, criteria in {'transport': {'mix': None, 'hold': None},
            'crossfader': {'B': None, 'hold': None}, 'bass': {'B': None, 'hold': None},
            'duration': {'beats8': None, 'beats16': None}}.items()}}}


def answer(identifier=1, hold=False):
    choices = {'transport': 'hold' if hold else 'mix', 'crossfader': 'B', 'bass': 'B', 'duration': 'beats8'}
    return {'event': 'answer', 'request_id': identifier, 'seconds': .3,
        'response': {'model': 'fixture-not-live-Jev', 'answers': {k: {'type': 'choice', 'choice': v,
            'confidence': .8, 'probabilities': {v: .8, 'beats16' if k == 'duration' else 'mix' if hold and k == 'transport' else 'hold': .2}}
            for k, v in choices.items()}}}


def decision(hold=False):
    return {'transport': 'hold' if hold else 'mix', 'crossfader': 'hold' if hold else 'B',
        'bass': 'hold' if hold else 'B', 'duration_beats': 0 if hold else 8}


class AudioReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'events.jsonl'

    def write(self, events):
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events))

    def test_join_preserves_both_deck_evidence_actual_choices_and_verified_result(self):
        source = request()
        after = {'valid': True, 'decks': {'A': {'bass': -.6}, 'B': {'bass': 0}},
            'mixer': {'cross': 1., 'aligned': True}}
        self.write([source, answer(), {'event': 'dispatch', 'request_id': 1, 'decision': decision()},
            {'event': 'verified', 'request_id': 1, 'decision': decision(),
             'result': {'verified': True, 'dispatched': True, 'snapshot': after}}])
        row = reporter.transition_decisions(self.path)['decisions'][0]
        self.assertEqual(row['audio_evidence'], 'recorded_at_decision')
        self.assertEqual(row['audio_context'], source['request']['state']['audio_context'])
        self.assertEqual(row['observed']['decks']['A']['elapsed'], 66.)
        self.assertEqual(row['answer']['answers']['bass']['probabilities'], {'B': .8, 'hold': .2})
        self.assertEqual(row['dispatch']['duration_beats'], 8)
        self.assertEqual(row['verified_state']['decks']['B']['bass'], 0)
        self.assertEqual(row['outcome'], 'verified_physical')
        self.assertEqual(row['lines'], {'request': 1, 'answer': 2, 'dispatch': 3, 'verified': 4})

    def test_hold_and_ignored_answers_do_not_claim_speculative_targets_executed(self):
        self.write([request(1), answer(1, hold=True),
            {'event': 'dispatch', 'request_id': 1, 'decision': decision(True)},
            {'event': 'verified', 'request_id': 1, 'decision': decision(True),
             'result': {'verified': True, 'dispatched': False, 'snapshot': {}}},
            request(2), answer(2), {'event': 'ignored', 'request_id': 2, 'reason': 'decision_no_longer_applicable'}])
        rows = reporter.transition_decisions(self.path)['decisions']
        self.assertEqual(rows[0]['answer']['answers']['bass']['choice'], 'B')
        self.assertEqual(rows[0]['dispatch']['bass'], 'hold')
        self.assertEqual(rows[0]['outcome'], 'verified_hold')
        self.assertEqual(rows[1]['outcome'], 'ignored')
        self.assertNotIn('verified_state', rows[1])
        self.assertNotIn('dispatch', rows[1])

    def test_legacy_missing_context_stays_missing_and_filter_keeps_request_identity(self):
        self.write([request(1, audio=False), answer(1), request(2), answer(2)])
        result = reporter.transition_decisions(self.path, request_id=1)
        self.assertEqual(len(result['decisions']), 1)
        self.assertEqual(result['decisions'][0]['audio_evidence'], 'not_available_at_decision')
        self.assertIsNone(result['decisions'][0]['audio_context'])
        self.assertIn('not model rationales', result['limitations'])
        self.assertIn('no later analysis', result['limitations'])

    def test_error_deferred_and_failed_verification_remain_unconfirmed(self):
        self.write([request(1), {'event': 'execution_deferred', 'request_id': 1,
            'stage': 'execute', 'message': 'fresh state required', 'commands_sent': False},
            request(2), {'event': 'verified', 'request_id': 2, 'decision': decision(),
                'result': {'verified': False, 'dispatched': True, 'snapshot': {'valid': True}}},
            request(3), {'event': 'error', 'request_id': 3, 'stage': 'execute', 'reason': 'execution_unconfirmed'}])
        rows = reporter.transition_decisions(self.path)['decisions']
        self.assertEqual([r['outcome'] for r in rows], ['execution_deferred', 'execution_unconfirmed', 'error'])
        self.assertFalse(rows[0]['failure']['commands_sent'])
        self.assertTrue(all('verified_state' not in r for r in rows))

    def test_projection_redacts_secrets_drops_arbitrary_fields_and_handles_malformed_lines(self):
        source = request()
        source['request']['headers'] = {'Authorization': 'top-secret-header'}
        source['request']['state']['audio_context']['api_key'] = 'secret-in-context'
        source['request']['state']['audio_context']['decks']['A']['current_window']['energy_dbfs'] = float('nan')
        source['request']['questions']['transport']['criteria']['Authorization'] = 'secret-in-criteria'
        source['request']['questions']['transport']['instructions'] = 'Bearer hidden-credential'
        reply = answer()
        reply['response']['rationale'] = 'fabricated explanation'
        self.write([source, reply, {'event': 'request', 'request_id': 2, 'request': {'state': None, 'questions': []}}])
        with self.path.open('a') as stream:
            stream.write('[]\n{"event":')
        report = reporter.transition_decisions(self.path)
        encoded = json.dumps(report, allow_nan=False)
        for secret in ('top-secret-header', 'secret-in-context', 'hidden-credential', 'fabricated explanation', 'secret-in-criteria'):
            self.assertNotIn(secret, encoded)
        self.assertIn('[redacted]', encoded)
        self.assertEqual(report['malformed_lines'], [4, 5])
        self.assertNotIn('energy_dbfs', report['decisions'][0]['audio_context']['decks']['A']['current_window'])

    def test_contour_replay_is_allowlisted_bounded_and_not_added_to_older_evidence(self):
        source=request()
        contour=source['request']['state']['audio_context']['decks']['A']['source_contour']
        contour['api_key']='secret-in-contour'
        contour['segments']*=8
        contour['segments'][0]['peak_passed']=True
        contour['segments'][0]['energy_dbfs']=float('inf')
        older=request(2)
        older['request']['state']['audio_context']['decks']['A'].pop('source_contour')
        self.write([source,older])
        rows=reporter.transition_decisions(self.path)['decisions']
        projected=rows[0]['audio_context']['decks']['A']['source_contour']
        self.assertEqual(projected['position_segment_index'],2)
        self.assertEqual(len(projected['segments']),16)
        self.assertNotIn('energy_dbfs',projected['segments'][0])
        self.assertNotIn('peak_passed',projected['segments'][0])
        self.assertNotIn('secret-in-contour',json.dumps(rows,allow_nan=False))
        self.assertNotIn('source_contour',rows[1]['audio_context']['decks']['A'])

    def test_cli_is_opt_in_and_default_summary_contract_is_unchanged(self):
        self.write([request(), answer()])
        original = reporter.summarize(self.path)
        self.assertNotIn('transition_decisions', original)
        for extra, included in (([], False), (['--transitions', '--request-id', '1'], True)):
            output = io.StringIO()
            with patch('sys.argv', [str(SCRIPT), str(self.path), '--json', *extra]), redirect_stdout(output):
                reporter.main()
            result = json.loads(output.getvalue())
            self.assertEqual('transition_decisions' in result, included)
            result.pop('transition_decisions', None)
            self.assertEqual(result, original)


if __name__ == '__main__':
    unittest.main()
