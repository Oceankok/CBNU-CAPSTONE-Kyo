from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.integration_scenario import run_scenario
from backend.tests.support import isolated_api


class BackendIntegrationTests(unittest.TestCase):
    def test_http_journey_from_video_to_consent_deletion_and_worker_education(self):
        self.enterContext(isolated_api())
        root = Path(self.enterContext(TemporaryDirectory(prefix='kyo-integration-')))
        storage, work = root/'storage', root/'.private_media'
        originals, processed = work/'originals', storage/'candidate_events/processed'
        values = {'backend.media.paths.PROJECT_ROOT': root, 'backend.media.paths.STORAGE_ROOT': storage,
                  'backend.media.paths.SOURCE_ROOT': originals, 'backend.media.service.SOURCE_ROOT': originals,
                  'backend.media.service.WORK_ROOT': work, 'backend.media.service.PROCESSED_ROOT': processed,
                  'backend.media.routes.WORK_ROOT': work, 'backend.processing.routes.WORK_ROOT': work,
                  'backend.processing.routes.PROCESSED_ROOT': processed, 'backend.api.main._STORAGE_DIR': storage,
                  'backend.consents.service.CONSENT_ROOT': work/'consents', 'backend.video.service.ROOT': work/'video_ingest'}
        for name, value in values.items():
            self.enterContext(patch(name, value))
        self.enterContext(patch.dict('os.environ', {'PPE_PROCESSING_MODE': 'remote', 'PPE_COOKIE_SECURE': 'false'}))
        from backend.api.main import app
        report = run_scenario(lambda: TestClient(app), 'test-password')
        self.assertEqual(report['result'], 'PASS')
        self.assertEqual(report['media_count'], 4)
        self.assertFalse(report['real_inference'])
        self.assertFalse(list(processed.glob('*.mp4')))
        self.assertFalse(list(originals.glob('*')))
