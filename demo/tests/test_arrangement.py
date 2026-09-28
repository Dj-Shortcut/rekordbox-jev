"""Written for the next authorized test session; no live audio or controls."""
import unittest

from djjev.arrangement import context, first_drop_evidence


class ArrangementTests(unittest.TestCase):
    def test_energy_return_remains_hypothesis_and_opening_is_not_a_later_drop(self):
        rows = [dict(start_bar=0, bars=4, energy_dbfs=-24,
                     low_energy_dbfs_estimate=-35, onsets_per_beat=.1),
                dict(start_bar=4, bars=4, energy_dbfs=-10,
                     low_energy_dbfs_estimate=-14, onsets_per_beat=1.)]
        structure = {'groups': [{'start_bar':4, 'start_seconds':8, 'bars':16}]}
        result = first_drop_evidence(rows, structure)
        self.assertEqual(result['status'], 'possible_first_drop')
        self.assertFalse(result['drop_confirmed'])
        structure['groups'][0]['start_bar'] = 0
        result = first_drop_evidence(rows, structure)
        self.assertEqual(result['status'], 'starts_with_activity')
        self.assertIsNone(result['candidate'])

    def test_drop_countdown_uses_live_tempo_and_actual_running_position(self):
        state = {'decks': {'A': {'remaining':100},
                          'B': {'playing':False, 'elapsed':25, 'bpm':126}},
                 'audio_windows': {'A': {'status':'available', 'tempo_ratio':1.},
                     'B': {'status':'available', 'tempo_ratio':1.05,
                           'first_drop': {'status':'possible_first_drop', 'drop_confirmed':False,
                                          'candidate': {'source_seconds':42}}}}}
        planned = context(state, 'A', 'B', 10)
        self.assertEqual(planned['seconds_to_incoming_drop'],40)
        self.assertEqual(planned['outgoing_seconds_at_drop'],50)
        self.assertEqual(planned['handover_fit'],'possible_if_arrangement_supported')
        state['decks']['B'].update(playing=True,elapsed=21)
        running = context(state, 'A', 'B', 10)
        self.assertEqual(running['seconds_to_incoming_drop'],20)
        self.assertEqual(running['outgoing_seconds_at_drop'],80)
        state['decks']['B']['elapsed']=43
        self.assertEqual(context(state,'A','B')['handover_fit'],'drop_already_passed')

    def test_missing_drop_does_not_invent_a_countdown(self):
        result=context({'decks':{'A':{'remaining':100},'B':{'playing':False}}},'A','B')
        self.assertEqual(result['handover_fit'],'unknown')
        self.assertIsNone(result['seconds_to_incoming_drop'])


if __name__ == '__main__':
    unittest.main()
