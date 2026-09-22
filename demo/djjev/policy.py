"""Independent Jev questions over one observed DJ state. No physical controls."""
from copy import deepcopy
import time
from .state import BANDS, closed as _closed, cross_closed as _cross_closed, cue_offset, number, text
from .musical_timing import context as musical_timing

PITCH = {'C':0,'B#':0,'C#':1,'Db':1,'D':2,'D#':3,'Eb':3,'E':4,'Fb':4,'F':5,
         'E#':5,'F#':6,'Gb':6,'G':7,'G#':8,'Ab':8,'A':9,'A#':10,'Bb':10,'B':11,'Cb':11}
DURATIONS = {'beats2':2, 'beats4':4, 'beats8':8, 'beats16':16}
PACING = ('Read musical_timing: this is a flowing musical set, not a rapid test. '
         'Load and prepare the successor early, but readiness is not a reason to PLAY it immediately. '
         'Keep a suitable prepared successor; do not keep replacing it while waiting. '
         'After preparation and EQ cleanup, HOLD while seconds_until_preferred_launch_window is positive. '
         'Within that approximate late-track window, prefer an available near_16_bar_group hint for PLAY. '
         'The hint is grid arithmetic, not a detected phrase; if unknown, use the time window and the native '
         'next-bar launch instead of waiting for nonexistent phrase evidence. '
         'Prefer a gradual audible blend around 32 bars, or 16 bars when the remaining time calls for it. '
         'Those are style preferences, not mandatory delays. A muted running successor is not an audible blend. '
         'Once started and aligned, MIX toward center to establish the blend. Then HOLD the center blend '
         'between useful bass/fader changes; do not immediately jump to the incoming endpoint. '
         'Use confirmed_audible_overlap_seconds and seconds_to_preferred_overlap, never the HOLD count. '
         'Shorten the remaining blend if seconds_to_preferred_overlap exceeds '
         'overlap_time_available_before_finish_seconds; reserve that final margin for completing the handoff. '
         'When ending_needs_priority is true, or the outgoing remaining time is running short, '
         'start/progress/finish sooner and shorten the blend. Never wait for the style target or a '
         '16-bar hint at the cost of continuity. Unknown overlap timing is not a reason to wait forever. '
         'A silent set must still start promptly; a completed handoff must still get EQ and deck cleanup promptly. ')
GOAL = ('You are DJ Jev. Play a continuous EDM set from folder 26. Choose the music, preparation, '
        'mix timing and simultaneous mixer gestures yourself. There is no fixed phase sequence. '
        'Start music promptly when silent. Prepare the successor early while letting the current '
        'track develop. Complete a safe handoff before it ends, restore EQ to neutral afterwards, '
        'then reuse the freed deck and continue. Complement bass: as one deck loses bass, the other '
        'may gain it, never boost above neutral. Incoming bass reduction is temporary during overlap; '
        'restore the new audible track to neutral bass and neutral trim/high/mid by the handoff. '
        'Reaching its fader endpoint with its bass still reduced is unfinished EQ work. Restore the '
        'new audible deck as well as the old deck before loading again or waiting. '
        'HOLD means briefly maintain the current music. '
        'Do not repeatedly hold while silent or postpone preparation until the ending. '
        'A prepared stopped successor is not yet mixing: PLAY starts it while its route stays muted. '
        'Start it early enough to confirm alignment and finish the handoff before the audible track ends. '
        'Do not wait for both decks to align while one is stopped. '
        'After the new track takes over, promptly stop the muted previous track and restore its EQ; '
        'then load a fresh successor onto that freed deck early, before waiting. The previous played '
        'track still sitting on that deck is not the next successor. Loading a stopped closed deck '
        'leaves the audible music playing unchanged. Let the current track develop while selecting, '
        'loading and preparing its successor; musical development does not require delaying LOAD. '
        'Transport MIX selects a mixer gesture; transport HOLD does nothing to any control. '
        'Independent fader, bass and duration questions assume MIX was selected. Their answers '
        'are used only for MIX; fader and bass can move together. A track answer matters only for LOAD. '
        + PACING + 'Only visual state and export metadata are available: do not invent vocals, phrases, '
        'drops, energy or audio loudness. Displayed key is metadata, not verified sounding key. '
        'Bass is visual pointer angle, not dB. Equal BPM alone is not beat alignment.')


def _harmonic(a, b):
    def key(s):
        minor = isinstance(s, str) and s.endswith('m')
        return (PITCH.get(s[:-1] if minor else s), minor)
    a, b = key(a), key(b)
    return a[0] is not None and b[0] is not None and ((b[0]-a[0]) % 12 in (0,5,7)
        if a[1] == b[1] else (a[0]+(3 if a[1] else 9)) % 12 == b[0])


def _non_bass_neutral(deck):
    return all(deck['eq_neutral'].get(band) is True for band in ('trim','high','mid'))


def _launch_ready(snapshot, name):
    deck = snapshot['decks'][name]
    other = snapshot['decks']['B' if name=='A' else 'A']
    return (_cross_closed(snapshot, name) and all(d['channel']>=.9 for d in snapshot['decks'].values())
            and deck['sync'] is True and other['master'] is True
            and number(deck['bpm'],60,200) and number(other['bpm'],60,200)
            and abs(deck['bpm']-other['bpm'])<=.02 and number(deck['cue_offset'],0,2)
            and _non_bass_neutral(deck)
            and _compatible_successor(snapshot,name))


def _compatible_successor(snapshot, name):
    deck=snapshot['decks'][name];other=snapshot['decks']['B' if name=='A' else 'A']
    track=next((t for t in snapshot['library'] if t['id']==deck['track_id']),None)
    return (track is not None and _harmonic(other['key'],deck['key'])
            and number(other['bpm'],1) and number(track.get('bpm'),1)
            and abs((other['bpm']/track['bpm']-1)*100)<=6)


def _ended(deck):
    # The illuminated play button can remain true at track end. Keep that
    # observation intact; remaining time is separate evidence of an ended track.
    return number(deck.get('remaining'), 0, .1)


def _history(history):
    history = history if isinstance(history, dict) else {'actions': history or []}
    recent = list(history.get('recent_tracks', []))
    actions = []
    for entry in history.get('actions', history.get('history', [])):
        if not isinstance(entry, dict) or entry.get('verified') is not True:
            continue
        decision = entry.get('decision', {})
        actions.append({k:decision[k] for k in ('transport','track_id','crossfader','bass','duration_beats') if k in decision})
        if str(decision.get('transport','')).startswith('load_') and isinstance(decision.get('track_id'), str):
            recent.append(decision['track_id'])
    holds = 0
    for action in reversed(actions):
        if any(action.get(k,'hold')!='hold' for k in ('transport','crossfader','bass')):
            break
        holds += 1
    return recent[-12:], actions[-8:], holds


def _transition(snapshot, history):
    anchor=history.get('transition') if isinstance(history,dict) else None
    if not isinstance(anchor,dict) or anchor.get('source')!='verified_silent_successor_start':
        return None
    incoming,outgoing=anchor.get('incoming'),anchor.get('outgoing')
    identity={n:{'title':d['title'],'track_id':d['track_id']} for n,d in snapshot['decks'].items()}
    if {incoming,outgoing}!={'A','B'} or anchor.get('identities')!=identity:
        return None
    decks=snapshot['decks']
    return {**deepcopy(anchor), 'outgoing_remaining_seconds':decks[outgoing]['remaining'],
        'incoming_playing':decks[incoming]['playing'], 'outgoing_playing':decks[outgoing]['playing'],
        'incoming_route_open':not _closed(snapshot,incoming),
        'handoff_endpoint_reached':_cross_closed(snapshot,outgoing),
        'outgoing_eq_neutral':all(decks[outgoing]['eq_neutral'].get(b) is True for b in BANDS),
        'incoming_eq_neutral':all(decks[incoming]['eq_neutral'].get(b) is True for b in BANDS),
        'incoming_non_neutral_bands':[b for b in BANDS if decks[incoming]['eq_neutral'].get(b) is False]}


def prepare(snapshot, history=None, busy=False):
    if not snapshot.get('valid') or not 0<=time.monotonic_ns()-snapshot['captured_ns']<=3_000_000_000:
        raise ValueError(snapshot.get('error', 'No valid observation.'))
    decks = snapshot['decks']; playing = [n for n in decks if decks[n]['playing']]
    recent, last_actions, hold_streak = _history(history)
    transition=_transition(snapshot,history)
    loaded = {d['track_id'] for d in decks.values()}
    audible = [n for n in playing if not _ended(decks[n]) and not _closed(snapshot, n)]
    reference = decks[audible[0]] if len(audible) == 1 else None
    timing = musical_timing(snapshot, transition, audible)
    candidates = []
    for track in snapshot['library']:
        if (track.get('folder') != '26' or track['id'] in loaded or track['id'] in recent
                or track['file'] in recent or track['title'] in recent or not number(track.get('bpm'), 1)
                or not _harmonic(track.get('key'), track.get('key'))):
            continue
        if sum(text(t['title'])==text(track['title']) for t in snapshot['library']) != 1:
            continue
        if reference and (not number(reference['bpm'], 1) or not _harmonic(reference['key'], track['key'])
                or abs((reference['bpm']/track['bpm']-1)*100)>6 or cue_offset(track, reference['bpm']) is None):
            continue
        candidates.append(track)
    transport = {'hold': 'Do nothing to any control this turn. A stopped successor stays stopped; HOLD does not start a mix or perform a mixer gesture. Wait only with sufficient continuity margin and after completed-handoff cleanup.'}
    if not busy:
        for name, deck in decks.items():
            other_name = 'B' if name=='A' else 'A'; other = decks[other_name]
            closed = _closed(snapshot, name)
            if not deck['playing'] and (deck['track_id'] is None or closed or not playing) and candidates:
                transport['load_'+name] = f'Load the separately chosen track onto stopped deck {name}; this does not start playback. When silent prefer the open route.'
                if closed:
                    transport['load_'+name] += ' This closed deck can receive a fresh successor early while the current music keeps playing and developing.'
                if deck['track_id'] is not None:
                    transport['load_'+name] += ' This replaces the track already waiting on this deck.'
            if deck['track_id'] is None:
                continue
            neutral = all(deck['eq_neutral'].get(b) is True for b in BANDS)
            if any(deck['eq_neutral'].get(b) is False for b in BANDS) and (closed or not other['playing'] or _closed(snapshot, other_name)):
                transport['reset_'+name] = (f'Restore all EQ and trim of {name} to neutral. '
                    'If this is the muted previous track after the handoff, reset it now before waiting.')
                if name in audible and (not other['playing'] or _closed(snapshot,other_name)):
                    bands=[b for b in BANDS if deck['eq_neutral'].get(b) is False]
                    transport['reset_'+name] = (f'Restore current audible deck {name} to neutral EQ/trim; '
                        f'its non-neutral bands are {", ".join(bands)}. An audible successor must regain '
                        'full neutral bass after the overlap. Finish this before loading again or waiting.')
                if not deck['playing'] and other['playing'] and number(deck['bass'],-1,-.05):
                    transport['reset_'+name] += ' If this is instead the prepared successor before its start, resetting would remove its incoming bass reduction.'
            if deck['playing']:
                if _ended(deck):
                    transport['stop_'+name] = f'Stop transport at the confirmed track end on {name} so the set can start fresh music. Its play indicator is still on, but observed remaining time is at most 0.1 second.'
                elif closed and other['playing'] and not _ended(other) and not _closed(snapshot, other_name):
                    transport['stop_'+name] = (f'Stop silent deck {name}; {other_name} continues. '
                        'After a completed handoff, stop the muted previous track now before waiting. '
                        'Do not stop a newly started successor that is still waiting to be mixed in.')
                continue
            ended = number(deck['remaining'], 0, .5)
            if not playing and deck['channel'] >= .9 and neutral and not ended:
                transport['play_'+name] = f'Start ready deck {name} to open or resume the set. Both decks are stopped; if its crossfader route is closed, open that route before starting.'
            if (other['playing'] and not _ended(other) and not _closed(snapshot, other_name)
                    and all(d['channel']>=.9 for d in decks.values())):
                compatible=_compatible_successor(snapshot,name)
                staged = (_cross_closed(snapshot,name) and deck['sync'] is True and other['master'] is True
                    and number(deck['bpm'], 60, 200) and number(other['bpm'], 60, 200)
                    and abs(deck['bpm']-other['bpm']) <= .02 and number(deck['bass'], -1, -.05)
                    and _non_bass_neutral(deck))
                if staged and _launch_ready(snapshot,name) and not ended:
                    transport['play_'+name] = (f'Start already prepared successor {name} now on the playing beat grid, '
                        f'while its route stays muted and {other_name} continues. Both decks must be playing '
                        'before alignment and fader mixing can follow. This action does not move faders.')
                elif (not staged and (not ended or not _cross_closed(snapshot,name))
                      and (compatible or not _cross_closed(snapshot,name))):
                    transport['prepare_'+name] = f'Sync, mute, restore trim/high/mid to neutral and prepare reduced bass of stopped incoming {name}; the other continues. Preserve bass that is already reduced.'
                    if not compatible:
                        transport['prepare_'+name] = f'Close stopped incompatible deck {name} so an eligible replacement can be loaded; {other_name} continues. Do not start this incompatible track.'
    aligned = (len(playing)==2 and not any(_ended(d) for d in decks.values()) and snapshot['mixer']['aligned'] is True
               and all(number(d['bpm'], 1) for d in decks.values())
               and abs(decks['A']['bpm']-decks['B']['bpm'])<=.02)
    if not busy and len(playing)==2 and not any(_ended(d) for d in decks.values()) and not aligned:
        for name in decks:
            if _launch_ready(snapshot,name):
                transport['align_'+name] = f'Realign inaudible deck {name} while the other keeps playing.'
    questions = {'transport': {'type':'choice', 'instructions':
        PACING + 'Which transport action should happen now to keep this set flowing musically and continuously? '
        'All elapsed and remaining times are seconds. Read continuity and prepared_silent_decks. '
        'A stopped prepared successor must be started while muted before a fader handoff is possible. '
        'As seconds_until_silence_if_unchanged runs down, prioritize starting/alignment/handoff over '
        'more waiting, replacing an already prepared successor or undoing its bass reduction. '
        'When transition is known, its outgoing_remaining_seconds is the deadline to finish that handoff, '
        'even when the incoming track prevents total silence. When handoff_endpoint_reached is true, '
        'incoming_eq_neutral must also become true: restore the new audible deck if its bass or other '
        'bands are still reduced. The fader endpoint alone does not complete the EQ handoff. '
        'Stop the recorded outgoing deck if still playing and restore its EQ if outgoing_eq_neutral is false. '
        'Once the new audible deck and the stopped previous deck both have neutral EQ, load a fresh successor there before waiting; '
        'the previously played track still loaded there is not the next successor. Loading the closed '
        'inactive deck does not interrupt the current music or prevent it from developing. '
        'Do not infer that repeated HOLD choices have advanced the transition. After a completed '
        'handoff, stop the muted previous track and reset its EQ before waiting.', 'criteria':transport}}
    if any(k.startswith('load_') for k in transport):
        if len(candidates)>255:
            raise ValueError('More than 255 eligible tracks; no shortlist was silently substituted.')
        questions['next_track'] = {'type':'choice', 'instructions':'If loading now, which eligible track best opens or continues this set? Compare all candidate metadata and recent tracks; do not infer arrangement from titles.',
                                  'criteria': {t['id']:None for t in candidates}}
    fader, bass = {'hold':'Leave the crossfader as it is.'}, {'hold':'Keep the current bass balance.'}
    if (not busy and aligned and _harmonic(decks['A']['key'],decks['B']['key'])
            and all(d['channel']>=.9 for d in decks.values())):
        questions['transport']['instructions'] += ' Mixer questions are available now; choose MIX to continue or complete the handoff with their gesture. HOLD leaves every control unchanged.'
        transport['mix'] = 'Continue or complete the transition using the separately chosen crossfader, bass and gesture duration. Both decks keep playing. Choose this when the handoff should progress now.'
        fader.update({target:f'Move the crossfader to {target}. A/B gives that deck the lead; center blends both.'
            for target,pos in (('A',0),('center',.5),('B',1)) if abs(snapshot['mixer']['cross']-pos)>.01})
        if all(number(d['bass'],-1,0) for d in decks.values()):
            bass.update({'A':'Give A neutral bass while reducing B.', 'balanced':'Share the bass reduction across both decks.', 'B':'Give B neutral bass while reducing A.'})
        questions['crossfader'] = {'type':'choice','instructions':'Assume transport MIX is selected. Which crossfader position should this gesture reach? Read musical_timing and transition. Establish center first when the incoming route is still closed and time permits. Keep the center blend while seconds_to_preferred_overlap is substantial; HOLD this fader if only the bass needs changing. Finish toward transition.incoming near the desired overlap duration, or earlier when outgoing remaining time requires it. Do not reverse back to the outgoing endpoint just to make another movement. Without known transition direction, use the current routes and remaining time without inventing a history. Answer this MIX branch independently; it is ignored if MIX is not selected.', 'criteria':fader}
        questions['bass'] = {'type':'choice','instructions':'Assume transport MIX is selected. Which complementary bass balance should this gesture reach? During a long blend, retain the outgoing bass initially, share its reduction through the middle, and give the incoming track neutral bass toward the handoff. Read musical_timing and transition; do not repeatedly swap bass back and forth. The prepared incoming bass cut is temporary; a completed handoff must leave the new audible track at neutral bass, never still reduced. HOLD preserves the current bass values, including any cut, so it does not restore bass when the fader moves. Read transition.incoming and incoming_non_neutral_bands when known. Answer this MIX branch independently; it is ignored if MIX is not selected.', 'criteria':bass}
        questions['duration'] = {'type':'choice','instructions':'Assume transport MIX is selected. How many beats should this one fader/bass gesture last? Prefer smooth 8 or 16 beat gestures when time permits; 2 or 4 beats are for short corrections or an urgent ending. This is not the whole mix duration: a 32-bar blend can include several bounded gestures and HOLD between them. Use outgoing remaining time; this answer is ignored outside MIX.',
                                 'criteria': {k:f'{v} beats for this gesture.' for k,v in DURATIONS.items()}}
    prepared = [n for n in decks if playing and 'play_'+n in transport and _closed(snapshot,n) and not decks[n]['playing']]
    countdown = (max(decks[n]['remaining'] for n in audible)
                 if audible and all(number(decks[n]['remaining'],0) for n in audible) else (None if audible else 0.))
    return {'model':'jev-latest', 'state': {'goal':GOAL, 'snapshot_version':snapshot['version'],
        'captured_ns':snapshot['captured_ns'], 'expected_titles':{n:d['title'] for n,d in decks.items()},
        'decks':deepcopy(decks), 'mixer':deepcopy(snapshot['mixer']), 'busy':bool(busy),
        'time_units':'All deck elapsed/remaining and continuity countdown values are seconds, not beats or bars.',
        'routes':{n:{'muted':_closed(snapshot,n), 'playing_through_open_route':n in audible,
                     'ended':_ended(d)} for n,d in decks.items()},
        'continuity':{'audible_decks':audible, 'seconds_until_silence_if_unchanged':countdown,
            'prepared_silent_decks':prepared, 'mixer_questions_available':'crossfader' in questions,
            'successor_compatible':{n:(_compatible_successor(snapshot,n)
                if ('B' if n=='A' else 'A') in audible and decks[n]['track_id'] else None) for n in decks},
            'audible_non_neutral_bands':{n:[b for b in BANDS if decks[n]['eq_neutral'].get(b) is False] for n in audible},
            'trailing_hold_decisions_in_retained_history':hold_streak,
            'basis':'Visual playback, route positions and remaining clocks; audio loudness is not measured.'},
        'recent_tracks':recent, 'last_actions':last_actions,
        'recent_meaningful_actions':deepcopy(history.get('meaningful_actions',[])) if isinstance(history,dict) else [],
        'transition':transition,
        'musical_timing':timing,
        'candidates':{t['id']:{k:t.get(k) for k in ('title','artist','bpm','key','duration')} for t in candidates}
            if 'next_track' in questions else {}}, 'questions':{} if busy else questions}


def resolve(request, response):
    answers = response.get('answers') if isinstance(response, dict) else None
    if not isinstance(response.get('model') if isinstance(response, dict) else None, str) or not isinstance(answers, dict) or set(answers)!=set(request['questions']):
        raise ValueError('Jev response does not match the requested questions.')
    for name, question in request['questions'].items():
        a=answers[name]; p=a.get('probabilities') if isinstance(a,dict) else None
        if (not isinstance(a,dict) or a.get('type')!='choice' or a.get('choice') not in question['criteria']
                or not number(a.get('confidence'),0,1) or not isinstance(p,dict) or set(p)!=set(question['criteria'])
                or not all(number(v,0,1) for v in p.values()) or abs(sum(p.values())-1)>.03
                or p[a['choice']]+1e-6<max(p.values())):
            raise ValueError('Invalid Jev Choice answer; no replacement choice made.')
    mixing = answers['transport']['choice']=='mix'
    decision = {'expected_titles':deepcopy(request['state']['expected_titles']),
        'snapshot_version':request['state']['snapshot_version'], 'transport':answers['transport']['choice'],
        'crossfader':answers['crossfader']['choice'] if mixing else 'hold',
        'bass':answers['bass']['choice'] if mixing else 'hold',
        'duration_beats':DURATIONS[answers['duration']['choice']] if mixing else 0,
        'answers':deepcopy(answers)}
    if decision['transport'].startswith('load_'):
        decision['track_id']=answers['next_track']['choice']
    return decision


def applicable(decision, snapshot, history=None):
    if (not snapshot.get('valid') or not 0<=time.monotonic_ns()-snapshot['captured_ns']<=3_000_000_000
            or decision.get('expected_titles')!={n:d['title'] for n,d in snapshot['decks'].items()}):
        return False
    try:
        fresh=prepare(snapshot,history,False)['questions']
        for key in ('transport','crossfader','bass'):
            if decision.get(key) not in fresh.get(key,{'criteria':{'hold':None}})['criteria']:
                return False
        if decision['transport'].startswith('load_') and decision.get('track_id') not in fresh['next_track']['criteria']:
            return False
        moving=decision['crossfader']!='hold' or decision['bass']!='hold'
        return (type(decision.get('snapshot_version')) is int and decision['snapshot_version']<=snapshot['version']
                and (not moving or (type(decision['duration_beats']) is int and decision['duration_beats'] in DURATIONS.values()))
                and not (decision['transport']!='mix' and moving))
    except (KeyError,TypeError,ValueError):
        return False
