#!/usr/bin/env python3
"""One bounded, preselected transition demo in Rekordbox, not an autonomous DJ."""
import json
import math
import time
from pathlib import Path
from controller import checked, observe, deck_state, fader, playback, load, sync_tempo

root = Path(__file__).resolve().parents[1]
log = []
started = time.monotonic()
expected = {1: 'No Rules', 2: 'Caribou - Sun (Kastis Torrau & Arnas D Remix)'}

def record(event, **details):
    row = {'seconds': round(time.monotonic()-started, 3), 'event': event, **details}
    log.append(row)
    (root/'evidence/demo-run.json').write_text(json.dumps(log, indent=2)+'\n')
    print(event, details.get('message', ''), flush=True)

def tracks_present(state):
    if state['folder'] != '26':
        raise RuntimeError('Map 26 is niet meer geopend.')
    for deck, title in expected.items():
        if deck_state(state, deck)['title'] != title:
            raise RuntimeError('Geladen track gewijzigd; demo onderbroken.')

try:
    before = observe()
    if any(deck_state(before, d)['playingIndicator'] is not False for d in (1, 2)):
        raise RuntimeError('De demo verwacht twee stilstaande decks; bestaande weergave blijft ongemoeid.')
    load(1, 'Notre Dame - No Rules.mp3')
    state = observe()
    tracks_present(state)
    fader(2, 0.0)
    fader(1, 1.0)
    checked({'command':'crossfader','value':0.5})
    sync_tempo(2)
    record('prepared', state=observe())
    playback(1, True)
    checked({'command':'action','action':'deck1.master','expectedTrack':expected[1]})
    record('track_a_playing', message='No Rules speelt; overgang volgt automatisch.')
    time.sleep(15)
    tracks_present(observe())
    playback(2, True)
    checked({'command':'action','action':'deck2.sync','expectedTrack':expected[2]})
    state = observe()
    tracks_present(state)
    if not all(deck_state(state, d)['playingIndicator'] is True for d in (1, 2)):
        raise RuntimeError('Beide decks spelen niet bevestigd; geen fade gestart.')
    record('overlap_started', state=state)
    for step in range(1, 9):
        state = observe()
        tracks_present(state)
        if not all(deck_state(state,d)['playingIndicator'] is True for d in (1,2)):
            raise RuntimeError('Afspeelstand gewijzigd; fade onderbroken.')
        theta = step/8 * math.pi/2
        # These are fader positions, not measured acoustic/equal-power gain.
        fader(2, round(math.sin(theta), 3))
        state = fader(1, round(math.cos(theta), 3))
        record('fade_step_confirmed', step=step, state=state)
    playback(1, False)
    state = observe()
    tracks_present(state)
    record('transition_completed', state=state,
           message='Caribou speelt alleen verder. Geen verdere automatische overgang gepland.')
    checked({'command':'observe','saveImage':True})
except Exception as error:
    record('interrupted', message=str(error),
           note='Geen blinde herhaling of automatische stop: een spelend deck blijft beschikbaar.')
    raise SystemExit(1)
