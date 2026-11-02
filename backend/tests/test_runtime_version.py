"""Host version inspection never executes git writes or real HTTP in these tests."""
from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.runtime_version import compare, local_version, main


class RuntimeVersionTests(unittest.TestCase):
    def test_match_difference_dirty_unknown_and_schema_failure(self):
        local = {'revision': 'a'*40, 'dirty': False}
        server = {'revision': 'a'*40, 'database_schema_version': 16, 'required_schema_version': 16}
        self.assertEqual(compare(local, server)['status'], 'matched')
        self.assertEqual(compare({**local, 'revision': 'b'*40}, server)['status'], 'different')
        self.assertEqual(compare({**local, 'dirty': True}, server)['status'], 'dirty')
        self.assertEqual(compare(local, {**server, 'revision': None})['status'], 'unknown')
        self.assertEqual(compare(local, {**server, 'database_schema_version': 15})['status'], 'schema_mismatch')

    def test_git_inspection_returns_no_paths_or_command_output(self):
        with patch('backend.runtime_version.subprocess.run', side_effect=[
                SimpleNamespace(stdout='a'*40+'\n'), SimpleNamespace(stdout='?? private-file-name\n')]) as run:
            result = local_version('test-root')
        self.assertEqual(result, {'revision': 'a'*40, 'dirty': True})
        self.assertNotIn('private-file-name', json.dumps(result))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 5)
            self.assertNotIn('shell', call.kwargs)

    def test_missing_git_does_not_manufacture_a_revision(self):
        with patch('backend.runtime_version.subprocess.run', side_effect=FileNotFoundError()):
            self.assertEqual(local_version(), {'revision': None, 'dirty': None})

    def test_cli_match_exits_zero_and_closes_client(self):
        client = Mock()
        client.get_runtime_version.return_value = {'revision': 'a'*40, 'database_schema_version': 16, 'required_schema_version': 16}
        output = io.StringIO()
        with patch('inference_worker.video_client.VideoClient', return_value=client), \
             patch('backend.runtime_version.local_version', return_value={'revision': 'a'*40, 'dirty': False}), \
             redirect_stdout(output), self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'matched')
        client.close.assert_called_once()

    def test_cli_http_failure_does_not_print_url_or_error_body(self):
        client = Mock()
        client.get_runtime_version.side_effect = httpx.ReadTimeout('must-not-be-printed')
        output = io.StringIO()
        with patch('inference_worker.video_client.VideoClient', return_value=client), \
             patch('backend.runtime_version.local_version', return_value={'revision': 'a'*40, 'dirty': False}), \
             redirect_stdout(output), self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'server_version_unavailable'})
        client.close.assert_called_once()
