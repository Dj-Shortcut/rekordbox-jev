"""Offline entry-grid regression cases; no live inputs or audio playback."""
from copy import deepcopy
import unittest

from djjev import policy
from djjev.entry_grid import target_current
from test_musical_timing import MusicalTimingTests
from test_policy import response


def frame(*, duration=420, anchor=320, elapsed=314, end=400, ratio=1.):
    state = MusicalTimingTests().prepared(elapsed=elapsed, remaining=duration-elapsed)
    for deck in state['decks'].values():
        deck['bpm'] = 120 * ratio
    state['decks']['B'].update(elapsed=0., remaining=300.)
    structure = {'status':'candidate', 'groups':[], 'last_return': {
        'start_seconds':anchor, 'end_seconds':end, 'start_bar':anchor/2,
        'bars':(end-anchor)/2, 'low_band_return_db':12.}}
    state['audio_windows'] = {
        'A': {'status':'available', 'track_id':state['decks']['A']['track_id'],
              'analysis_id':'outgoing', 'position_seconds':elapsed, 'position_bar':elapsed/2,
              'tempo_ratio':ratio, 'source_grid':{'bpm':120.,'start_seconds':0.},
              'entry_structure':structure},
        'B': {'status':'available', 'track_id':state['decks']['B']['track_id'],
              'analysis_id':'incoming', 'position_seconds':0., 'position_bar':0.,
              'tempo_ratio':ratio}}
    return state


def start(request, slot):
    return policy.resolve(request,response(request, transport='play_B',entry_slot=slot,
        kick_pattern='plausible',last_section='supported',post_peak='past',entry_fit='suitable',
        arrangement_fit='unknown'))


class EntryGridTests(unittest.TestCase):
    def test_long_track_x1_short_track_x2_on_two_bar_grid(self):
        for state, preferred in ((frame(),'x1'),
                (frame(duration=280,anchor=180,elapsed=174,end=272),'x2')):
            req=policy.prepare(state)
            grid=req['state']['musical_timing']['entry_grid']
            self.assertEqual(grid['selected_preference'],preferred)
            self.assertEqual([s['index'] for s in grid['slots']],[1,2,4,6,8])
            slots={s['id']:s for s in grid['slots']}
            self.assertEqual(slots['x2']['source_seconds']-slots['x1']['source_seconds'],4.)
            self.assertEqual(slots['x4']['source_seconds']-slots['x2']['source_seconds'],8.)

    def test_a_future_agreed_slot_is_armed_before_the_return(self):
        state=frame();req=policy.prepare(state);decision=start(req,'x1')
        self.assertEqual(decision['entry_target']['source_seconds'],320)
        self.assertTrue(policy.applicable(decision,state))
        self.assertTrue(target_current(decision['entry_target'],state))
        hold=policy.resolve(req,response(req,transport='hold',entry_slot='x1'))
        self.assertNotIn('entry_target',hold)

    def test_missed_x2_offers_x4_never_x3(self):
        state=frame(duration=280,anchor=180,elapsed=185,end=272)
        req=policy.prepare(state)
        self.assertEqual(req['state']['musical_timing']['entry_grid']['selected_preference'],'x4')
        self.assertEqual(set(req['questions']['entry_slot']['criteria']),{'fallback','x4'})

    def test_short_track_can_prefer_x1_when_x2_places_drop_too_late(self):
        state=frame(duration=280,anchor=180,elapsed=174,end=272)
        state['audio_windows']['B']['first_drop'] = {'status':'possible_first_drop',
            'drop_confirmed':False,'candidate':{'source_seconds':69.}}
        req=policy.prepare(state)
        self.assertEqual(req['state']['musical_timing']['entry_grid']['selected_preference'],'x1')

    def test_selected_slot_survives_only_same_track_tempo_and_future_clock(self):
        state=frame();decision=start(policy.prepare(state),'x1');target=decision['entry_target']
        for kind in ('seek','tempo','identity','expired'):
            newer=deepcopy(state)
            if kind=='seek': newer['audio_windows']['A']['position_seconds']+=2
            if kind=='tempo': newer['audio_windows']['A']['tempo_ratio']=1.02
            if kind=='identity': newer['decks']['B']['track_id']='changed'
            if kind=='expired':
                newer['captured_ns']+=6_000_000_000
                newer['audio_windows']['A']['position_seconds']+=6
            self.assertFalse(target_current(target,newer),kind)

    def test_variable_or_missing_grid_keeps_the_unquantized_fallback(self):
        state=frame();state['audio_windows']['A'].pop('source_grid')
        req=policy.prepare(state)
        self.assertEqual(req['state']['musical_timing']['entry_grid']['status'],'unavailable')
        self.assertNotIn('entry_slot',req['questions'])

    def test_source_time_is_converted_once_under_pitch(self):
        state=frame(ratio=1.04);req=policy.prepare(state)
        slot=req['state']['musical_timing']['entry_grid']['slots'][0]
        self.assertAlmostEqual(slot['seconds_until'],6/1.04)
        self.assertEqual(slot['source_seconds'],320)

    def test_short_incoming_file_does_not_gain_a_fictitious_slot_budget(self):
        state=frame();state['decks']['B']['remaining']=20
        req=policy.prepare(state)
        self.assertIsNone(req['state']['musical_timing']['entry_grid']['selected_preference'])
        self.assertEqual(set(req['questions']['entry_slot']['criteria']),{'fallback'})


if __name__ == '__main__':
    unittest.main()
