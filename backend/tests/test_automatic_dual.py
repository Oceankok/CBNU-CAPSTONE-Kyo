import json, os, tempfile, unittest, sys
from io import StringIO
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from video_sender.automatic_archive import archive_chunk
from video_sender.automatic_dual import AutomaticDual

POLICY={'server_epoch':'epoch','clock_policy':{'ttl_seconds':30,'max_rtt_seconds':1,'drift_ppm':100,
 'sample_error_seconds':.005,'max_error_seconds':.25,'step_tolerance_seconds':.05},
 'refresh_seconds':5,'sources':{'cam1':'node1','cam2':'node2'},'archive_max_bytes':1024}

class FakeStarter:
    def __init__(self,root,**kw):self.root=Path(root);self.directory=None;self.calls=[];self.exit78=False
    def start(self,target,**kw):self.calls.append((target,kw,dict(os.environ)));self.directory=target
    def active(self):return False
    def pre_session_failure(self):return self.exit78 and self.directory is not None and not (self.directory/'session.json').exists()
    def journal(self):
        if not self.directory or not (self.directory/'session.json').exists():raise FileNotFoundError()
        return json.loads((self.directory/'session.json').read_text())
    def _publish_stop(self):self.calls.append('stop')

class Tests(unittest.TestCase):
    def test_archive_idempotence_mismatch_and_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);chunk=root/'chunk_00000.mp4';chunk.write_bytes(b'video')
            chunk.with_suffix('.txt').write_text('time')
            archive=root/'archive'
            archive_chunk(archive,'run',chunk,clock_required=False,max_bytes=9)
            archive_chunk(archive,'run',chunk,clock_required=False,max_bytes=9)
            chunk.write_bytes(b'other')
            with self.assertRaisesRegex(ValueError,'mismatch'):archive_chunk(archive,'run',chunk,clock_required=False,max_bytes=9)
            other=root/'chunk_00001.mp4';other.write_bytes(b'x');other.with_suffix('.txt').write_text('time')
            with self.assertRaisesRegex(OSError,'capacity'):archive_chunk(archive,'run',other,clock_required=False,max_bytes=9)
            self.assertTrue(other.exists())

    def test_nested_mapping_environment_restoration_and_unresolved_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);entries=[dict(repo=str(root),config=str(root/('cfg'+str(i))),output=str(root/('out'+str(i))),index=i,retain_source=True) for i in (1,2)]
            settings=root/'dual.json';settings.write_text(json.dumps([entries[:1],[entries[1:]]]))
            policy=root/'policy.json';policy.write_text(json.dumps(POLICY))
            def validate(entry):
                i=entry['index'];return root,Path(entry['config']),Path(entry['output']),{'camera_id':'cam'+str(i),'server_url':'https://example.test'},'PATHVALUE'
            loader=SimpleNamespace(validate=validate,read_token=lambda config:'secret'+config.name)
            dual=AutomaticDual(settings,loader,policy,repository=root,starter_factory=FakeStarter)
            before=os.environ.get('VIDEO_NODE_TOKEN')
            for slot in dual.slots:dual.start(slot)
            self.assertEqual(os.environ.get('VIDEO_NODE_TOKEN'),before)
            for i,slot in enumerate(dual.slots,1):
                target,kwargs,env=slot['starter'].calls[0]
                self.assertEqual(env['VIDEO_CAMERA_ID'],'cam'+str(i));self.assertEqual(env['VIDEO_CLOCK_SOURCE_NODE_ID'],'node'+str(i))
                self.assertEqual(kwargs['camera_index'],i);self.assertEqual(env['VIDEO_AUTOMATIC_ARCHIVE_MAX_BYTES'],'1024')
                with self.assertRaises(FileNotFoundError):dual.start(slot)
            dual2=AutomaticDual(settings,loader,policy,repository=root,starter_factory=FakeStarter)
            with self.assertRaises(FileNotFoundError):dual2.start(dual2.slots[0])
            slot=dual2.slots[0];slot['starter'].directory.mkdir(parents=True)
            (slot['starter'].directory/'session.json').write_text(json.dumps({'closed':False}))
            dual2.stop(slot)
            self.assertIn('stop',slot['starter'].calls)
            with self.assertRaisesRegex(ValueError,'unresolved'):dual2.start(slot)
    def test_check_is_local_and_never_reads_token_or_starts(self):
        from video_sender import automatic_dual as module
        fake=SimpleNamespace(slots=[{'camera':'cam1','entry':{'index':0}}, {'camera':'cam2','entry':{'index':1}}])
        output=StringIO()
        with patch.object(sys,'argv',['automatic_dual','--settings','settings','--launcher','launcher','--policy','policy','--check']), \
             patch.object(module,'launcher_lock',return_value=nullcontext()), \
             patch.object(module,'load_original',return_value=object()), \
             patch.object(module,'AutomaticDual',return_value=fake), redirect_stdout(output):
            module.main()
        self.assertEqual(output.getvalue().splitlines(),['local_configuration_valid_not_server_proof','cam1 0','cam2 1'])

    def test_exit78_marker_and_partial_start_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);entries=[dict(repo=str(root),config=str(root/('cfg'+str(i))),output=str(root/('out'+str(i))),index=i,retain_source=True) for i in (1,2)]
            settings=root/'dual.json';settings.write_text(json.dumps(entries))
            policy=root/'policy.json';policy.write_text(json.dumps(POLICY))
            def validate(entry):
                i=entry['index'];return root,Path(entry['config']),Path(entry['output']),{'camera_id':'cam'+str(i),'server_url':'https://example.test'},'PATHVALUE'
            loader=SimpleNamespace(validate=validate,read_token=lambda config:'secret')
            dual=AutomaticDual(settings,loader,policy,repository=root,starter_factory=FakeStarter)
            slot=dual.slots[0];dual.start(slot);slot['starter'].exit78=True
            self.assertTrue(dual.record_pre_session_failure(slot))
            recovered=AutomaticDual(settings,loader,policy,repository=root,starter_factory=FakeStarter)
            self.assertIsNone(recovered.slots[0]['starter'].directory)
            dual.start(slot)  # exact observed exit 78 allows retry in same GUI
            other=dual.slots[1]
            other['starter'].directory=root/'unresolved'
            stopped=[]
            with patch.object(dual,'start',side_effect=[None,ValueError('failed')]), patch.object(dual,'stop',side_effect=lambda item:stopped.append(item)):
                with self.assertRaisesRegex(RuntimeError,'partial_start_stopping'):dual.start_all()
            self.assertEqual(stopped,[slot])

if __name__=='__main__':unittest.main()
