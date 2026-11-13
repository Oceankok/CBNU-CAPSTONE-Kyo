"""Real API quota rejection and sender retry; isolated DB and fixture MP4 only."""
from unittest import TestCase
from unittest.mock import patch
import httpx
from backend.tests import test_capture_sender_e2e as baseline
from video_sender.backpressure import Policy


class BackpressureSenderE2ETests(TestCase):
    def setUp(self):
        baseline.CaptureSenderE2ETests.setUp(self)
        self.protocol.policy=Policy(1024**3,256*1024**2,2*1024**3)
        self.runner.policy=self.protocol.policy
        self.protocol.guard=self.runner.check_spool

    chunk=baseline.CaptureSenderE2ETests.chunk
    proof=baseline.CaptureSenderE2ETests.proof
    move=baseline.CaptureSenderE2ETests.move

    def test_real_quota_429_then_same_sequence_ack(self):
        p=self.chunk(); original=p.read_bytes(); context=self.runner.state['context_id']
        seen=[]
        def quota(*args):
            seen.append(p.read_bytes())
            return len(seen)<=2  # first request stays full after cleanup => 429
        with patch('backend.video.service._buffer_full',side_effect=quota):
            self.runner.drain()
        chunks=[(status,body) for path,status,body in self.transport.calls if path.endswith('/chunks')]
        self.assertEqual([status for status,_ in chunks],[429,202])
        self.assertEqual(chunks[-1][1]['sequence'],0)
        self.assertTrue(all(data==original for data in seen))
        self.assertFalse(p.exists());self.assertEqual(self.runner.state['acknowledged_sequence'],0)
        self.assertEqual(self.runner.state['context_id'],context)
        self.assertFalse(self.runner.state['closed']);self.assertEqual(self.move().status_code,409)

    def test_real_repeated_429_exhaustion_preserves_source_and_lock(self):
        p=self.chunk(); original=p.read_bytes(); context=self.runner.state['context_id']
        with patch('backend.video.service._buffer_full',return_value=True):
            with self.assertRaises(httpx.HTTPStatusError) as error:self.runner.drain()
        self.assertEqual(error.exception.response.status_code,429)
        self.assertEqual(p.read_bytes(),original)
        self.assertEqual(self.runner.state['acknowledged_sequence'],-1)
        self.assertFalse(self.runner.state['closed']);self.assertEqual(self.runner.state['context_id'],context)
        self.assertEqual(len([p for p,_,_ in self.transport.calls if p.endswith('/chunks')]),5)
        self.assertEqual(self.move().status_code,409)
