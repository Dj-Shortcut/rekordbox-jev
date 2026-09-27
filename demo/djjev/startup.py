"""Start owns folder inventory, official XML export, and offline cache refresh."""
import asyncio
import importlib.util
import json
from pathlib import Path
import unicodedata
from .clock import bridge_ns
from .state import read_library
from .audio_timeline import load_cached


def folder_files(root):
    root=root.resolve()
    return {unicodedata.normalize('NFC',p.name) for p in root.iterdir()
            if p.suffix.lower()=='.mp3' and p.is_file() and p.resolve().parent==root}


def missing_files(tracks, root):
    return folder_files(root)-{unicodedata.normalize('NFC',t['file']) for t in tracks}


async def prepare_library(native, display, *, root, bridge, music_root=None):
    music_root=Path(music_root or Path.home()/'Music/Music/26')
    display.status('Muziek uit map 26 bijwerken…',status='preparing')
    # Refresh the same fixed-folder inventory used by native title guards.
    spec=importlib.util.spec_from_file_location('jev_start_inventory',bridge/'scripts/inventory.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    inventory=await asyncio.to_thread(module.inventory)
    destination=bridge/'evidence/inventory.json'
    temporary=destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(destination)
    imported=False
    for attempt in range(6):
        result=await native.call('control','refreshLibrary',importFolder26=imported and attempt==1,
            notAfterMonotonicNS=bridge_ns()+85_000_000_000)
        export=Path(result.get('path','')).resolve()
        if result.get('verified') is not True or export.parent!=(bridge/'evidence/library').resolve():
            raise RuntimeError('Rekordbox-export niet bevestigd; geen verouderde lijst gebruikt.')
        tracks=await asyncio.to_thread(read_library,export,music_root)
        missing=missing_files(tracks,music_root)
        if not missing:break
        imported=True
        display.status(f'{len(missing)} nieuwe nummers in Rekordbox voorbereiden…',status='preparing')
        await asyncio.sleep(2)
    else:
        raise RuntimeError('Nieuwe muziek is nog niet gereed in Rekordbox: '+', '.join(sorted(missing)[:3]))
    if not tracks:raise RuntimeError('Map 26 bevat nog geen bruikbare muziek.')
    cache=root/'.audio-cache'
    pending=await asyncio.to_thread(lambda:[t for t in tracks
        if load_cached(t,music_root/t['file'],cache,verify_hash=True) is None])
    if pending:
        python=root/'.venv/bin/python'
        script=root/'scripts/analyze_transitions.py'
        if not python.is_file() or not script.is_file():
            raise RuntimeError('De ingebouwde muziekanalyse ontbreekt; installatie niet compleet.')
        display.status(f'{len(pending)} nummers analyseren…',status='preparing')
        args=[str(python),str(script),'--export',str(export),'--music-root',str(music_root),'--cache-dir',str(cache)]
        for track in pending:args.extend(['--track-id',track['id']])
        log=root/'evidence/startup-analysis.log';log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('wb') as errors:
            process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=errors)
            completed=0;failed=[]
            async def consume_analysis():
                nonlocal completed
                async for line in process.stdout:
                    row=json.loads(line)
                    completed+=1
                    if row.get('status')=='unavailable':failed.append(row.get('track_id'))
                    display.status(f'Muziekanalyse {completed}/{len(pending)}',status='preparing')
                return await process.wait()
            try:
                code=await asyncio.wait_for(consume_analysis(),600)
                if code or failed:
                    raise RuntimeError('Muziekanalyse niet voltooid; controleer de beatgrid van de nieuwe nummers in Rekordbox.')
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:await asyncio.wait_for(process.wait(),3)
                    except asyncio.TimeoutError:process.kill();await process.wait()
    saved=root/'library/rekordbox.xml';saved.parent.mkdir(parents=True,exist_ok=True)
    tmp=saved.with_suffix('.tmp');tmp.write_bytes(export.read_bytes());tmp.replace(saved)
    # Keep only the last confirmed private export; never remove user exports.
    for old in (bridge/'evidence/library').glob('rekordbox-*.xml'):
        if old!=export:old.unlink(missing_ok=True)
    display.status(f'{len(tracks)} nummers gereed · Rekordbox controleren',status='preparing')
    return tracks
