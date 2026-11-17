"""Fresh-proof bootstrap checks, using the actual lease and config validators."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from clock_contract import ClockPolicy, sample_interval
from deploy.host_runtime.automatic_profile import prepare, load_template, PreparationStopped
from inference_worker.manual_review_config import configuration


class AutomaticProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temp)
        self.cameras = ['LAPTOP_CAM_01', 'LAPTOP_CAM_02']
        self.policy = ClockPolicy(300, 1, 100, .02, .25, .05)
        profile = configuration(SimpleNamespace(camera_id=self.cameras[0], reference_camera_id=self.cameras[1:],
                                buffer_mib=128, episode_missing_grace_seconds=2))
        for key in ('clocks', 'state_directory'):
            profile.pop(key)
        profile['capture'].pop('manual_review')
        profile['capture']['max_clock_error'] = .25
        profile['mux'].pop('manual_review')
        profile['mux']['max_clock_error'] = .25
        profile['source'].pop('tolerate_source_gaps')
        profile['lifecycle'].pop('review_episode_continuity')
        profile['lifecycle']['evidence_mode'] = 'verified_continuous'
        self.template = dict(version=1, source_node_ids={c:'laptop-source' for c in self.cameras},
                             server_epoch='test-epoch', policy=self.policy.public(), wait_seconds=.1,
                             poll_seconds=.01, proof_poll_seconds=10, max_host_rtt_seconds=1,
                             state_root=str(self.root/'runtime'), profile=profile)
        self.path = self.root/'template.json'
        self.settings = dict(runner='continuous', mode='review', camera_ids=self.cameras,
                             model_path='not-used-in-this-proof-check.pt', continuous_bootstrap=str(self.path))
        self.streams, self.proofs = [], {}
        now = time.time()
        for camera in self.cameras:
            stream, context = str(uuid4()), str(uuid4())
            self.streams.append(dict(camera_id=camera, stream_id=stream, capture_context_id=context,
                                     capture_stopped=False))
            sent, received = now-.22, now-.18
            low, high = sample_interval(now, sent, received, 100., 100.04, self.policy)
            self.proofs[stream] = dict(version=1, proof_id=str(uuid4()), sample_sequence=1,
                source_node_id='laptop-source', camera_id=camera, stream_id=stream,
                capture_context_id=context, capture_session_id=str(uuid4()), run_id=str(uuid4()), epoch=str(uuid4()),
                server_epoch='test-epoch', server_boot_id='test-boot', server_sample=now,
                source_sent_at=sent, source_received_at=received, monotonic_sent=100., monotonic_received=100.04,
                offset_low=low, offset_high=high, valid_until=now+300, policy=self.policy.public())
        def handler(request):
            stream = request.url.path.split('/')[-2]
            return httpx.Response(200, json=dict(version=1, server_epoch='test-epoch', server_boot_id='test-boot',
                                                server_time=time.time(), proof=self.proofs[stream]))
        self.http = self.enterContext(httpx.Client(base_url='https://test.invalid', transport=httpx.MockTransport(handler)))
        self.client = SimpleNamespace(server_url='https://test.invalid', client=self.http,
                                      list_streams=lambda: copy.deepcopy(self.streams))

    def run_prepare(self):
        self.path.write_text(json.dumps(self.template), encoding='utf-8')
        return prepare(self.settings, self.client)

    def test_fresh_sources_produce_separate_verified_profiles_without_mutating_settings(self):
        original = copy.deepcopy(self.settings)
        result = self.run_prepare()
        self.assertEqual(self.settings, original)
        self.assertNotIn('continuous_bootstrap', result)
        states = []
        for camera, path in result['continuous_profiles'].items():
            profile = json.loads(Path(path).read_text())
            states.append(profile['state_directory'])
            self.assertEqual(profile['lifecycle']['evidence_mode'], 'verified_continuous')
            self.assertEqual(profile['clock_proof']['bindings'][camera]['stream_id'], self.streams[self.cameras.index(camera)]['stream_id'])
            self.assertAlmostEqual(profile['clocks'][camera]['offset'], .2, places=5)
        self.assertEqual(len(set(states)), 2)
        self.assertFalse(any(Path(p).exists() for p in states))

    def test_closed_or_missing_sources_timeout_without_creating_profiles(self):
        for stream in self.streams:
            stream['capture_stopped'] = True
        with self.assertRaisesRegex(ValueError, 'automatic_source_proof_timeout'):
            self.run_prepare()
        self.assertFalse((self.root/'runtime').exists())

    def test_ambiguous_active_sources_are_not_resolved_by_picking_newest(self):
        self.streams.append(dict(self.streams[0], stream_id=str(uuid4())))
        with self.assertRaisesRegex(ValueError, 'ambiguous_active_streams'):
            self.run_prepare()

    def test_foreign_node_and_context_and_server_boot_are_rejected(self):
        stream = self.streams[0]['stream_id']
        original = copy.deepcopy(self.proofs[stream])
        for field, value in [('source_node_id','other-node'), ('capture_context_id',str(uuid4())),
                             ('server_boot_id','other-boot')]:
            self.proofs[stream] = dict(original, **{field:value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_prepare()
        self.assertFalse((self.root/'runtime').exists())

    def test_expired_proof_is_never_extended(self):
        for proof in self.proofs.values():
            for key in ('server_sample','source_sent_at','source_received_at','valid_until'):
                proof[key] -= 400
        with self.assertRaisesRegex(ValueError, 'automatic_source_proof_timeout'):
            self.run_prepare()

    def test_manual_evidence_and_path_traversal_are_rejected(self):
        self.template['profile']['capture']['manual_review'] = True
        self.path.write_text(json.dumps(self.template), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manual_evidence_forbidden'):
            load_template(self.path, self.cameras)
        self.settings['camera_ids'] = ['../escape']
        with self.assertRaisesRegex(ValueError, 'camera_scope_invalid'):
            prepare(self.settings, self.client)

    def test_runtime_state_cannot_be_nested_inside_source_checkout(self):
        checkout = Path(__file__).resolve().parents[2]
        self.template['state_root'] = str(checkout / 'unexpected-runtime')
        self.path.write_text(json.dumps(self.template), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'state_contains_source_or_template'):
            load_template(self.path, self.cameras)

    def test_indefinite_wait_stops_cooperatively_without_creating_state(self):
        self.template['wait_seconds'] = None
        self.path.write_text(json.dumps(self.template), encoding='utf-8')
        self.streams.clear()
        polls = []
        def wait(seconds):
            polls.append(seconds)
        with self.assertRaises(PreparationStopped):
            prepare(self.settings, self.client, sleep=wait, stopped=lambda: len(polls) >= 2)
        self.assertEqual(len(polls), 2)
        self.assertFalse((self.root/'runtime').exists())

    def run_entry(self, run):
        from deploy.host_runtime.entry import main
        settings = copy.deepcopy(self.settings)
        model = self.root/'model.pt'
        model.touch()
        settings['model_path'] = str(model)
        self.path.write_text(json.dumps(self.template), encoding='utf-8')
        settings_path = self.root/'settings.json'
        settings_path.write_text(json.dumps(settings), encoding='utf-8')
        generated = copy.deepcopy(settings)
        generated.pop('continuous_bootstrap')
        generated['continuous_profiles'] = {c:str(self.path) for c in self.cameras}
        client = SimpleNamespace(get_runtime_version=lambda:{'processing_mode':'remote'},close=lambda:None)
        with patch('inference_worker.video_client.VideoClient', return_value=client), \
             patch('backend.runtime_version.local_version', return_value={}), \
             patch('backend.runtime_version.compare', return_value={'status':'matched'}), \
             patch('deploy.host_runtime.automatic_profile.prepare', side_effect=[generated,generated,PreparationStopped()]) as prepare_mock, \
             patch('inference_worker.run_multi.main', side_effect=run):
            result = main(['--settings',str(settings_path),'--source',str(Path(__file__).resolve().parents[2]),
                           '--stop-file',str(self.root/'external-stop')])
        return result, prepare_mock.call_count

    def test_clean_source_end_rearms_with_fresh_profiles_and_separate_stop_files(self):
        stops=[]
        def run(argv, *, return_after_clean_source):
            self.assertTrue(return_after_clean_source)
            path=Path(argv[argv.index('--stop-file')+1])
            stops.append(str(path))
            path.touch()  # Supervisor's private cleanup signal, not user stop.
            return True
        self.assertEqual(self.run_entry(run), (0,3))
        self.assertEqual(len(set(stops)),2)
        self.assertFalse((self.root/'external-stop').exists())

    def test_inference_failure_does_not_silently_rearm_or_retry(self):
        def run(argv, **kwargs):
            raise RuntimeError('synthetic failure')
        self.assertEqual(self.run_entry(run), (1,1))
