"""Rekordbox is the running environment; this adapter contains no DJ policy.

A dedicated observer keeps reading while a bounded control operation executes.
The already signed native app supplies screen/keyboard/mouse primitives only.
"""
import asyncio
import json
import os
from pathlib import Path
import time
from .state import normalize, read_library, number, ENDPOINT_TOLERANCE
from .events import emit, native_parameters, native_result_summary, snapshot_summary

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT.parent if (ROOT.parent/'Sources/Bridge.swift').is_file() else ROOT.parent/'rekordbox-bridge'
SOCKETS = Path(f'/private/tmp/rekordbox-bridge-{os.getuid()}')


class NativePreDispatch(RuntimeError):
    """An explicit native rejection before any input, never an inferred retry."""
    native_pre_dispatch = True

    def __init__(self, message, *, retryable):
        super().__init__(message)
        self.retryable = retryable
        self.commands_sent = False
        self.dispatched = False

    def after_prior_input(self):
        self.retryable = False
        self.commands_sent = True
        self.dispatched = True
        return self


class LocalPreDispatch(RuntimeError):
    """A local state guard failed before any native control call was attempted."""
    local_pre_dispatch = True
    commands_sent = False
    dispatched = False
    retryable = True

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def native_result(reply):
    if reply.get('ok') is not True:
        message = reply.get('error', 'Rekordbox gaf geen bevestiging.')
        if (reply.get('errorKind') == 'pre_dispatch_guard'
                and reply.get('commandsSent') is False
                and type(reply.get('retryable')) is bool):
            raise NativePreDispatch(message, retryable=reply['retryable'])
        raise RuntimeError(message)
    return reply['result']


class Native:
    def __init__(self):
        self.children = []
        self.cancel = SOCKETS / f'stop-{os.getpid()}'
        self.cancel.unlink(missing_ok=True)

    async def call(self, role, command, **parameters):
        reader, writer = await asyncio.open_unix_connection(str(SOCKETS / f'demo-{role}.sock'), limit=4_000_000)
        try:
            writer.write(json.dumps({'command': command, 'clientPID': os.getpid(), **parameters}, allow_nan=False).encode()+b'\n')
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 60 if command == 'loadChosenTrack' else 25)
            reply = json.loads(line)
            return native_result(reply)
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self):
        executable = BRIDGE / 'Rekordbox Bridge.app/Contents/MacOS/rekordbox-bridge'
        environment = {k:v for k,v in os.environ.items() if 'TYPESAFE' not in k.upper() and not k.upper().endswith('API_KEY')}
        for role in ('observer', 'control'):
            try:
                if (await self.call(role, 'status')).get('demoRole') == role:
                    continue
            except (OSError, ValueError, RuntimeError):
                pass
            child = await asyncio.create_subprocess_exec(str(executable), f'--demo-{role}', cwd=BRIDGE,
                env=environment, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            self.children.append((role,child))
            until = time.monotonic()+8
            while time.monotonic() < until:
                try:
                    if (await self.call(role,'status')).get('demoRole') == role:
                        break
                except (OSError, ValueError, RuntimeError):
                    pass
                if child.returncode is not None:
                    raise RuntimeError('De lokale '+role+' kon niet starten.')
                await asyncio.sleep(.1)
            else:
                raise RuntimeError('De lokale '+role+' is niet bereikbaar.')
        await self.call('control','activate')

    async def close(self):
        SOCKETS.mkdir(parents=True, exist_ok=True)
        self.cancel.touch(mode=0o600, exist_ok=True)
        for role, child in self.children:
            try:
                await asyncio.wait_for(self.call(role,'quit'),2)
            except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
                pass
        # No SIGKILL: cancellation is checked before the next native input.


class Rekordbox:
    def __init__(self, native=None, library=None, native_trace=None):
        self.native = native or Native()
        self.library = library if library is not None else read_library()
        self.version = 0
        self.stopping = False
        self.native_trace = native_trace
        self.command_sequence = 0

    async def start(self):
        await self.native.start()

    def snapshot(self, raw):
        self.version += 1
        return normalize(raw, self.library, self.version)

    async def observe(self):
        return self.snapshot(await self.native.call('observer', 'observe', fast=True))

    async def stop(self):
        self.stopping = True
        await self.native.close()

    async def execute(self, decision, snapshot):
        """Apply a model-selected bundle; never choose a successor or mix target."""
        titles = decision['expected_titles']
        expected = {'1':titles['A'],'2':titles['B']}
        state = snapshot
        dispatched = False
        control_attempted = False

        def reject_state(message, code='state_changed'):
            # Only explicit local guard failures qualify. A completed or failed
            # native call, even folder navigation, closes this recovery path.
            if not control_attempted:
                raise LocalPreDispatch(message, code)
            raise RuntimeError(message)

        async def fresh():
            current = await self.observe()
            if not current.get('valid') or any(current['decks'][d]['title'] != titles[d] for d in ('A','B')):
                reject_state('Tracks of waarneming veranderden tijdens de bediening.')
            return current

        async def confirm(predicate, message):
            """Wait for a sent command to render; never send it a second time."""
            nonlocal state
            deadline = time.monotonic()+2.5
            for attempt in range(7):
                if predicate(state):
                    if attempt:
                        emit(self.native_trace, 'native_readback', attempts=attempt,
                             decision_snapshot_version=snapshot.get('version'),
                             state=snapshot_summary(state))
                    return
                if self.stopping:
                    raise asyncio.CancelledError()
                remaining = deadline-time.monotonic()
                if attempt == 6 or remaining <= 0:
                    break
                await asyncio.sleep(min(.05, remaining))
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                try:
                    observed = await asyncio.wait_for(self.observe(), remaining)
                except asyncio.TimeoutError:
                    break
                if not observed.get('valid'):
                    continue
                if any(observed['decks'][d]['title'] != titles[d] for d in ('A','B')):
                    raise RuntimeError('Track veranderde tijdens terugkoppeling; niet herhaald.')
                state = observed
            raise RuntimeError(message)

        async def command(name, **params):
            nonlocal state, dispatched, control_attempted
            if self.stopping:
                raise asyncio.CancelledError()
            if name == 'action':
                action_deck = {'deck1': 'A', 'deck2': 'B'}[params['action'].split('.')[0]]
                params['expectedTrack'] = titles[action_deck]
            self.command_sequence += 1
            trace = {'command_id': self.command_sequence, 'command': name,
                     'decision_snapshot_version': snapshot.get('version'),
                     'expected_titles': dict(titles), 'parameters': native_parameters(params)}
            emit(self.native_trace, 'native_command', phase='started',
                 before=snapshot_summary(state), **trace)
            started = time.monotonic()
            try:
                control_attempted = True
                result = await self.native.call('control', name, expectedTracks=expected,
                    notAfterMonotonicNS=time.monotonic_ns()+55_000_000_000, **params)
            except asyncio.CancelledError:
                emit(self.native_trace, 'native_command', phase='cancelled',
                     seconds=time.monotonic()-started, **trace)
                raise
            except Exception as error:
                if isinstance(error, NativePreDispatch) and dispatched:
                    error.after_prior_input()
                flags = {key: getattr(error, key) for key in
                         ('native_pre_dispatch', 'commands_sent', 'dispatched', 'retryable')
                         if type(getattr(error, key, None)) is bool}
                emit(self.native_trace, 'native_command', phase='error',
                     seconds=time.monotonic()-started, error_type=type(error).__name__,
                     message=str(error), flags=flags, **trace)
                raise
            # Save the native reply BEFORE any verification below can reject it.
            # This is evidence, never permission to retry a physical operation.
            seconds = time.monotonic()-started
            summary = native_result_summary(result)
            if isinstance(result, dict) and isinstance(result.get('after'), dict):
                try:
                    summary['after'] = snapshot_summary(normalize(result['after'], self.library, self.version+1))
                except Exception as error:
                    summary['after_summary_error'] = type(error).__name__
            emit(self.native_trace, 'native_command', phase='returned', seconds=seconds,
                 result=summary, **trace)
            mutating = name not in ('openFolder26','observe','status')
            # Opening the folder can itself send input. Count it for safe retry
            # even though it does not require a mixer after-frame.
            dispatched = dispatched or (name not in ('observe','status') and result.get('dispatched') is not False)
            if mutating and not isinstance(result.get('after'),dict):
                raise RuntimeError('Bediening gaf geen nieuwe waarneming; niet herhaald.')
            if isinstance(result.get('after'),dict):
                state = self.snapshot(result['after'])
                if not state.get('valid'):
                    raise RuntimeError('Resultaat van de bediening is niet leesbaar.')
                if name != 'loadChosenTrack' and any(state['decks'][d]['title'] != titles[d] for d in ('A','B')):
                    raise RuntimeError('Track veranderde tijdens de bediening; niet herhaald.')
            # This primitive verifies the whole gesture, including both
            # transports and red-marker alignment in its after-frame.
            if name == 'mixGesture' and result.get('verified') is not True:
                raise RuntimeError('De volledige mixbeweging is niet bevestigd; niet herhaald.')
            return result

        def closed(s,d):
            return s['decks'][d]['channel'] <= .01 or cross_closed(s,d)

        def cross_closed(s,d):
            value=s['mixer']['cross']
            return value >= 1-ENDPOINT_TOLERANCE if d=='A' else value <= ENDPOINT_TOLERANCE

        async def reset(d, bands=None):
            bands=['low','mid','high','trim'] if bands is None else bands
            result=await command('eqReset',deck=1 if d=='A' else 2,bands=bands)
            if result.get('verified') is not True or not all(state['decks'][d]['eq_neutral'].get(b) is True for b in bands):
                raise RuntimeError('Neutrale EQ is niet bevestigd.')

        async def transport(d,desired,end_only=False):
            result=await command('setPlayback',deck=1 if d=='A' else 2,playing=desired,expectedTrack=titles[d],endOnly=end_only)
            if result.get('verified') is not True or state['decks'][d]['playing'] is not desired:
                raise RuntimeError('Afspeelstand is niet bevestigd.')

        control=decision.get('transport','hold')
        if control!='mix' and any(decision.get(k,'hold')!='hold' for k in ('crossfader','bass')):
            raise ValueError('Mixerdoelen zijn alleen geldig voor de gekozen MIX-tak.')
        if control not in ('hold','mix'):
            kind,d=control.rsplit('_',1)
            n=1 if d=='A' else 2
            other='B' if d=='A' else 'A'
            deck=state['decks'][d]
            if kind=='load':
                if deck['playing'] or (deck['track_id'] and state['decks'][other]['playing'] and not closed(state,d)):
                    reject_state('Doeldeck is hoorbaar of speelt; laden geweigerd.')
                chosen=next(t for t in self.library if t['id']==decision['track_id'])
                await command('openFolder26')
                result=await command('loadChosenTrack',deck=n,file=chosen['file'],
                    replaceStopped=bool(deck['track_id']),expectedTrack=titles[d],
                    allowSilentReplacement=bool(deck['track_id']) and not state['decks'][other]['playing'],
                    expectedOtherTrack=titles[other])
                if (result.get('verified') is not True or state['decks'][d]['track_id']!=chosen['id']
                        or state['decks'][d]['playing'] is not False):
                    raise RuntimeError('Gekozen track is niet bevestigd geladen.')
            elif kind=='prepare':
                if deck['playing'] or not state['decks'][other]['playing']:
                    reject_state('Voorbereiden vereist een gestopt inkomend deck.')
                if not all(v['channel']>=.9 for v in state['decks'].values()):
                    reject_state('Voorbereiden vereist twee open kanaalfaders.')
                if any(type(deck['eq_neutral'].get(b)) is not bool for b in ('trim','high','mid')):
                    reject_state('Trim/high/mid niet leesbaar; voorbereiding wacht op een nieuwe waarneming.')
                if not cross_closed(state,d):
                    result = await command('closeStoppedDeck', deck=n)
                    if (result.get('verified') is not True or not cross_closed(state,d)
                            or state['decks'][d]['playing'] is not False
                            or state['decks'][other]['playing'] is not True):
                        raise RuntimeError('Stilstaand deck niet bevestigd gesloten.')
                bands=[b for b in ('trim','high','mid') if state['decks'][d]['eq_neutral'].get(b) is False]
                if bands:
                    await reset(d,bands)
                if not state['decks'][other]['master']:
                    await command('action',action=f'deck{3-n}.master')
                    await confirm(lambda s: s['decks'][other]['master'] is True,
                                  'Masterwissel niet bevestigd; niet herhaald.')
                if not state['decks'][d]['sync']:
                    await command('action',action=f'deck{n}.sync')
                    await confirm(lambda s: s['decks'][d]['sync'] is True,
                                  'Sync niet bevestigd; niet herhaald.')
                if not cross_closed(state,d):
                    reject_state('Inkomende route is niet gesloten.')
                if state['decks'][d]['bass'] is None:
                    reject_state('Bassknop niet leesbaar.')
                if state['decks'][d]['bass'] > -.05:
                    before=state['decks'][d]['bass']
                    await command('eq',deck=n,band='low',pixels=30.)
                    await confirm(lambda s: number(s['decks'][d]['bass']) and s['decks'][d]['bass'] < before-.04,
                                  'Bassvermindering is niet bevestigd.')
                await confirm(lambda s: s['decks'][d]['sync'] is True and s['decks'][other]['master'] is True
                    and all(s['decks'][d]['eq_neutral'].get(b) is True for b in ('trim','high','mid'))
                    and number(s['decks'][d]['bpm']) and number(s['decks'][other]['bpm'])
                    and abs(s['decks'][d]['bpm']-s['decks'][other]['bpm']) <= .02,
                    'Neutrale trim/high/mid of master/sync is niet bevestigd.')
            elif kind in ('play','align'):
                if state['decks'][other]['playing']:
                    offset=state['decks'][d].get('cue_offset')
                    if not number(offset,0,2):
                        reject_state('Eerste downbeat ontbreekt.')
                    incoming, outgoing = state['decks'][d], state['decks'][other]
                    if (not cross_closed(state,d) or not all(v['channel']>=.9 for v in state['decks'].values())
                            or incoming['sync'] is not True or outgoing['master'] is not True
                            or not all(incoming['eq_neutral'].get(b) is True for b in ('trim','high','mid'))
                            or not number(incoming['bpm'],60,200) or not number(outgoing['bpm'],60,200)
                            or abs(incoming['bpm']-outgoing['bpm'])>.02
                            or number(outgoing['remaining'],0,.1)):
                        reject_state('Inzet vereist gesloten crossfaderroute, open kanalen, neutrale trim/high/mid en bevestigde master/sync/BPM.')
                    if incoming['playing']:
                        await transport(d,False)
                    await command('action',action=f'deck{n}.start')
                    await confirm(lambda s: s['decks'][d]['playing'] is False
                        and number(s['decks'][d]['elapsed'], 0, .2),
                        'Terugkeer naar het trackbegin niet bevestigd; niet gestart.')
                    await command('launchAligned',incoming=n,outgoing=3-n,
                        bpm=float(state['decks'][other]['bpm']),cueOffsetSeconds=float(offset))
                    await confirm(lambda s: s['decks'][d]['playing'] is True, 'Inzet niet bevestigd.')
                else:
                    state=await fresh()
                    def opening_ready(s):
                        selected=s['decks'][d]
                        return (kind=='play' and all(v['playing'] is False for v in s['decks'].values())
                            and selected['track_id'] is not None and selected['channel']>=.9
                            and all(selected['eq_neutral'].get(b) is True for b in ('trim','high','mid','low'))
                            and not number(selected['remaining'],0,.5))
                    if not opening_ready(state):
                        reject_state('Openingsdeck vereist twee gestopte decks, open kanaal, neutrale EQ en een niet beëindigde track.')
                    if cross_closed(state,d):
                        result=await command('openSilentDeck',deck=n)
                        endpoint=0. if d=='A' else 1.
                        if (result.get('verified') is not True or not opening_ready(state)
                                or abs(state['mixer']['cross']-endpoint)>ENDPOINT_TOLERANCE):
                            raise RuntimeError('Openingsroute niet bevestigd terwijl beide decks gestopt zijn; niet gestart.')
                    await transport(d,True)
            elif kind=='stop':
                state=await fresh()
                remaining=state['decks'][d]['remaining']
                ended=type(remaining) in (int,float) and 0<=remaining<=.1
                normal=closed(state,d) and state['decks'][other]['playing']
                if not normal and not ended:
                    reject_state('Hoorbare track wordt niet gestopt.')
                await transport(d,False,end_only=not normal)
            elif kind=='reset':
                if not closed(state,d) and state['decks'][other]['playing'] and not closed(state,other):
                    reject_state('EQ-reset tijdens dubbele hoorbare mix geweigerd.')
                await reset(d)
            else:
                raise ValueError('Onbekende transportkeuze.')

        cross=decision.get('crossfader','hold'); bass=decision.get('bass','hold')
        if cross!='hold' or bass!='hold':
            state=await fresh()
            if not state['mixer']['aligned'] or not all(state['decks'][d]['playing'] for d in ('A','B')):
                reject_state('Beats niet gelijk; geen mixbeweging.', 'alignment_not_confirmed')
            if (not all(number(d['bpm'],1) and d['channel']>=.9 for d in state['decks'].values())
                    or abs(state['decks']['A']['bpm']-state['decks']['B']['bpm'])>.02):
                reject_state('BPM of kanaalstand niet bevestigd; geen mixbeweging.')
            seconds=decision.get('duration_beats',4)*60/state['decks']['A']['bpm']
            target={'A':0.,'center':.5,'B':1.}.get(cross)
            goals={'A':{'A':0.,'B':-.6},'B':{'A':-.6,'B':0.},'balanced':{'A':-.3,'B':-.3}}.get(bass)
            if goals:
                for _ in range(20):
                    if not state['mixer']['aligned'] or not all(state['decks'][d]['playing'] for d in ('A','B')):
                        reject_state('Uitlijning verloren; geen volgende mixbeweging.', 'alignment_not_confirmed')
                    if any(state['decks'][d]['bass'] is None for d in ('A','B')):
                        reject_state('Bassknop niet leesbaar; geen volgende beweging.')
                    lower=[d for d in ('A','B') if state['decks'][d]['bass'] > goals[d]+.07]
                    higher=[d for d in ('A','B') if state['decks'][d]['bass'] < goals[d]-.07]
                    if not lower and not higher:break
                    if lower and higher:
                        down,up=lower[0],higher[0]
                        before={d:state['decks'][d]['bass'] for d in ('A','B')}
                        paired={}
                        if target is not None:
                            fraction=min(1.,4./max(4.,max(abs(before[d]-goals[d]) for d in ('A','B'))*50))
                            paired['crossfader']=state['mixer']['cross']+(target-state['mixer']['cross'])*fraction
                        result=await command('mixGesture',outgoing=1 if down=='A' else 2,
                            incoming=1 if up=='A' else 2,bassPixels=4.,durationSeconds=max(.25,seconds/8),**paired)
                        if (result.get('bassDirectionVerified') is not True
                                or state['decks'][down]['bass'] is None or state['decks'][up]['bass'] is None
                                or state['decks'][down]['bass'] >= before[down]-.01
                                or state['decks'][up]['bass'] <= before[up]+.01):
                            raise RuntimeError('Gekoppelde bassbeweging niet bevestigd.')
                    else:
                        d=(lower or higher)[0];before=state['decks'][d]['bass']
                        await command('eq',deck=1 if d=='A' else 2,band='low',pixels=4. if lower else -4.)
                        await confirm(lambda s: number(s['decks'][d]['bass'])
                            and (before-s['decks'][d]['bass'] if lower else s['decks'][d]['bass']-before)>=.01,
                            'Bassknop reageert niet bevestigd.')
                else:raise RuntimeError('Bassdoel niet bereikt binnen de begrensde beweging.')
                for d in ('A','B'):
                    if goals[d]==0:
                        result=await command('eqReset',deck=1 if d=='A' else 2,bands=['low'])
                        if result.get('verified') is not True or state['decks'][d]['eq_neutral']['low'] is not True:
                            raise RuntimeError('Neutrale bass niet bevestigd.')
                if any(state['decks'][d]['bass'] is None or abs(state['decks'][d]['bass']-goals[d])>.1 for d in ('A','B')):
                    raise RuntimeError('Bassdoel niet bevestigd.')
            if target is not None:
                result=await command('mixGesture',crossfader=target,durationSeconds=max(.25,min(12.,seconds)))
                if result.get('crossfaderVerified') is not True or abs(state['mixer']['cross']-target)>.04:
                    raise RuntimeError('Crossfaderdoel niet bevestigd.')
            state=await fresh()
            if state['mixer']['aligned'] is not True or not all(state['decks'][d]['playing'] is True for d in ('A','B')):
                raise RuntimeError('Uitlijning of afspelen na de mixbeweging niet bevestigd.')
            if target is not None and abs(state['mixer']['cross']-target)>.04:
                raise RuntimeError('Crossfaderdoel na de mixbeweging niet bevestigd.')
            if goals and any(state['decks'][d]['bass'] is None or abs(state['decks'][d]['bass']-goals[d])>.1 for d in ('A','B')):
                raise RuntimeError('Bassdoel na de mixbeweging niet bevestigd.')
        return {'verified':True,'dispatched':dispatched,'snapshot':state}
