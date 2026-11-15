"""Configured default runner: real source MP4 decoding and output H264 encoding.

Synthetic detector and HTTP responses; no camera, external API or model weights.
"""
import io,json,os,tempfile,shutil
from contextlib import redirect_stdout
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import cv2,httpx,numpy as np

from inference_worker import run_continuous as runner
from inference_worker.manual_review_config import configuration
from inference_worker.test_continuous_dispatcher import FakeTransport
from inference_worker.test_legacy_required_episode import POLICY


class ConfiguredManualRunnerTests(TestCase):
    def test_manual_source_waits_for_capture_and_does_not_equate_unverified_camera_clocks(self):
        from inference_worker.continuous_source_mux import SourceMux
        from inference_worker.continuous_source_reader import ContinuousSourceReader
        mux=SourceMux(camera_ids=['main','ref'],primary_camera='main',max_bytes=100000,max_frames=10,
            max_frame_pixels=4096,max_clock_error=0,clock_error_by_camera={'main':0,'ref':0},manual_review=True)
        client=SimpleNamespace(list_streams=lambda:[])
        reader=ContinuousSourceReader(client,mux,offsets={'main':0,'ref':0},
            max_chunk_bytes=100000,max_chunk_gap_seconds=.25,tolerate_source_gaps=True)
        self.assertEqual(reader.poll(),[])
        self.assertEqual(reader.poll(),[])
        frame=np.zeros((16,16,3),np.uint8)
        mux.push('main',20,frame)
        self.assertEqual(mux.pop_ready()[0][:2],('main',20))
        mux.push('ref',10,frame)
        self.assertEqual(mux.pop_ready()[0][:2],('ref',10))
        self.assertEqual(mux.last_emitted,20)

    def test_default_review_real_chunks_seam_and_whole_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.mp4'
            writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),15,(64,64))
            self.assertTrue(writer.isOpened())
            for _ in range(15):writer.write(np.zeros((64,64,3),np.uint8))
            writer.release();blob=source.read_bytes()
            epoch=datetime.now(timezone.utc).timestamp();commands=[];manifests={};probes=[]
            class Client:
                server_url='https://synthetic.test'
                tick=-2
                def get_ppe_policy(self,camera):return POLICY
                def set_inference_load(self,*a,**kw):pass
                def list_streams(self):
                    self.tick=min(self.tick+1,25)
                    return [dict(camera_id=c,stream_id=c,capture_context_id='a'*36,
                        capture_session_id='b'*36,assignment_version=1,last_sequence=max(-1,self.tick),
                        capture_stopped=self.tick==25,**({'final_sequence':25} if self.tick==25 else {})) for c in ('main','ref')]
                def list_chunks(self,stream,after_sequence=-1):
                    if after_sequence>=self.tick:return []
                    n=after_sequence+1
                    return [dict(chunk_id=stream+str(n),sequence=n,duration_ms=1000,
                        capture_start_at=datetime.fromtimestamp(epoch+n+(.072 if n>=10 else 0),timezone.utc).isoformat(),
                        capture_context_id='a'*36,capture_session_id='b'*36,assignment_version=1)]
                def close(self):self.client.close()
            def metadata(request):
                if '/video/chunks/' in request.url.path:return httpx.Response(200,content=blob)
                body=json.loads(request.read());commands.append((request.url.path,body))
                parts=request.url.path.strip('/').split('/')
                event=body.get('event_id') or parts[3]
                kind='start' if body.get('event_id') else 'manifest' if parts[-1]=='segments' else 'capture_report' if parts[-1]=='capture-reports' else body['kind']
                camera=parts[5] if kind=='capture_report' else None
                rows=manifests.setdefault(event,[])
                if kind=='manifest':
                    c=body['camera_id'];rows.append(dict(camera_id=c,sequence=body['sequence'],
                        request_id=event+c+str(body['sequence']),role='clip' if c=='main' else 'reference'))
                return httpx.Response(200,json={'event_id':event,'segments':rows,
                    'cameras':[dict(camera_id=c,role='clip' if c=='main' else 'reference') for c in ('main','ref')],
                    'accepted_command':dict(kind=kind,session_id=body['session_id'],sequence=body.get('sequence',0),camera_id=camera)})
            client=Client();client.client=httpx.Client(base_url=client.server_url,transport=httpx.MockTransport(metadata))
            transport=FakeTransport();transport.client=httpx.Client(base_url=client.server_url,transport=httpx.MockTransport(metadata))
            transport.close=transport.client.close
            class Detector:
                model_version='synthetic-3class';supported_ppe=('helmet',);supports_no_helmet=False
                count=0
                def detect(self,frame):
                    self.count+=1
                    rows=[dict(class_name='person',confidence=.95,bbox=[4,2,28,60])]
                    if self.count>300:rows.append(dict(class_name='helmet',confidence=.95,bbox=[10,2,22,12]))
                    return rows
            args=SimpleNamespace(submit_events=False,submit_review_candidates=True,replay_buffer=False,
                continuous_config=None,camera_id='main',reference_camera_id=['ref'],model_path=None,
                max_chunks=1000,stop_file=None,poll=.01,buffer_mib=8,
                episode_missing_grace_seconds=1,rewear_seconds=.5,qualification_grace_seconds=.5)
            with patch.dict(os.environ,{'PPE_PROCESSING_NODE_TOKEN':'synthetic','PPE_CONNECTED_REVIEW_STATE_DIR':str(root/'state')}):
                config=configuration(args)
            artifact=Path(os.environ['PPE_CONNECTED_VALIDATION_OUTPUT'])/uuid4().hex if os.environ.get('PPE_CONNECTED_VALIDATION_OUTPUT') else None
            if artifact:artifact.mkdir(parents=True)
            upload=transport.upload
            def checked_upload(plan):
                file=Path(config['state_directory'])/'raw'/plan.relative_path
                cap=cv2.VideoCapture(str(file))
                try:
                    self.assertTrue(cap.isOpened())
                    duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/cap.get(cv2.CAP_PROP_FPS)
                    self.assertAlmostEqual(duration,plan.capture_end-plan.capture_start,places=5)
                    probes.append((plan.camera_id,duration))
                    if artifact:
                        shutil.copy2(file,artifact/(plan.camera_id+'-'+str(len(probes))+'.mp4'))
                finally:cap.release()
                return upload(plan)
            transport.upload=checked_upload
            output=io.StringIO()
            with patch.dict(os.environ,{'PPE_PROCESSING_NODE_TOKEN':'synthetic'}), \
                 patch('inference_worker.manual_review_config.configuration',return_value=config), \
                 patch('inference_worker.run_server.create_detector',return_value=Detector()), \
                 patch.object(runner,'VideoClient',return_value=client), \
                 patch.object(runner,'PrivateClipTransport',return_value=transport), \
                 patch.object(runner.time,'sleep'),redirect_stdout(output):
                result=runner.run(args)
            self.assertEqual(result['state'],'drained')
            starts=[body for _,body in commands if 'event_id' in body]
            self.assertEqual(len(starts),1)
            self.assertEqual(starts[0]['evidence_mode'],'manual_review')
            closes=[b for p,b in commands if p.endswith('/updates') and b['kind']=='close']
            self.assertEqual(len(closes),1)
            self.assertGreaterEqual((datetime.fromisoformat(closes[0]['timestamp_end'])-datetime.fromisoformat(starts[0]['timestamp_start'])).total_seconds(),19.9)
            for c in ('main','ref'):self.assertGreater(sum(t for camera,t in probes if camera==c),21)
            reports=[b for p,b in commands if p.endswith('/capture-reports')]
            self.assertTrue(all(b['state']=='incomplete' and b['clock_uncertainty_seconds'] is None for b in reports))
            self.assertTrue(any(any(m['reason']=='source_input_gap' for m in b['missing_ranges']) for b in reports))
            if artifact:
                (artifact/'configured-runner-result.json').write_text(
                    json.dumps(dict(state=result['state'],starts=starts,closes=closes,reports=reports,
                                    encoded_media=probes,source_chunks=26,seam_seconds=.072,
                                    detector='synthetic fixture',network='fake HTTP',model_loaded=False),indent=2),encoding='utf-8')
