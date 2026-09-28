"""Rekordbox is the running environment; this adapter contains no DJ policy.

A dedicated observer keeps reading while a bounded control operation executes.
The already signed native app supplies screen/keyboard/mouse primitives only.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import time
import uuid
from .clock import bridge_ns
from .state import normalize, read_library, number, closed, cross_closed, ENDPOINT_TOLERANCE
from .events import emit, native_parameters, native_result_summary, snapshot_summary
from .audio_timeline import TimelineStore
from .eq import BASS_TARGETS, TONE_TARGETS, position, readable
from .recovery import MixReadbackIncomplete, reconcileable_mix, mixer_state
from .health import HealthBlocked, NativeConnectionError, RecoveryBudget
from .transition_budget import COMPLETION_MARGIN_SECONDS
from .entry_grid import target_current

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
    """Own Bridge roles; recover only after the role lock proves no worker remains."""
    def __init__(self, *, sockets=SOCKETS, bridge=BRIDGE, trace=None, clock=time.monotonic, session_id=None):
        self.children = []
        self.sockets, self.bridge, self.trace, self.clock = sockets, bridge, trace, clock
        self.session_id = str(uuid.UUID(session_id or os.environ.get('DJ_JEV_SESSION_ID') or str(uuid.uuid4())))
        self.cancel = sockets / f'stop-{os.getpid()}-{self.session_id}'
        self.command_sequence = 0
        # A host Stop may arrive before Python has installed its signal handlers.
        # Never erase that fence during startup.
        self.stopping = self.cancel.exists()
        self.control_inhibited = False
        self.generation = 0
        self.statuses = {}
        self.expected_build = None
        self.restarts = RecoveryBudget(3, 'bridge_restarts')
        self.focus = RecoveryBudget(2, 'focus_recovery')
        self._last_health = float('-inf')
        self._last_focus = float('-inf')
        self._role_locks = {role: asyncio.Lock() for role in ('observer', 'control')}
        self._health_lock = asyncio.Lock()

    async def call(self, role, command, **parameters):
        # These controls run before/outside Rekordbox.execute's traced wrapper.
        if command not in ('activate', 'refreshLibrary'):
            return await self._call(role, command, **parameters)
        self.command_sequence += 1
        trace = {'command_id':f'native-{self.command_sequence}', 'command':command,
                 'role':role, 'parameters':native_parameters(parameters)}
        emit(self.trace, 'native_command', phase='started', **trace)
        if getattr(self.trace, 'evidence_fault', False):
            raise HealthBlocked('evidence_unavailable', 'Sessielog niet schrijfbaar; geen invoer verstuurd.')
        started = time.monotonic()
        try:
            result = await self._call(role, command, **parameters)
        except asyncio.CancelledError:
            emit(self.trace, 'native_command', phase='cancelled', seconds=time.monotonic()-started, **trace)
            raise
        except Exception as error:
            flags = {key:getattr(error,key) for key in ('commands_sent','dispatched','retryable')
                     if type(getattr(error,key,None)) is bool}
            emit(self.trace, 'native_command', phase='error', seconds=time.monotonic()-started,
                 error_type=type(error).__name__, message=str(error), flags=flags, **trace)
            raise
        emit(self.trace, 'native_command', phase='returned', seconds=time.monotonic()-started,
             result=native_result_summary(result), **trace)
        return result

    async def _call(self, role, command, **parameters):
        """Send once; a failed or cancelled reply never retries the command."""
        if (self.stopping or role == 'control' and self.control_inhibited) and command not in ('status', 'quit'):
            raise HealthBlocked('stopped', 'Stop gevraagd; geen verdere bediening.')
        async with self._role_locks[role]:
            writer = None
            sent = False
            timeout = (90 if command == 'refreshLibrary' else 60 if command == 'loadChosenTrack'
                       else 8 if command == 'observe' else 2 if command == 'status' else 25)
            async def exchange():
                nonlocal writer, sent
                reader, writer = await asyncio.open_unix_connection(
                    str(self.sockets / f'demo-{role}.sock'), limit=4_000_000)
                if (self.stopping or role == 'control' and self.control_inhibited) and command not in ('status', 'quit'):
                    raise HealthBlocked('stopped', 'Stop gevraagd; geen verdere bediening.')
                payload = json.dumps({'command': command, 'clientPID': os.getpid(),
                                     'clientSessionID':self.session_id, **parameters},
                                     allow_nan=False).encode()+b'\n'
                # Conservatively mark input possible before writing any bytes.
                sent = True
                writer.write(payload)
                await writer.drain()
                line = await reader.readline()
                if not line.endswith(b'\n'):
                    raise ValueError('incomplete native reply')
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise ValueError('invalid native reply')
                return native_result(reply)
            try:
                return await asyncio.wait_for(exchange(), timeout)
            except (OSError, ValueError, KeyError, asyncio.TimeoutError) as error:
                self._last_health = float('-inf')
                raise NativeConnectionError(role, command, sent, error) from error
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), .2)
                    except (OSError, asyncio.TimeoutError):
                        pass

    def role_free(self, role):
        """The native lifetime flock, not a lost socket, proves a worker has exited."""
        self.sockets.mkdir(parents=True, exist_ok=True)
        with (self.sockets / f'demo-{role}.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(lock, fcntl.LOCK_UN)
            return True

    def validate_status(self, role, status):
        if (not isinstance(status, dict) or status.get('demoRole') != role
                or type(status.get('protocolVersion')) is not int or status['protocolVersion'] != 3
                or type(status.get('bridgePID')) is not int):
            raise HealthBlocked('bridge_protocol_mismatch', 'Bridge-rol of protocol klopt niet; bouw de apps opnieuw.')
        if self.expected_build and status.get('sourceDigest') != self.expected_build['source_digest']:
            raise HealthBlocked('bridge_build_mismatch', 'De draaiende Bridge hoort bij een andere build; sluit de oude apps eerst.')
        self.statuses[role] = status
        emit(self.trace, 'bridge_status', role=role, status=status,
             restarts=self.restarts.used, focus_attempts=self.focus.used)
        return status

    async def ensure_role(self, role, *, initial=False):
        try:
            return self.validate_status(role, await self.call(role, 'status'))
        except NativeConnectionError:
            pass
        if self.stopping:
            raise HealthBlocked('stopped', 'Stop gevraagd; herstel afgebroken.')
        if not self.role_free(role):
            raise HealthBlocked('bridge_worker_unresponsive',
                                f'{role} antwoordt niet maar bestaat nog; geen tweede uitvoerder gestart.')
        attempt = 0 if initial else self.restarts.consume()
        emit(self.trace, 'health', phase='restarting', role=role, attempt=attempt)
        executable = self.bridge / 'Rekordbox Bridge.app/Contents/MacOS/rekordbox-bridge'
        environment = {k:v for k,v in os.environ.items() if 'TYPESAFE' not in k.upper() and not k.upper().endswith('API_KEY')}
        child = await asyncio.create_subprocess_exec(str(executable), f'--demo-{role}', cwd=self.bridge,
            env=environment, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self.children.append((role, child))
        validated = False
        try:
            deadline = self.clock()+8
            while not self.stopping and self.clock() < deadline:
                try:
                    status = self.validate_status(role, await self.call(role, 'status'))
                    if status['bridgePID'] != child.pid:
                        raise HealthBlocked('bridge_owner_mismatch', 'Onverwacht Bridge-proces; bediening niet gestart.')
                    validated = True
                    if not initial:
                        self.generation += 1
                    return status
                except NativeConnectionError:
                    if child.returncode is not None:
                        raise HealthBlocked('bridge_start_failed', f'{role} kon niet starten.')
                    await asyncio.sleep(.1)
            raise HealthBlocked('bridge_start_timeout', f'{role} werd niet tijdig bereikbaar.')
        finally:
            if not validated:
                # This owned child has only received status probes, never an
                # authorized physical action. Retire it even if its socket hung.
                try:
                    if child.returncode is None:
                        try: child.terminate()
                        except ProcessLookupError: pass
                    try:
                        await asyncio.wait_for(child.wait(), 2)
                    except asyncio.TimeoutError:
                        try: child.kill()
                        except ProcessLookupError: pass
                        await asyncio.wait_for(child.wait(), 2)
                finally:
                    self.children.remove((role, child))

    @property
    def health_in_progress(self):
        return self._health_lock.locked()

    async def health(self):
        """Check between inputs; never queue health/focus behind a physical action."""
        if self.stopping or self.control_inhibited or self._role_locks['control'].locked():
            return
        if self.clock()-self._last_health < 2:
            return
        async with self._health_lock:
            if self.stopping or self.control_inhibited or self._role_locks['control'].locked():
                return
            self._last_health = self.clock()
            for role in ('observer', 'control'):
                await self.ensure_role(role)
            status = self.statuses['observer']
            if status.get('rekordboxRunning') is not True:
                raise HealthBlocked('rekordbox_exited', 'Rekordbox is afgesloten; bediening onderbroken.')
            if status.get('accessibility') is not True or status.get('screenRecording') is not True:
                raise HealthBlocked('permission_missing', 'Scherm- of bedieningstoestemming ontbreekt.')
            if status.get('rekordboxFrontmost') is not True:
                # Only an active runner calls health. Two attempts per session,
                # spaced apart; status polling outside a set never steals focus.
                if self.clock()-self._last_focus < 5:
                    return
                attempt = self.focus.consume()
                self._last_focus = self.clock()
                emit(self.trace, 'health', phase='focus_recovery', attempt=attempt)
                await self.call('control', 'activate')
                self.generation += 1
                # Do not count an activate acknowledgement as a valid screen.

    async def start(self):
        manifest = self.bridge/'build-info.json'
        if manifest.is_file():
            self.expected_build = json.loads(manifest.read_text())
        for role in ('observer', 'control'):
            await self.ensure_role(role, initial=True)
        await self.call('control', 'activate')

    def inhibit(self):
        """Fence all further input for this session while read-only observation remains."""
        if self.control_inhibited:
            return
        self.control_inhibited = True
        self.sockets.mkdir(parents=True, exist_ok=True)
        self.cancel.touch(mode=0o600, exist_ok=True)

    async def close(self):
        """Fence the session PID before waiting for workers; never pause playback."""
        if self.stopping:
            return
        self.stopping = True
        self.sockets.mkdir(parents=True, exist_ok=True)
        self.cancel.touch(mode=0o600, exist_ok=True)
        for role, child in self.children:
            if child.returncode is not None:
                continue
            try:
                await asyncio.wait_for(self.call(role, 'quit'), 2)
            except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
                pass
        # No SIGKILL: cancellation is checked before the next native input.


class Rekordbox:
    def __init__(self, native=None, library=None, native_trace=None, audio_store=None, *, effects_enabled=False):
        self.native = native or Native(trace=native_trace)
        self.library = library if library is not None else read_library()
        # Cache validation/preload happens before Runner starts. Snapshot lookup
        # is bounded in-memory work, with no Essentia import or file access.
        self.audio_store = audio_store if audio_store is not None else TimelineStore(self.library)
        self.version = 0
        self.stopping = False
        # Effects are paused at the user's request; hardware capability is not authorization.
        self.effects_enabled = effects_enabled is True
        self.last_effect_ns = None
        self.native_trace = native_trace
        self.command_sequence = 0

    async def start(self):
        await self.native.start()

    def snapshot(self, raw):
        self.version += 1
        snapshot = normalize(raw, self.library, self.version)
        try:
            context = self.audio_store.snapshot_context(snapshot)
            if context:
                snapshot['audio_windows'] = context
        except Exception:
            # Optional musical evidence cannot invalidate physical state or
            # authorize an action. Missing analysis retains the old behavior.
            pass
        if snapshot.get('valid'):
            snapshot['effects']['subtle_echo_supported'] &= self.effects_enabled
            snapshot['effects']['cooldown_seconds'] = (max(0.,45-(time.monotonic_ns()-self.last_effect_ns)/1e9)
                if self.last_effect_ns else 0.)
        return snapshot

    @property
    def health_generation(self):
        return getattr(self.native, 'generation', 0)

    async def observe(self):
        if hasattr(self.native, 'health'):
            await self.native.health()
        return self.snapshot(await self.native.call('observer', 'observe', fast=True))

    def inhibit(self):
        if hasattr(self.native, 'inhibit'):
            self.native.inhibit()

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
        blend_confirmation = None
        action_generation = self.health_generation

        def reject_state(message, code='state_changed'):
            # Only explicit local guard failures qualify. A completed or failed
            # native call, even folder navigation, closes this recovery path.
            if not control_attempted:
                raise LocalPreDispatch(message, code)
            raise RuntimeError(message)

        async def fresh():
            try:
                current = await self.observe()
            except NativeConnectionError as error:
                if not control_attempted and error.role == 'observer':
                    raise LocalPreDispatch('Observer tijdelijk niet bereikbaar; niets bediend.',
                                           'observation_unavailable') from error
                raise
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
            if self.health_generation != action_generation or getattr(self.native, 'health_in_progress', False):
                reject_state('Verbinding of focus hersteld tijdens de actie; nieuwe Jev-keuze nodig.', 'health_changed')
            if name == 'action':
                action_deck = {'deck1': 'A', 'deck2': 'B'}[params['action'].split('.')[0]]
                params['expectedTrack'] = titles[action_deck]
            self.command_sequence += 1
            trace = {'command_id': self.command_sequence, 'command': name,
                     'decision_snapshot_version': snapshot.get('version'),
                     'expected_titles': dict(titles), 'parameters': native_parameters(params)}
            emit(self.native_trace, 'native_command', phase='started',
                 before=snapshot_summary(state), **trace)
            if getattr(self.native_trace, 'evidence_fault', False):
                raise HealthBlocked('evidence_unavailable', 'Sessielog niet schrijfbaar; geen invoer verstuurd.')
            started = time.monotonic()
            try:
                control_attempted = True
                result = await self.native.call('control', name, expectedTracks=expected,
                    notAfterMonotonicNS=bridge_ns()+55_000_000_000, **params)
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
                if reconcileable_mix(result, state, titles):
                    raise MixReadbackIncomplete(state, result['verification']['reasons'])
                raise RuntimeError('De volledige mixbeweging is niet bevestigd; niet herhaald.')
            return result

        def check_completion_margin():
            outgoing=decision.get('completion_outgoing')
            if outgoing not in ('A','B') or not number(state['decks'][outgoing]['remaining'],0,COMPLETION_MARGIN_SECONDS):
                return
            if not control_attempted:
                reject_state('Afronden heeft nu voorrang op verdere EQ-beweging.', 'completion_margin_reached')
            if mixer_state(state,titles) is not None:
                raise MixReadbackIncomplete(state,['completion_margin_reached'])
            raise RuntimeError('Afrondtijd bereikt maar mixer niet opnieuw leesbaar.')

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
        if control!='mix' and any(decision.get(k,'hold')!='hold' for k in ('crossfader','bass','mid','high')):
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
                if number(state['decks'][d].get('remaining'), 0, .5):
                    # An ended track is closed only to make room for a replacement.
                    # Do not sync or EQ it: it cannot be launched as a successor.
                    return {'verified':True,'dispatched':dispatched,'snapshot':state}
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
                    target = decision.get('entry_target') if kind == 'play' else None
                    if target and (target.get('incoming') != d or target.get('outgoing') != other
                                   or not target_current(target,state)):
                        reject_state('Gekozen inzetpunt gewijzigd of gemist; nieuwe keuze nodig.', 'entry_target_expired')
                    if (not cross_closed(state,d) or not all(v['channel']>=.9 for v in state['decks'].values())
                            or incoming['sync'] is not True or outgoing['master'] is not True
                            or not all(incoming['eq_neutral'].get(b) is True for b in ('trim','high','mid'))
                            or not number(incoming['bpm'],60,200) or not number(outgoing['bpm'],60,200)
                            or abs(incoming['bpm']-outgoing['bpm'])>.02
                            or number(outgoing['remaining'],0,.1)):
                        reject_state('Inzet vereist gesloten crossfaderroute, open kanalen, neutrale trim/high/mid en bevestigde master/sync/BPM.')
                    if incoming['playing']:
                        await transport(d,False)
                    # A prepared stopped deck already at zero needs no rewind,
                    # whether launching at an explicit target or the next bar.
                    # Otherwise verify the
                    # rewind once and never retry it to rescue a missed target.
                    if kind != 'play' or incoming['playing'] is not False or not number(incoming['elapsed'],0,.05):
                        await command('action',action=f'deck{n}.start')
                        await confirm(lambda s: s['decks'][d]['playing'] is False
                            and number(s['decks'][d]['elapsed'], 0, .2),
                            'Terugkeer naar het trackbegin niet bevestigd; niet gestart.')
                    planned = {}
                    if target:
                        state = await fresh()
                        if not target_current(target,state):
                            reject_state('Inzetpunt tijdens voorbereiding gemist; geen verlate Play.', 'entry_target_expired')
                        planned['targetBeatMonotonicNS'] = target['beat_monotonic_ns']
                        planned['targetOutgoingRemainingSeconds'] = target['outgoing_remaining_at_target']
                        planned['targetOutgoingTempoRatio'] = target['tempo_ratio']
                    await command('launchAligned',incoming=n,outgoing=3-n,
                        bpm=float(state['decks'][other]['bpm']),cueOffsetSeconds=float(offset),**planned)
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
            elif kind=='echo':
                state=await fresh()
                deck=state['decks'][d]
                if (not state.get('effects',{}).get('subtle_echo_supported')
                        or state['effects'].get('cooldown_seconds',45)>0
                        or deck['playing'] is not True or closed(state,d)
                        or any(not number(x['remaining'],20.001) for x in state['decks'].values() if x['playing'])
                        or not number(deck['remaining'],20.001) or not number(deck['bpm'],60,200)):
                    reject_state('Geen ruimte voor een subtiel effect op dit deck.')
                try:
                    result=await command('echoAccent',deck=n,bpm=deck['bpm'])
                finally:
                    # A lost reply cannot prove that this transient effect never ran.
                    self.last_effect_ns=time.monotonic_ns()
                if result.get('verified') is not True or result.get('effectOffVerified') is not True:
                    raise RuntimeError('Echo of automatisch uitschakelen niet bevestigd.')
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
        if any(decision.get(k,'hold')!='hold' for k in ('crossfader','bass','mid','high')):
            state=await fresh()
            if not state['mixer']['aligned'] or not all(state['decks'][d]['playing'] for d in ('A','B')):
                reject_state('Beats niet gelijk; geen mixbeweging.', 'alignment_not_confirmed')
            if (not all(number(d['bpm'],1) and d['channel']>=.9 for d in state['decks'].values())
                    or abs(state['decks']['A']['bpm']-state['decks']['B']['bpm'])>.02):
                reject_state('BPM of kanaalstand niet bevestigd; geen mixbeweging.')
            seconds=decision.get('duration_beats',4)*60/state['decks']['A']['bpm']
            target={'A':0.,'center':.5,'B':1.}.get(cross)
            goals=BASS_TARGETS.get(bass)
            # Open an already prepared, bass-reduced route promptly when Jev
            # explicitly chose center. EQ work must not leave this first blend
            # silent for an entire sequence of knob/readback operations.
            # Unprepared routes and endpoint transfers retain their old order.
            center_first = target == .5 and any(
                cross_closed(state,d) and number(state['decks'][d]['bass'],-1.,-.24)
                and all(state['decks'][d]['eq_neutral'].get(b) is True for b in ('trim','high','mid'))
                for d in ('A','B'))
            if center_first:
                result=await command('mixGesture',crossfader=target,durationSeconds=max(.25,min(12.,seconds)))
                if result.get('crossfaderVerified') is not True or abs(state['mixer']['cross']-target)>.04:
                    raise RuntimeError('Eerste middenstand niet bevestigd; geen volgende EQ-beweging.')
                blend_confirmation=snapshot_summary(state)
                state=await fresh()
                if (state['mixer']['aligned'] is not True
                        or not all(d['playing'] is True and number(d['bpm'],60,200)
                                   and d['channel']>=.9 for d in state['decks'].values())
                        or abs(state['decks']['A']['bpm']-state['decks']['B']['bpm'])>.02
                        or abs(state['mixer']['cross']-target)>.04):
                    reject_state('Middenstand, afspelen of uitlijning gewijzigd; geen volgende EQ-beweging.')
            if goals:
                for _ in range(20):
                    check_completion_margin()
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
                        if target is not None and not center_first:
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
                        result=await command('eq',deck=1 if d=='A' else 2,band='low',pixels=4. if lower else -4.)
                        try:
                            await confirm(lambda s: number(s['decks'][d]['bass'])
                                and (before-s['decks'][d]['bass'] if lower else s['decks'][d]['bass']-before)>=.01,
                                'Bassknop reageert niet bevestigd.')
                        except RuntimeError:
                            # Only a completed native stroke with a fully readable
                            # mixer can be reconciled. Never retry this relative input.
                            if (result.get('dispatched') is True and result.get('commandsSent') is True
                                    and mixer_state(state,titles) is not None):
                                raise MixReadbackIncomplete(state,['bass_direction_not_confirmed'])
                            raise
                else:raise RuntimeError('Bassdoel niet bereikt binnen de begrensde beweging.')
                for d in ('A','B'):
                    if goals[d]==0 and state['decks'][d]['eq_neutral']['low'] is not True:
                        result=await command('eqReset',deck=1 if d=='A' else 2,bands=['low'])
                        if result.get('verified') is not True or state['decks'][d]['eq_neutral']['low'] is not True:
                            raise RuntimeError('Neutrale bass niet bevestigd.')
                if any(state['decks'][d]['bass'] is None or abs(state['decks'][d]['bass']-goals[d])>.1 for d in ('A','B')):
                    raise RuntimeError('Bassdoel niet bevestigd.')
            for band in ('mid', 'high'):
                tone = TONE_TARGETS.get(decision.get(band, 'hold'))
                if not tone:
                    continue
                for d, goal in tone.items():
                    for step in range(12):
                        state = await fresh()
                        check_completion_margin()
                        if (state['mixer']['aligned'] is not True
                                or not all(deck['playing'] for deck in state['decks'].values())
                                or not readable(state['decks'], band)):
                            reject_state('EQ-toestand of uitlijning niet bevestigd.')
                        before = position(state['decks'][d], band)
                        if abs(before-goal) <= .07 and (goal != 0 or state['decks'][d]['eq_neutral'][band] is True):
                            break
                        if goal == 0 and before >= -.09:
                            await reset(d, [band])
                        else:
                            await command('eq', deck=1 if d=='A' else 2, band=band,
                                          pixels=4. if before>goal else -4.)
                            await confirm(lambda s: number(position(s['decks'][d], band))
                                and (before-position(s['decks'][d], band) if before>goal
                                     else position(s['decks'][d], band)-before) >= .01,
                                'EQ-beweging niet bevestigd.')
                        # Spread small steps; native capture/control time also consumes the budget.
                        if abs(position(state['decks'][d], band)-goal) > .07:
                            await asyncio.sleep(min(.25, seconds/8))
                    else:
                        raise RuntimeError('EQ-doel niet bereikt binnen de begrensde beweging.')
                    if abs(position(state['decks'][d], band)-goal) > .1:
                        raise RuntimeError('EQ-doel niet bevestigd.')
            if target is not None and not center_first:
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
            for band in ('mid', 'high'):
                tone = TONE_TARGETS.get(decision.get(band, 'hold'))
                if tone and any(not number(position(state['decks'][d], band))
                        or abs(position(state['decks'][d], band)-goal)>.1
                        or (goal == 0 and state['decks'][d]['eq_neutral'][band] is not True)
                        for d, goal in tone.items()):
                    raise RuntimeError('EQ-doel na de mixbeweging niet bevestigd.')
        result={'verified':True,'dispatched':dispatched,'snapshot':state}
        if blend_confirmation is not None:
            result['blend_confirmation']=blend_confirmation
        return result
