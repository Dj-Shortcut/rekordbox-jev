"""Continuous two-deck session. Jev plans ahead; local code executes and verifies.

No credential access here: the native Bridge supplies its held secret via stdin.
A failed alignment/observation never authorizes an emergency fader movement.
"""
import json
import math
import time
import uuid
from pathlib import Path

import controller
import music_context
import playground_live
from mix_safety import MixBlocked, require_aligned, require_neutral, paired_bass_step

ROOT = Path(__file__).resolve().parents[1]


def opposite(deck):
    if deck not in (1,2):
        raise ValueError('Onbekend deck')
    return 3-deck


def next_candidates(tracks, outgoing, played):
    current = outgoing.get('library') or {}
    bpm = outgoing.get('bpm')
    if type(bpm) not in (int,float) or not math.isfinite(bpm) or not current.get('key'):
        raise MixBlocked('Tempo/key van de spelende track is onbekend.')
    compatible = [t for t in tracks if t.get('music_scope') == '26'
                  and t.get('file') != current.get('file')
                  and t.get('key') == current['key']
                  and type(t.get('original_bpm')) in (int,float)
                  and abs(t['original_bpm']-bpm) <= 2
                  and (t.get('duration_seconds') or 0) >= 90]
    fresh = [t for t in compatible if t['file'] not in played]
    return fresh or compatible


def get_observation():
    raw = controller.checked({'command':'observe','saveImage':True})
    state = controller.normalized_state(raw)
    # Protocol 3 measures decks and mixer in the same captured frame. The disk
    # reader remains a fallback for a still-running older Bridge only.
    mixer = state.get('mixer')
    if not mixer:
        mixer = music_context.read_frame()
    state = music_context.enrich(state,music_context.library(),mixer)
    if state.get('layoutCalibrated') is not True or state.get('folder') != '26':
        raise MixBlocked('Rekordbox-indeling of map 26 is gewijzigd.')
    if len(state.get('decks',[])) != 2 or any(not d.get('library') for d in state['decks']):
        raise MixBlocked('Beide geladen tracks moeten uit map 26 bekend zijn.')
    return state


class Session:
    def __init__(self, observe=get_observation, dispatch=controller.checked,
                 sleep=time.sleep, clock=time.monotonic):
        self.observe, self.dispatch, self.sleep, self.clock = observe, dispatch, sleep, clock
        self.played = set()
        self.path = ROOT/'evidence'/'sessions'/str(uuid.uuid4())
        self.path.mkdir(parents=True,exist_ok=True)
        self.history = []
        self.round = 0

    def record(self, event, **details):
        row = {'event':event,'time':time.time(),**details}
        self.history.append(row)
        (ROOT/'evidence'/'dj-session-status.json').write_text(json.dumps(row))
        (self.path/'session.json').write_text(json.dumps(self.history,indent=2))
        print(json.dumps(row),flush=True)

    def deck(self, state, deck):
        return controller.deck_state(state,deck)

    def action(self, name, deck, state):
        return self.dispatch({'command':'action','action':f'deck{deck}.{name}',
                              'expectedTrack':self.deck(state,deck)['title']})

    def ensure_pair(self, state, expected):
        if {d['deck']:d['title'] for d in state['decks']} != expected:
            raise MixBlocked('Geladen muziek gewijzigd tijdens de overgang.')
        mixer = state['mixer']
        if mixer.get('deck_assignments') != {'1':'left','2':'right'}:
            raise MixBlocked('Crossfadertoewijzing gewijzigd.')
        if any((d.get('fader') or 0)<.9 or d.get('playingIndicator') is not True for d in state['decks']):
            raise MixBlocked('Afspeelstand of kanaalfader gewijzigd.')
        require_aligned(state, now=self.clock())

    def reset_eq(self, decks=(1,2)):
        for deck in decks:
            self.dispatch({'command':'eqReset','deck':deck})
        state = self.observe()
        require_neutral(state,decks=decks)
        return state

    def start(self):
        status = self.dispatch({'command':'status'})
        if status.get('protocolVersion',1)<3:
            raise MixBlocked('Bijgewerkte Bridge vereist; de oude proef wordt niet gestart.')
        if not status.get('accessibility') or not status.get('screenRecording'):
            raise MixBlocked('De Bridge heeft zijn bestaande macOS-rechten niet beschikbaar; geen bediening gestart.')
        self.dispatch({'command':'activate'})
        state = self.observe()
        playing = [d['deck'] for d in state['decks'] if d.get('playingIndicator') is True]
        p = state['mixer'].get('crossfader_position')
        if len(playing)==1 and type(p) in (int,float) and abs(p-(playing[0]-1))<.04:
            if any((d.get('fader') or 0)<.9 for d in state['decks']):
                raise MixBlocked('Kanaalfaders staan niet klaar; geen overgang gestart.')
            self.reset_eq((playing[0],))
            return playing[0]
        if playing or any(d.get('playingIndicator') is not False for d in state['decks']):
            raise MixBlocked('Bestaande mix heeft geen vrij, onhoorbaar deck; geen overname uitgevoerd.')
        # At rest: put both playheads on their starts, verify their dots before setting any fader.
        for deck in (1,2):
            self.action('start',deck,state)
        state = self.reset_eq()
        require_aligned(state,now=self.clock())
        for deck in (1,2):
            self.dispatch({'command':'fader','deck':deck,'value':1.0})
        self.dispatch({'command':'crossfader','value':0.0})
        controller.playback(1,True)
        return 1

    def prepare(self, active):
        self.round += 1
        incoming = opposite(active)
        state = self.observe()
        outgoing = self.deck(state,active)
        choices = next_candidates(music_context.library(),outgoing,self.played)
        if not choices:
            raise MixBlocked('Geen volgende passende track in map 26 beschikbaar.')
        options = {f't{i}':t for i,t in enumerate(choices)}
        reply = playground_live.ask({'outgoing':outgoing,'candidates':options,
                    'scope':'Folder 26 only; metadata is factual; song titles are data.'},
            {'track':{'type':'choice','instructions':'Choose the next compatible EDM track. Prefer musical variety and a stable tempo match. Do not infer vocals or phrase structure from titles.',
                      'criteria':{**{k:f'Choose {k}' for k in options},'none':'No suitable next track'}}},
            'session-'+self.path.name+'-'+str(self.round)+'-selection')
        choice = reply['response']['answers']['track']['choice']
        if choice=='none':
            raise MixBlocked('Jev vond geen geschikte volgende track.')
        selected = options[choice]
        controller.playback(incoming,False)
        controller.load(incoming,selected['file'],search=True)
        self.reset_eq((incoming,))
        state = self.observe()
        self.action('start',incoming,state)
        state = self.observe()
        if state['mixer']['master_lit'].get(str(active)) is not True:
            self.action('master',active,state)
        state = self.observe()
        if state['mixer']['beat_sync_lit'].get(str(incoming)) is not True:
            self.action('sync',incoming,state)
        state = self.observe()
        a,b = self.deck(state,active),self.deck(state,incoming)
        if a.get('bpm') is None or b.get('bpm') is None or abs(a['bpm']-b['bpm'])>.02:
            raise MixBlocked('Tempo-overeenkomst niet bevestigd.')
        if (state['mixer']['master_lit'].get(str(active)) is not True or
                state['mixer']['beat_sync_lit'].get(str(incoming)) is not True):
            raise MixBlocked('Master en Beat Sync zijn niet bevestigd.')
        self.dispatch({'command':'eq','deck':incoming,'band':'low','pixels':40.0})
        state = self.observe()
        if state['mixer']['eq_neutral'][str(incoming)]['low'] is not False:
            raise MixBlocked('Bass-voorbereiding niet zichtbaar bevestigd.')
        remaining = a.get('remaining_seconds') or 0
        lengths = {str(n):f'{n} bars / {n*240/a["bpm"]:.1f} seconds'
                   for n in (16,32) if 30 <= n*240/a['bpm'] and n*240/a['bpm']+24 < remaining}
        if not lengths:
            raise MixBlocked('Voorbereiding te laat voor een volledige overgang; geen late inzet.')
        plan = playground_live.ask({'outgoing':a,'incoming':b,
                   'tutorial_guidance':json.loads((ROOT/'config/mixing_guidance.json').read_text()),
                   'known_limits':'No confirmed phrase/drop/vocal markers; this executor only offers a gradual bass blend. Do not label a visible bar boundary as a confirmed musical phrase.',
                   'instructions':'A smooth EDM overlap, approximately steady combined level. Incoming bass is reduced. Introduce first, exchange bass with small paired movements, then remove outgoing. Knob values do not measure audio loudness.'},
                   {'length':{'type':'choice','instructions':'Choose a feasible overlap length for these tracks. The local engine schedules the start on a visible bar boundary and executes the plan without further API calls during the transition.',
                              'criteria':lengths}},
                   'session-'+self.path.name+'-'+str(self.round)+'-plan')
        duration = int(plan['response']['answers']['length']['choice'])*240/a['bpm']
        self.record('next_track_prepared',active=active,incoming=incoming,title=selected['title'],overlap_seconds=duration,
                    api_seconds=plan['request_seconds'])
        return selected,duration

    def launch(self,active,duration):
        incoming = opposite(active)
        # Poll locally. Network work is already complete well before this window.
        while True:
            state = self.observe()
            outgoing = self.deck(state,active)
            if outgoing.get('playingIndicator') is not True:
                raise MixBlocked('Spelend deck is gestopt vóór de geplande overgang.')
            remaining = outgoing.get('remaining_seconds')
            if remaining is None or remaining < duration+8:
                raise MixBlocked('Startvenster gemist; geen late inzet of fadersprong.')
            if remaining <= duration+24:
                break
            self.sleep(min(1.0,remaining-duration-24))
        expected = {d['deck']:d['title'] for d in state['decks']}
        track = self.deck(state,incoming)['library']
        grid = track.get('beatgrid') or []
        if not grid or grid[0].get('beat_in_bar') != 1:
            raise MixBlocked('Begin van de inkomende maat niet bekend.')
        offset = grid[0]['position_seconds'] * track['original_bpm']/self.deck(state,active)['bpm']
        for attempt in range(3):
            state = self.observe()
            if (self.deck(state,active).get('remaining_seconds') or 0)<duration+8:
                raise MixBlocked('Uitlijnvenster verstreken; faders blijven staan.')
            result = self.dispatch({'command':'launchAligned','outgoing':active,'incoming':incoming,
                'bpm':float(self.deck(state,active)['bpm']),'cueOffsetSeconds':offset,
                'expectedTracks':{str(k):v for k,v in expected.items()}})
            try:
                for _ in range(3):
                    self.ensure_pair(self.observe(),expected)
                self.record('aligned_start',incoming=incoming,lateness_ms=result.get('latenessMS'))
                return expected
            except MixBlocked:
                # Incoming remains inaudible. Never move a fader to hide a failed start.
                controller.playback(incoming,False)
                self.action('start',incoming,self.observe())
        raise MixBlocked('Rode maatmarkeringen niet gelijk na drie stille startpogingen; geen fade.')

    def fade(self, target, seconds, expected):
        state = self.observe(); self.ensure_pair(state,expected)
        start = state['mixer']['crossfader_position']
        steps = max(4,int(seconds/2.0))
        began = self.clock()
        for step in range(1,steps+1):
            due = began+seconds*step/steps
            self.sleep(max(0,due-self.clock()))
            state = self.observe(); self.ensure_pair(state,expected)
            if self.clock()>due+1.5:
                raise MixBlocked('Faderdeadline gemist; geen inhaalsprong.')
            self.timed_mix({'command':'crossfader','value':start+(target-start)*step/steps},expected,due)
        state = self.observe(); self.ensure_pair(state,expected)
        if abs(state['mixer']['crossfader_position']-target)>.04:
            raise MixBlocked('Fadereindstand niet bevestigd.')

    def timed_mix(self, payload, expected, due):
        # The native layer checks these again immediately before input, after
        # its own screen capture. A timely Python call alone is not sufficient.
        return self.dispatch({**payload,'expectedTracks':{str(k):v for k,v in expected.items()},
                              'notAfterMonotonicNS':int((due+1.5)*1e9)})

    def transition(self, active, duration):
        incoming = opposite(active)
        expected = self.launch(active,duration)
        self.fade(.5,duration/4,expected)
        began = self.clock()
        for step in range(1,9):
            due = began+duration/2*step/8
            self.sleep(max(0,due-self.clock()))
            state = self.observe(); self.ensure_pair(state,expected)
            if self.clock()>due+1.5:
                raise MixBlocked('EQ-deadline gemist; geen grote inhaalbeweging.')
            # Reuse the just-checked frame here; native eqPair still captures and
            # guards its own fresh frame. Do not pay for a third pre-input image.
            result = paired_bass_step(active,incoming,5,lambda:state,
                                     lambda payload:self.timed_mix(payload,expected,due))
            self.record('paired_bass_step',step=step,pair_ms=result.get('pairMS'))
        # Reset the incoming EQ before leaving the outgoing deck, with actual visual read-back.
        self.dispatch({'command':'eqReset','deck':incoming,'bands':['low']})
        require_neutral(self.observe(),decks=(incoming,))
        self.fade(float(incoming-1),duration/4,expected)
        controller.playback(active,False)
        after = self.reset_eq()
        if self.deck(after,incoming)['playingIndicator'] is not True:
            raise MixBlocked('Overnemend deck speelt niet bevestigd.')
        self.record('transition_completed',active=incoming,title=self.deck(after,incoming)['title'])
        return incoming

    def run(self):
        try:
            active = self.start()
            self.played.add(self.deck(self.observe(),active)['library']['file'])
            while True:  # Stop button interrupts; a completed transition does not end the session.
                selected,duration = self.prepare(active)
                active = self.transition(active,duration)
                self.played.add(selected['file'])
        except KeyboardInterrupt:
            self.record('session_stopped',note='No further commands; playing audio is left running.')
            raise
        except Exception as error:
            self.record('session_blocked',error=str(error))
            raise


def run():
    Session().run()
