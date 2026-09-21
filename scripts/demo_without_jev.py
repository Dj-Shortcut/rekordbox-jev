"""Assistant-directed control demonstration. No Jev import, API or credentials."""
import json
from pathlib import Path
import time
import controller
import music_context

ROOT = Path(__file__).resolve().parents[1]
LOG = {'mode':'assistant-directed without Jev','actions':[]}

def observe():
    raw = controller.checked({'command':'observe','saveImage':True})
    return music_context.enrich(controller.normalized_state(raw),music_context.library(),music_context.read_frame())

def record(action, state=None):
    LOG['actions'].append({'action':action,'time':time.time(),'state':state})
    (ROOT/'evidence/without-jev-demo.json').write_text(json.dumps(LOG,ensure_ascii=False,indent=2)+'\n')
    print(action,flush=True)

try:
    print('Wachten op zichtbaar Rekordbox-venster…',flush=True)
    deadline = time.monotonic()+60
    while True:
        if time.monotonic()>deadline:
            raise RuntimeError('Rekordbox bleef onbereikbaar; demonstratie niet gestart.')
        if controller.checked({'command':'status'})['rekordboxFrontmost']:
            try:
                state = observe()
                if controller.deck_state(state,1)['title']=='No Rules' and controller.deck_state(state,2)['title']=='Caribou - Sun (Kastis Torrau & Arnas D Remix)':
                    break
            except RuntimeError:
                pass
        time.sleep(.3)
    # Set up the demonstration explicitly; these are assistant choices, not AI inference.
    controller.playback(2,False)
    controller.checked({'command':'crossfader','value':0.0})
    controller.playback(1,False)
    controller.checked({'command':'action','action':'deck1.start','expectedTrack':'No Rules'})
    controller.checked({'command':'action','action':'deck2.start','expectedTrack':state['decks'][1]['title']})
    controller.checked({'command':'action','action':'deck1.master','expectedTrack':'No Rules'})
    record('No Rules gestart',controller.playback(1,True))
    state=observe()
    if state['mixer']['beat_sync_lit']['2'] is not True:
        controller.checked({'command':'action','action':'deck2.sync','expectedTrack':state['decks'][1]['title']})
        state=observe()
    assert state['mixer']['beat_sync_lit']['2'] is True,'Beat Sync niet bevestigd'
    assert state['mixer']['crossfader_position']<.05,'Crossfader niet links'
    assert all(d['fader']>.9 for d in state['decks']),'Kanaalfaders niet open'
    record('Sun gestart met Beat Sync',controller.playback(2,True))
    start=time.monotonic()
    for step in range(1,21):
        time.sleep(max(0,start+step*.8-time.monotonic()))
        controller.checked({'command':'crossfader','value':step/20})
        if step%5==0:
            state=observe()
            record('Crossfader '+str(step*5)+'%',state)
            assert abs(state['mixer']['crossfader_position']-step/20)<.08,'Crossfader niet bevestigd'
    state=observe()
    assert controller.deck_state(state,2)['playingIndicator'] is True
    assert state['mixer']['crossfader_position']>.95
    record('No Rules gepauzeerd na overname',controller.playback(1,False))
    LOG['after']=observe()
    LOG['status']='controls_verified'
    LOG['audio_quality']='Not independently heard or measured'
    record('Overgang uitgevoerd zonder Jev; Sun speelt verder')
except Exception as error:
    LOG['status']='failed';LOG['error']=str(error)
    record('Gestopt: '+str(error))
    raise SystemExit(1)
