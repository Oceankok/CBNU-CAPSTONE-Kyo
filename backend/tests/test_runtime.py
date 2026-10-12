"""API-only mode must reject GPU work before reading or saving uploads."""

import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend.equipment.recognition_routes import analyze_image
from backend.media.routes import redact_existing, upload_media
from backend.runtime import local_processing_enabled
from backend.api.main import readiness


class RuntimeTests(unittest.TestCase):
    def test_readiness_does_not_create_a_missing_database(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with patch.dict(os.environ, {"PPE_DATABASE_URL": "", "PPE_DB_PATH": str(path), "PPE_JWT_SECRET": "x" * 32}):
                with self.assertRaises(HTTPException) as error:
                    readiness()
                self.assertEqual(error.exception.detail, "database_not_ready")
                self.assertFalse(path.exists())

    def test_native_default_keeps_processing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(local_processing_enabled())

    def test_api_only_rejects_processing_before_side_effects(self):
        with patch.dict(os.environ, {"PPE_LOCAL_PROCESSING": "false"}), \
                patch("backend.media.routes.get_candidate_event_by_id") as lookup, \
                patch("backend.equipment.recognition_routes.run_in_threadpool") as submit:
            request = Mock()
            calls = [lambda: upload_media("event", request, "video"),
                     lambda: redact_existing("media"),
                     lambda: analyze_image(request, None, {"user_id": "admin"})]
            for call in calls:
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(call())
                self.assertEqual(error.exception.status_code, 503)
                self.assertEqual(error.exception.detail, "processing_worker_not_connected")
            lookup.assert_not_called()
            submit.assert_not_called()
            request.stream.assert_not_called()
