from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import importlib.util
import json
import os

from video_sender.context_launcher_bridge import configured_environment


class BridgeTests(TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.repo=self.root/'repo'; (self.repo/'video_sender').mkdir(parents=True)
        (self.repo/'video_sender'/'context_sender.py').touch()
        self.cfg=dict(repo=str(self.repo), config=str(self.root/'config'),
                      output=str(self.root/'output'), ffmpeg='', index='2')
        (self.root/'launcher-settings.json').write_text(json.dumps(self.cfg))
        self.loader=Mock()
        self.loader.validate.return_value=(self.repo, self.root/'config', self.root/'output',
            {'server_url':'https://test.invalid','camera_id':'CAM_001'}, 'safe-path')
        self.loader.read_token.return_value='test-only-secret'

    def test_existing_config_and_dpapi_reader_used_without_writes(self):
        before=(self.root/'launcher-settings.json').read_bytes()
        with patch.dict(os.environ, {'VIDEO_NODE_TOKEN':'previous'}):
            with configured_environment(self.root,loader=self.loader) as (repo,sessions,index):
                self.assertEqual(repo,self.repo); self.assertEqual(index,2)
                self.assertEqual(sessions,self.root/'output'/'context-sessions')
                self.assertEqual(os.environ['VIDEO_NODE_TOKEN'],'test-only-secret')
            self.assertEqual(os.environ['VIDEO_NODE_TOKEN'],'previous')
        self.loader.validate.assert_called_once_with(self.cfg)
        self.loader.read_token.assert_called_once_with(self.root/'config')
        self.assertEqual(before,(self.root/'launcher-settings.json').read_bytes())

    def test_cleanup_on_gui_failure(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                with configured_environment(self.root,loader=self.loader): raise RuntimeError('gui failure')
            self.assertNotIn('VIDEO_NODE_TOKEN',os.environ)

    def test_legacy_repository_rejected_before_decrypt(self):
        (self.repo/'video_sender'/'context_sender.py').unlink()
        with self.assertRaises(ValueError):
            with configured_environment(self.root,loader=self.loader): pass
        self.loader.read_token.assert_not_called()

    def test_original_local_launcher_validate_contract(self):
        source=Path('/workspace/shared/camera-launcher-library/camera_launcher.py')
        if not source.exists(): self.skipTest('Read-only original launcher reference not present')
        spec=importlib.util.spec_from_file_location('original_launcher', source)
        original=importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
        for name in ('capture.py','live_sender.py','chunks.py'):
            (self.repo/'video_sender'/name).touch()
        config=self.root/'config';config.mkdir()
        (config/'node-token.xml').write_text('synthetic DPAPI placeholder, never decrypted')
        (config/'settings.json').write_text(json.dumps({'server_url':'https://test.invalid','camera_id':'CAM_001'}))
        validate=original.validate
        with patch.object(original,'validate',side_effect=lambda cfg:validate(cfg,dependencies=False)), \
             patch.object(original,'read_token',return_value='synthetic-token') as decrypt:
            with configured_environment(self.root,loader=original) as (_,_,index):
                self.assertEqual(index,2)
                self.assertEqual(os.environ['VIDEO_CAMERA_ID'],'CAM_001')
            decrypt.assert_called_once_with(config)
