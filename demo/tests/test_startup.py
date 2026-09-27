import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from djjev.startup import prepare_library, missing_files

class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_exports_refreshes_inventory_and_uses_cache_without_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bridge=root/'bridge';music=root/'26';music.mkdir()
            (music/'A.mp3').write_bytes(b'fixture')
            (bridge/'scripts').mkdir(parents=True)
            (bridge/'scripts/inventory.py').write_text("def inventory(): return {'tracks':[{'file':'A.mp3'}]}\n")
            (bridge/'evidence/library').mkdir(parents=True)
            export=bridge/'evidence/library/rekordbox-fixture.xml';export.write_text('<fixture/>')
            calls=[];statuses=[]
            class Native:
                async def call(self,role,command,**args):
                    calls.append((role,command,args));return {'verified':True,'path':str(export)}
            class Display:
                def status(self,text,**values):statuses.append((text,values))
            tracks=[{'id':'one','file':'A.mp3'}]
            with patch('djjev.startup.read_library',return_value=tracks),patch('djjev.startup.load_cached',return_value={'valid':True}),patch('asyncio.create_subprocess_exec') as process:
                result=await prepare_library(Native(),Display(),root=root,bridge=bridge,music_root=music)
            self.assertEqual(result,tracks);process.assert_not_called()
            self.assertEqual(calls[0][1],'refreshLibrary')
            self.assertFalse(calls[0][2]['importFolder26'])
            self.assertEqual((root/'library/rekordbox.xml').read_text(),'<fixture/>')
            self.assertEqual(json.loads((bridge/'evidence/inventory.json').read_text())['tracks'][0]['file'],'A.mp3')
            self.assertTrue(all(v['status']=='preparing' for _,v in statuses))

    def test_unicode_filenames_and_outside_symlinks_do_not_trigger_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'26';root.mkdir();(root/'E\u0301cho.mp3').write_bytes(b'a')
            outside=Path(tmp)/'outside.mp3';outside.write_bytes(b'b');(root/'outside.mp3').symlink_to(outside)
            self.assertEqual(missing_files([{'file':'Écho.mp3'}],root),set())

    async def test_unconfirmed_export_never_falls_back_to_stale_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'scripts').mkdir();(root/'evidence').mkdir();(root/'26').mkdir()
            (root/'scripts/inventory.py').write_text('def inventory(): return {}\n')
            class Native:
                async def call(self,*a,**kw):return {'verified':False,'path':str(root/'stale.xml')}
            class Display:
                def status(self,*a,**kw):pass
            with self.assertRaisesRegex(RuntimeError,'niet bevestigd'):
                await prepare_library(Native(),Display(),root=root,bridge=root,music_root=root/'26')
