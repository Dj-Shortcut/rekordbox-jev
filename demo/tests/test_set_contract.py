"""Offline set contract, NOT Jev intelligence or real-Rekordbox proof.

Runs the production Runner, policy and environment together. The provider returns
explicit state-dependent FIXTURE answers. A strict in-memory native simulator
implements the wire contract; only that simulator advances track clocks, aligns
beats and applies requested controls. Gesture duration is instantaneous in this
test. There are no external state nudges, sockets, macOS actions or API calls.
"""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev import policy, state
from djjev.environment import Rekordbox
from djjev.runner import Runner
from test_policy import raw


def tracks():
    return [{'id':state.track_id(name+'.mp3'),'file':name+'.mp3','title':name,
             'artist':'Fixture artist','folder':'26','bpm':124.,'key':'Am','duration':300.,
             'beatgrid':[{'position_seconds':.1,'bpm':124.,'beat_in_bar':1,'meter':'4/4'}]}
            for name in ('Ended track','Opening track','Successor one','Successor two','Prepared next')]


class StrictNativeFixture:
    """Requested input alone changes controls; observation advances playing time."""
    def __init__(self):
        self.raw=raw(); self.library=tracks(); self.calls=[]; self.stopped=False
        self.positions={1:300.,2:0.}; self.loaded={1:self.library[0],2:None}
        self.launches={}; self.alignment_guards=0; self.silent_closures=0
        self.raw['playingIndicators']['deck1']=True  # observed real-world end case
        self._render()

    def _render(self):
        for n in (1,2):
            track=self.loaded[n]
            if track is None:continue
            elapsed=min(track['duration'],self.positions[n]);remaining=track['duration']-elapsed
            def clock(value):return f'{int(value)//60:02d}:{value%60:04.1f}'
            self.raw['decks'][n-1].update(title=track['title'],displayedBPM=f"0.0% {track['bpm']:.2f}",
                metadata=f"Fixture artist {track['bpm']:.2f} {track['key']} -{clock(remaining)} {clock(elapsed)}")
        self.raw['sampledAtMonotonicNS']=time.monotonic_ns()

    def playing(self,n):return self.raw['playingIndicators']['deck'+str(n)]
    def closed(self,n):
        cross=self.raw['mixer']['crossfader_position']
        return self.raw['faders']['deck'+str(n)]<=.01 or (cross>=.99 if n==1 else cross<=.01)
    def title(self,n):return self.raw['decks'][n-1]['title']
    def low(self,n):return self.raw['mixer']['eq_position'][str(n)]['low']
    def move(self,n,pixels):
        angle=max(-1.,min(0.,self.low(n)-pixels*.02))
        self.raw['mixer']['eq_position'][str(n)]['low']=angle
        self.raw['mixer']['eq_neutral'][str(n)]['low']=abs(angle)<1e-9

    async def start(self):pass
    async def close(self):self.stopped=True

    async def call(self,role,name,**params):
        assert not self.stopped, 'No input after stop'
        await asyncio.sleep(.0001)
        if name=='observe':
            assert role=='observer'
            for n in (1,2):
                if self.loaded[n] and self.playing(n):self.positions[n]=min(300.,self.positions[n]+.2)
            self._render()
            return deepcopy(self.raw)
        assert role=='control'
        wire={'command':name,'clientPID':os.getpid(),**params}
        assert type(wire['notAfterMonotonicNS']) is int and wire['notAfterMonotonicNS']>time.monotonic_ns()
        assert wire['expectedTracks']=={str(n):self.title(n) for n in (1,2)}, 'Both native title guards required'
        self.calls.append((name,deepcopy(wire)))
        result={'dispatched':True,'verified':True}
        if name=='openFolder26':
            self.raw['browserHeading']='26';result['dispatched']=False
        elif name=='loadChosenTrack':
            n=wire['deck']; other=3-n
            assert wire['expectedTrack']==self.title(n) and self.playing(n) is False
            replacing=self.loaded[n] is not None
            assert wire['replaceStopped'] is replacing
            if wire['allowSilentReplacement']:
                assert replacing and not self.playing(other) and wire['expectedOtherTrack']==self.title(other)
            elif replacing:
                assert self.closed(n) and self.playing(other) and not self.closed(other)
            self.loaded[n]=next(t for t in self.library if t['file']==wire['file'])
            self.positions[n]=0.
            self.raw['mixer']['red_bar_aligned']=False
        elif name=='setPlayback':
            n=wire['deck'];assert wire['expectedTrack']==self.title(n) and self.loaded[n]
            if wire.get('endOnly'):
                assert wire['playing'] is False and self.positions[n]>=299.9
            self.raw['playingIndicators']['deck'+str(n)]=wire['playing']
        elif name=='action':
            deck,action=wire['action'].split('.');n=int(deck[-1])
            assert wire['expectedTrack']==self.title(n)
            if action=='master':
                self.raw['mixer']['master_lit']={str(d):d==n for d in (1,2)}
            elif action=='sync':
                self.raw['mixer']['beat_sync_lit'][str(n)]=not self.raw['mixer']['beat_sync_lit'][str(n)]
            elif action=='start':self.positions[n]=0.
            else:raise AssertionError(action)
            result['verified']=False  # actual native shortcut is verified by caller
        elif name=='closeStoppedDeck':
            n=wire['deck'];assert not self.playing(n) and self.playing(3-n)
            assert not self.closed(3-n)
            self.raw['mixer']['crossfader_position']=1. if n==1 else 0.
            self.silent_closures+=1
        elif name=='launchAligned':
            n=wire['incoming'];other=wire['outgoing']
            assert {n,other}=={1,2} and not self.playing(n) and self.playing(other)
            assert self.closed(n) and self.raw['mixer']['master_lit'][str(other)] is True
            assert self.raw['mixer']['beat_sync_lit'][str(n)] is True
            assert 60<=wire['bpm']<=200 and 0<=wire['cueOffsetSeconds']<=2
            self.raw['playingIndicators']['deck'+str(n)]=True
            identity=self.loaded[n]['id'];self.launches[identity]=self.launches.get(identity,0)+1
            # A first imperfect launch requires an actual subsequent Jev ALIGN
            # answer and a second native launch, not a fixture-driver correction.
            self.raw['mixer']['red_bar_aligned']=self.launches[identity]>=2
            result['verified']=False
        elif name=='eq':
            n=wire['deck'];assert wire['band']=='low' and abs(wire['pixels'])<=40
            if self.playing(n) and not self.closed(n):assert self.raw['mixer']['red_bar_aligned'] is True
            self.move(n,wire['pixels']);result['verified']=False
        elif name=='eqReset':
            for band in wire['bands']:
                self.raw['mixer']['eq_position'][str(wire['deck'])][band]=0.
                self.raw['mixer']['eq_neutral'][str(wire['deck'])][band]=True
        elif name=='mixGesture':
            assert self.raw['mixer']['red_bar_aligned'] is True, 'No fader/EQ mix before red alignment'
            assert all(self.playing(n) for n in (1,2))
            assert .25<=wire['durationSeconds']<=12
            self.alignment_guards+=1
            if 'bassPixels' in wire:
                out,inc=wire['outgoing'],wire['incoming']
                assert {out,inc}=={1,2} and 0<wire['bassPixels']<=40
                assert self.low(out)>-.97 and self.low(inc)<-.04
                self.move(out,wire['bassPixels']);self.move(inc,-wire['bassPixels'])
                result['bassDirectionVerified']=True
            if 'crossfader' in wire:
                assert 0<=wire['crossfader']<=1
                self.raw['mixer']['crossfader_position']=wire['crossfader']
                result['crossfaderVerified']=True
        else:raise AssertionError('Unexpected native primitive '+name)
        self._render();result['after']=deepcopy(self.raw)
        return result


class FixtureChoices:
    """Synthetic state-dependent provider, intentionally NOT Jev inference."""
    def __init__(self):self.requests=[];self.closed=False

    async def ask(self,request):
        self.requests.append(deepcopy(request));await asyncio.sleep(.001)
        q=request['questions'];d=request['state']['decks']; options=q['transport']['criteria']
        selected={'transport':'hold','crossfader':'hold','bass':'hold','duration':'beats4'}
        if 'next_track' in q:
            # Candidate name order is fixture policy, never production ranking.
            candidate_order={t['id']:i for i,t in enumerate(tracks())}
            selected['next_track']=min(q['next_track']['criteria'],key=lambda t:candidate_order[t] or 999)
        playing=[n for n in ('A','B') if d[n]['playing']]
        ended=[n for n in playing if d[n]['remaining'] is not None and d[n]['remaining']<=.1]
        def choose(prefix,n):
            option=prefix+'_'+n
            if option in options:selected['transport']=option;return True
            return False
        if ended:
            choose('stop',ended[0])
        elif len(playing)==2:
            incoming=min(playing,key=lambda n:d[n]['elapsed'])
            outgoing='B' if incoming=='A' else 'A'
            if 'align_'+incoming in options:
                choose('align',incoming)
            elif abs(request['state']['mixer']['cross']-(0 if incoming=='A' else 1))<=.01:
                choose('stop',outgoing)
            else:
                assert incoming in q.get('crossfader',{}).get('criteria',{}), 'No legal fader successor in a known aligned state'
                selected.update(transport='mix',crossfader=incoming,bass=incoming)
        elif len(playing)==1:
            active=playing[0];other='B' if active=='A' else 'A'
            if choose('reset',active):pass
            elif d[other]['track_id'] and d[other]['elapsed']>0:
                if not choose('reset',other) and not choose('load',other):choose('prepare',other)
            elif choose('play',other):pass
            elif choose('prepare',other):pass
            else:choose('load',other)
        else:
            ready=[n for n in ('A','B') if 'play_'+n in options]
            if ready:choose('play',ready[0])
            elif 'load_B' in options:choose('load','B')
            elif 'load_A' in options:choose('load','A')
        assert selected['transport']!='hold' or selected['crossfader']!='hold', 'Fixture reached a state without a progressing legal action'
        answers={}
        for name,question in q.items():
            choice=selected[name]
            assert choice in question['criteria'], (name,choice,list(question['criteria']))
            answers[name]={'type':'choice','choice':choice,'confidence':1.,
                'probabilities':{k:float(k==choice) for k in question['criteria']}}
        return {'model':'fixture-no-inference','answers':answers}

    async def close(self):self.closed=True


class FullSetContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_answers_drive_two_handoffs_and_prepare_a_third_without_external_nudges(self):
        native=StrictNativeFixture();env=Rekordbox(native,tracks());client=FixtureChoices();events=[]
        runner=Runner(env,policy,client,events.append,tick_interval=.001,observe_interval=.002,decision_interval=.002)
        task=asyncio.create_task(runner.run())
        def finished():
            verified=[e for e in events if e['event']=='verified']
            mixes=[e for e in verified if e['decision']['crossfader'] in ('A','B')]
            return len(mixes)>=2 and bool(verified) and verified[-1]['decision']['transport'].startswith('prepare_')
        try:
            async def wait_for_outcome():
                while not finished() and not runner.blocked:
                    failures=[e for e in events if e['event']=='error']
                    if failures:self.fail(str(failures[-1]))
                    await asyncio.sleep(.002)
            await asyncio.wait_for(wait_for_outcome(),5)
            self.assertFalse(runner.blocked,[e for e in events if e['event']=='error'])
            self.assertTrue(finished())
        finally:
            await runner.stop();await asyncio.wait_for(task,1)
        verified=[e for e in events if e['event']=='verified']
        choices=[e['decision']['transport'] for e in verified]
        mixes=[e for e in verified if e['decision']['crossfader'] in ('A','B')]
        self.assertEqual(len(mixes),2)
        self.assertEqual([e['decision']['crossfader'] for e in mixes],['A','B'])
        self.assertEqual(choices[0],'stop_A')
        self.assertIn('align_A',choices);self.assertIn('align_B',choices)
        self.assertEqual(choices[-1],'prepare_A')
        self.assertGreaterEqual(choices.count('load_A'),2)
        self.assertGreaterEqual(choices.count('load_B'),2)
        for index,e in enumerate(mixes):
            s=e['result']['snapshot'];target=e['decision']['crossfader']
            self.assertTrue(s['mixer']['aligned']);self.assertTrue(s['decks'][target]['eq_neutral']['low'])
            old='B' if target=='A' else 'A'
            start=verified.index(e)+1
            end=verified.index(mixes[index+1]) if index+1<len(mixes) else len(verified)
            restored=[v for v in verified[start:end] if v['decision']['transport']=='reset_'+old
                and all(all(d['eq_neutral'].values()) for d in v['result']['snapshot']['decks'].values())]
            self.assertTrue(restored,'Each completed handoff must restore both decks before the next preparation')
        final=verified[-1]['result']['snapshot']['decks']
        self.assertEqual(final['A']['track_id'],tracks()[4]['id'])
        self.assertIs(final['A']['playing'],False)
        self.assertTrue(final['A']['sync']);self.assertLess(final['A']['bass'],-.4)
        self.assertTrue(final['B']['playing']);self.assertTrue(final['B']['master'])
        self.assertTrue(all(final['B']['eq_neutral'].values()))
        self.assertGreater(native.alignment_guards,2)
        self.assertGreaterEqual(native.silent_closures,1)
        self.assertTrue(any(e['event']=='snapshot' and e['busy'] for e in events))
        self.assertFalse(any(e['event']=='request' and e['busy'] for e in events))
        self.assertGreaterEqual(len(client.requests),runner.verified_decisions)
        answered={e['request_id'] for e in events if e['event']=='answer'}
        self.assertTrue(all(e['request_id'] in answered for e in verified))
        self.assertTrue(native.stopped);self.assertTrue(client.closed)
        self.assertTrue(all(e['response']['model']=='fixture-no-inference' for e in events if e['event']=='answer'))


if __name__=='__main__':unittest.main()
