"""One-button entry point. Receives the existing credential from its signed host."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import time
import uuid
from . import policy
from .environment import Rekordbox, BRIDGE
from .jev_client import JevClient
from .runner import Runner

ROOT=Path(__file__).resolve().parents[1]
DISPLAY=Path(f'/private/tmp/rekordbox-bridge-{os.getuid()}/widget/evidence')
LABELS={'hold':'Muziek laten lopen','mix':'Overgang voortzetten','load_A':'Nummer laden op A','load_B':'Nummer laden op B',
    'play_A':'Deck A starten','play_B':'Deck B starten','prepare_A':'Deck A voorbereiden',
    'prepare_B':'Deck B voorbereiden','align_A':'Deck A opnieuw inzetten','align_B':'Deck B opnieuw inzetten',
    'stop_A':'Deck A stoppen','stop_B':'Deck B stoppen','reset_A':'EQ A neutraal','reset_B':'EQ B neutraal'}


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    temp.replace(path)


class Display:
    def __init__(self,key):
        self.key=key
        self.run_id=str(uuid.uuid4())
        self.directory=ROOT/'evidence'/self.run_id
        self.directory.mkdir(parents=True)
        self.requests={}
        self.error=None
        self.started=time.time()
        self.last_snapshot=0
        self.blocked=False

    def clean(self,value):
        if isinstance(value,str):return value.replace(self.key,'[verborgen]')
        if isinstance(value,dict):return {k:self.clean(v) for k,v in value.items()}
        if isinstance(value,list):return [self.clean(v) for v in value]
        return value

    def status(self,message,**extra):
        atomic(DISPLAY/'dj-session-status.json',{'message':message,'event':'demo','status':'running',**extra})

    def __call__(self,event):
        event=self.clean(event)
        name=event['event']
        if name=='tick':return
        if name=='snapshot':
            if event.get('valid') is False and not self.blocked:
                self.status('Rekordbox opnieuw uitlezen · '+str(event['snapshot'].get('error','onbekende toestand')))
            event['snapshot']={k:v for k,v in event['snapshot'].items() if k!='library'}
        with (self.directory/'events.jsonl').open('a') as file:
            file.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
        if name=='started':self.status('DJ Jev · Rekordbox uitlezen')
        if name=='request':
            payload=event['request']
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            record={'id':self.run_id+'-'+str(event['request_id']),'started_at':time.time(),'status':'pending',
                'request':{'payload':payload,'payload_id':digest,'provenance':'observed','kind':'jev_demo_request'},'result':None}
            self.requests[event['request_id']]=record
            atomic(DISPLAY/'jev-events'/(record['id']+'.json'),record)
            if not event.get('busy'):self.status('Jev kiest · actuele Rekordbox-toestand')
        if name=='answer':
            record=self.requests.get(event['request_id'])
            if record:
                record.update(status='answered',finished_at=time.time(),
                    result={**event['response'],'payload_id':record['request']['payload_id']})
                atomic(DISPLAY/'jev-events'/(record['id']+'.json'),record)
        if name in ('dispatch','verified'):
            d=event['decision']; labels=[]
            if d.get('transport')!='hold':labels.append(LABELS.get(d['transport'],d['transport']))
            if d.get('crossfader')!='hold':labels.append('Fader naar '+d['crossfader'])
            if d.get('bass')!='hold':labels.append('Bass naar '+d['bass'])
            action=' · '.join(labels) or 'Muziek laten lopen'
            self.status(('Uitvoeren · ' if name=='dispatch' else 'Bevestigd · ')+action)
        if name=='execution_deferred':
            self.status('Opnieuw uitlezen · niets verstuurd · '+str(event.get('message','waarneming gewijzigd')))
        if name=='error':
            self.blocked = self.blocked or event.get('blocked') is True
            self.error=event.get('error') or event.get('message') or event.get('reason') or event.get('error_type')
            self.status(('Bediening onderbroken · ' if event.get('blocked') else 'Opnieuw waarnemen · ')+str(self.error),
                status='blocked' if event.get('blocked') else 'running')
        if name=='stopped':self.status('DJ Jev gestopt · muziek blijft spelen',status='stopped')


async def session(key):
    display=Display(key);env=Rekordbox(native_trace=display);client=JevClient(key)
    runner=Runner(env,policy,client,display)
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,runner.request_stop)
    try:
        await env.start()
        result=await runner.run()
    except Exception as error:
        display({'event':'error','stage':'startup','blocked':True,'error':str(error)})
        await env.stop();await client.close()
        result={'blocked':True,'error':display.error}
    result.update(run_id=display.run_id,duration_seconds=time.time()-display.started,
                  evidence_directory=str(display.directory),last_error=display.error)
    atomic(display.directory/'result.json',display.clean(result))
    return result


def run(key):
    configuration = BRIDGE/'config/live_trial.json'
    if configuration.exists() and json.loads(configuration.read_text()).get('mode') == 'decision_audit':
        from .decision_audit import run_audit
        return asyncio.run(run_audit(key))
    return asyncio.run(session(key))
