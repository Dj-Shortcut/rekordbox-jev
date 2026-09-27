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
from .evidence import SessionEvidence
from .build_info import validate_installation
from .environment import Rekordbox, BRIDGE
from .jev_client import JevClient
from .runner import Runner
from .startup import prepare_library
from .audio_timeline import TimelineStore

ROOT=Path(__file__).resolve().parents[1]
DISPLAY=Path(f'/private/tmp/rekordbox-bridge-{os.getuid()}/widget/evidence')
LABELS={'hold':'Muziek laten lopen','mix':'Overgang voortzetten','load_A':'Nummer laden op A','load_B':'Nummer laden op B',
    'play_A':'Deck A starten','play_B':'Deck B starten','prepare_A':'Deck A voorbereiden',
    'prepare_B':'Deck B voorbereiden','align_A':'Deck A opnieuw inzetten','align_B':'Deck B opnieuw inzetten',
    'echo_A':'Kort echo-accent op A', 'echo_B':'Kort echo-accent op B',
    'stop_A':'Deck A stoppen','stop_B':'Deck B stoppen','reset_A':'EQ A neutraal','reset_B':'EQ B neutraal'}


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    temp.replace(path)


class Display:
    def __init__(self,key):
        self.key=key
        supplied = os.environ.get('DJ_JEV_SESSION_ID')
        self.run_id=str(uuid.UUID(supplied)) if supplied else str(uuid.uuid4())
        self.directory=ROOT/'evidence'/self.run_id
        self.directory.mkdir(parents=True)
        self.requests={}
        self.error=None
        self.started=time.time()
        self.last_snapshot=0
        self.blocked=False
        self.evidence = SessionEvidence(self.directory, self.run_id, key)
        self.evidence_fault = False

    def clean(self,value):
        if isinstance(value,str):return value.replace(self.key,'[verborgen]')
        if isinstance(value,dict):return {k:self.clean(v) for k,v in value.items()}
        if isinstance(value,list):return [self.clean(v) for v in value]
        return value

    def status(self,message,**extra):
        atomic(DISPLAY/'dj-session-status.json',{'message':message,'event':'demo','status':'running',
            'run_id':self.run_id, 'build':self.evidence.state['build'], **extra})

    def __call__(self,event):
        event=self.clean(event)
        name=event['event']
        if name=='tick':return
        if name=='snapshot':
            if event.get('valid') is False and not self.blocked:
                self.status('Rekordbox opnieuw uitlezen · '+str(event['snapshot'].get('error','onbekende toestand')))
            # Requests already record the exact compact audio evidence Jev saw.
            event['snapshot']={k:v for k,v in event['snapshot'].items() if k not in ('library','audio_windows')}
        try:
            self.evidence.record(event)
        except OSError:
            self.evidence_fault = True
            raise
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
        if name in ('dispatch','verified','ignored','execution_deferred','execution_reconciling','execution_reconciled','error'):
            record=self.requests.get(event.get('request_id'))
            if record:
                if name == 'ignored' and record.get('status') == 'pending':
                    record.update(status='cancelled', finished_at=time.time())
                # Keep execution evidence attached to its own request; the
                # global session banner may already describe a newer decision.
                record['execution']={'status':name,'updated_at':time.time(),
                    **{key:event[key] for key in ('decision','reason','message','code','commands_sent','dispatched')
                       if key in event}}
                if name=='verified':
                    result=event.get('result',{})
                    record['execution'].update(verified=result.get('verified') is True,
                        dispatched=result.get('dispatched') is True)
                atomic(DISPLAY/'jev-events'/(record['id']+'.json'),record)
        if name in ('dispatch','verified') and not self.blocked:
            d=event['decision']; labels=[]
            if d.get('transport')!='hold':labels.append(LABELS.get(d['transport'],d['transport']))
            if d.get('crossfader')!='hold':labels.append('Fader naar '+d['crossfader'])
            if d.get('bass')!='hold':labels.append('Bass naar '+d['bass'])
            for band, label in (('mid','Midden'),('high','Hoog')):
                if d.get(band,'hold')!='hold':labels.append(label+' aanpassen')
            action=' · '.join(labels) or 'Muziek laten lopen'
            self.status(('Uitvoeren · ' if name=='dispatch' else 'Bevestigd · ')+action)
        if name=='execution_deferred':
            self.status('Opnieuw uitlezen · niets verstuurd · '+str(event.get('message','waarneming gewijzigd')))
        if name in ('execution_reconciling','execution_reconciled'):
            self.status(str(event['message']))
        if name=='error':
            was_blocked = self.blocked
            self.blocked = self.blocked or event.get('blocked') is True
            if not was_blocked:
                self.error=event.get('error') or event.get('message') or event.get('reason') or event.get('error_type')
            self.status(('Bediening onderbroken · ' if event.get('blocked') else 'Opnieuw waarnemen · ')+str(self.error),
                status='blocked' if event.get('blocked') else 'running')
        if name=='stopped':
            self.status('Bediening onderbroken · '+str(self.error) if self.blocked else
                        'DJ Jev gestopt · muziek blijft spelen', status='blocked' if self.blocked else 'stopped')


async def session(key):
    display=Display(key);env=Rekordbox(library=[],native_trace=display);client=JevClient(key)
    runner=Runner(env,policy,client,display)
    async def heartbeat():
        while True:
            try:
                display.evidence.heartbeat()
            except OSError:
                display.evidence_fault = True
                runner.request_stop()
            await asyncio.sleep(1)
    heartbeat_task=asyncio.create_task(heartbeat())
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,runner.request_stop)
    try:
        display.evidence.state['build'] = validate_installation(BRIDGE)
        await env.start()
        preparation=asyncio.create_task(prepare_library(env.native,display,root=ROOT,bridge=BRIDGE))
        stopped=asyncio.create_task(runner._stop_event.wait())
        try:
            await asyncio.wait((preparation,stopped),return_when=asyncio.FIRST_COMPLETED)
            if runner.stopping:
                await env.stop()
                preparation.cancel()
                await asyncio.gather(preparation,return_exceptions=True)
                await client.close()
                result={'stopped':True,'blocked':False}
                display({'event':'stopped'})
            else:
                env.library=await preparation
                env.audio_store=await asyncio.to_thread(TimelineStore,env.library)
                result=await runner.run()
        finally:
            stopped.cancel()
            if not preparation.done():preparation.cancel()
            await asyncio.gather(preparation,stopped,return_exceptions=True)
    except Exception as error:
        display({'event':'error','stage':'startup','blocked':True,'error':str(error)})
        await env.stop();await client.close()
        result={'blocked':True,'error':display.error}
    heartbeat_task.cancel()
    await asyncio.gather(heartbeat_task,return_exceptions=True)
    if display.evidence_fault:
        result.update(blocked=True, error='Sessielog kon niet worden bewaard; bediening gestopt.')
    result.update(run_id=display.run_id,duration_seconds=time.time()-display.started,
                  evidence_directory=str(display.directory),last_error=display.error)
    display.evidence.finish(result)
    return result


def run(key):
    configuration = BRIDGE/'config/live_trial.json'
    if configuration.exists() and json.loads(configuration.read_text()).get('mode') == 'decision_audit':
        from .decision_audit import run_audit
        return asyncio.run(run_audit(key))
    return asyncio.run(session(key))
