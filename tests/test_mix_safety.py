import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from mix_safety import MixBlocked, require_aligned, require_neutral, guarded_crossfader, paired_bass_step
import dj_session


def state():
    return {'sampledAtMonotonicNS':100_000_000_000,'layoutCalibrated':True,
            'folder':'26','decks':[{'deck':n,'title':str(n),'playingIndicator':True,'fader':1,
                                  'library':{'file':str(n),'key':'Abm'},'bpm':124} for n in (1,2)],
            'mixer':{'red_bar_aligned':True,'red_bar_alignment_error_px':0.5,
                     'deck_assignments':{'1':'left','2':'right'},'crossfader_position':0,
                     'eq_neutral':{str(n):{b:True for b in ('high','mid','low','trim')} for n in (1,2)}}}


class HardMixRules(unittest.TestCase):
    def test_misaligned_unknown_stale_and_nan_never_dispatch(self):
        changes=[('red_bar_aligned',False),('red_bar_aligned',None),
                 ('red_bar_alignment_error_px',float('nan')),('red_bar_alignment_error_px',9)]
        for field,value in changes:
            s=state();s['mixer'][field]=value; dispatch=Mock()
            with patch('mix_safety.time.monotonic',return_value=100.1):
                with self.assertRaises(MixBlocked): guarded_crossfader(1,lambda:s,dispatch)
            dispatch.assert_not_called()
        with self.assertRaises(MixBlocked): require_aligned(state(),now=102)

    def test_alignment_loss_mid_fade_stops_next_step(self):
        current=state(); now=[100.0]; commands=[]
        def observation():
            s=copy.deepcopy(current);s['sampledAtMonotonicNS']=now[0]*1e9
            if commands:s['mixer']['red_bar_aligned']=False
            return s
        session=object.__new__(dj_session.Session)
        session.observe=observation;session.dispatch=lambda payload:commands.append(payload)
        session.clock=lambda:now[0];session.sleep=lambda seconds:now.__setitem__(0,now[0]+seconds)
        with self.assertRaises(MixBlocked):session.fade(.5,8,{1:'1',2:'2'})
        self.assertEqual(len(commands),1)
        self.assertLess(commands[0]['value'],.5)

    def test_eq_pair_is_one_native_request_and_not_two_network_choices(self):
        dispatch=Mock(return_value={'pairMS':250})
        with patch('mix_safety.time.monotonic',return_value=100.1):
            paired_bass_step(1,2,5,state,dispatch)
        dispatch.assert_called_once_with({'command':'eqPair','outgoing':1,'incoming':2,'pixels':5.0})

    def test_reverse_drag_does_not_count_as_neutral(self):
        s=state();s['mixer']['eq_neutral']['2']['low']=False
        with self.assertRaises(MixBlocked):require_neutral(s)
        s['mixer']['eq_neutral']['2']['low']=None
        with self.assertRaises(MixBlocked):require_neutral(s)
        s['mixer']['eq_neutral']['2']['low']=True
        require_neutral(s)

    def test_late_local_fade_does_not_catch_up(self):
        now=[100.0]; commands=[]
        def observation():
            s=state();s['sampledAtMonotonicNS']=now[0]*1e9;return s
        session=object.__new__(dj_session.Session)
        session.observe=observation;session.dispatch=lambda p:commands.append(p)
        session.clock=lambda:now[0];session.sleep=lambda seconds:now.__setitem__(0,now[0]+seconds+2)
        with self.assertRaises(MixBlocked):session.fade(.5,8,{1:'1',2:'2'})
        self.assertEqual(commands,[])

    def test_session_prepares_third_track_after_first_transition(self):
        session=object.__new__(dj_session.Session)
        session.played=set();session.start=Mock(return_value=1);session.observe=state
        session.prepare=Mock(side_effect=[({'file':'new-B'},32),({'file':'new-A'},32),KeyboardInterrupt()])
        session.transition=Mock(side_effect=[2,1]);session.record=Mock()
        with self.assertRaises(KeyboardInterrupt):session.run()
        self.assertEqual([c.args[0] for c in session.prepare.call_args_list],[1,2,1])
        self.assertEqual(session.transition.call_count,2)
        self.assertTrue({'new-B','new-A'} <= session.played)

    def test_candidate_scope_tempo_key_and_history(self):
        tracks=[{'file':name,'key':key,'original_bpm':bpm,'music_scope':scope,'duration_seconds':200}
                for name,key,bpm,scope in [('ok','Abm',125,'26'),('fast','Abm',133,'26'),
                  ('otherkey','Am',124,'26'),('outside','Abm',124,'27')]]
        self.assertEqual([t['file'] for t in dj_session.next_candidates(tracks,state()['decks'][0],set())],['ok'])

if __name__=='__main__':unittest.main()
