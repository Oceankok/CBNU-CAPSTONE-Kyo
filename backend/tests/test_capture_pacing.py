"""Synthetic camera/clock regressions; no device, encoder or live clock use."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import time
from video_sender import backpressure, capture_control


class Clock:
    def __init__(self):self.now=0.0
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class Frame:
    shape=(16,16,3)
    def __init__(self,index):self.index=index
    def tobytes(self):return str(self.index).encode()


def capture_fixture(arrivals, *, submitted=15, write_seconds=0, stop_at=None,
                    close_error=None, result_observer=None, cleanup_probe=None, normal_at=None):
    from video_sender.frame_reader import LatestFrameReader, FrameSample
    from video_sender import clock_source
    clock=Clock();writes=[];reads=[];stamps=[]
    run_id='00000000-0000-4000-8000-000000000001'
    stream_id='00000000-0000-4000-8000-000000000002'
    proof_id='00000000-0000-4000-8000-000000000003'
    first_marker_write=None
    class WallClock(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,11,16,tzinfo=timezone.utc)+timedelta(seconds=clock.now)
            stamps.append(value);return value
    def read():
        index=len(reads)
        if index>=len(arrivals):return False,None
        clock.now=max(clock.now,arrivals[index]);reads.append((index,clock.now))
        return True,Frame(index)
    def write(data):
        nonlocal first_marker_write
        if marker.is_file() and first_marker_write is None:
            first_marker_write=len(writes)
        writes.append((int(data),clock.now));clock.now+=write_seconds
        if normal_at==len(writes):
            (root/f'.normal-stop-{run_id}').touch()
    camera=Mock();camera.isOpened.return_value=True;camera.get.return_value=16;camera.read.side_effect=read
    process=Mock();process.stdin.write.side_effect=write;process.wait.return_value=0
    class ScriptedReader:
        # Deterministic producer delivery into the real single-slot consumer.
        # Actual reader-thread ownership/join has separate tests below.
        def __init__(self,cap):
            self.inner=LatestFrameReader(cap,clock=clock.monotonic,wait=self.wait)
            self.index=0
        @property
        def frames_read(self):return self.inner.frames_read
        @property
        def superseded(self):return self.inner.superseded
        def start(self):pass
        def publish_due(self):
            while self.index<len(arrivals) and arrivals[self.index]<=clock.now:
                index=self.index;observed=arrivals[index];self.index+=1
                reads.append((index,observed))
                utc=datetime(2026,11,16,tzinfo=timezone.utc)+timedelta(seconds=observed)
                self.inner._publish(FrameSample(Frame(index),observed,utc))
            if self.index==len(arrivals):self.inner.ended=True
        def wait(self,seconds):
            target=clock.now+seconds
            if self.index<len(arrivals):target=min(target,max(clock.now,arrivals[self.index]))
            clock.now=target;self.publish_due()
        def next_frame(self,deadline,should_stop):
            self.publish_due()
            return self.inner.next_frame(deadline,should_stop)
        def close(self):
            if close_error is not None:raise close_error
            camera.release()
    witness=result_observer or Mock()
    if cleanup_probe is not None:cleanup_probe.update(camera=camera,process=process)
    with TemporaryDirectory() as temporary:
        root=Path(temporary)
        marker=capture_control.observation_stop_path(root,run_id)
        env={'VIDEO_OUTPUT_DIR':temporary,'VIDEO_CAPTURE_RUN_ID':run_id,'VIDEO_BACKPRESSURE_ENABLED':'false',
             'VIDEO_CLOCK_ANCHOR_FILE':'','VIDEO_NORMAL_STOP_STREAM_ID':''}
        if normal_at is not None:
            anchor=root/'anchor.json'
            anchor.write_text(json.dumps({'anchor':{'utc':0,'monotonic':0},'policy':dict(
                ttl_seconds=30,max_rtt_seconds=1,drift_ppm=100,sample_error_seconds=.005,
                max_error_seconds=.25,step_tolerance_seconds=.05)}))
            env.update(VIDEO_CLOCK_ANCHOR_FILE=str(anchor),VIDEO_NORMAL_STOP_STREAM_ID=stream_id)
        with patch.dict(os.environ,env), \
             patch.object(cv2,'VideoCapture',return_value=camera), \
             patch('subprocess.Popen',return_value=process), \
             patch.object(time,'perf_counter',side_effect=clock.monotonic), \
             patch.object(time,'sleep',side_effect=clock.sleep), \
             patch.object(capture_control,'stop_requested',side_effect=lambda *args:len(writes)>=submitted or (stop_at is not None and clock.now>=stop_at)), \
             patch.object(capture_control,'publish_result',witness), \
             patch('video_sender.frame_reader.LatestFrameReader',side_effect=ScriptedReader), \
             patch.object(clock_source,'capture_stamp',side_effect=lambda path,**kw:(kw['observed_monotonic'],proof_id)), \
             patch.dict('sys.modules',{'datetime':SimpleNamespace(datetime=WallClock,timezone=timezone)}):
            runpy.run_module('video_sender.capture',run_name='__main__')
        timestamps={p.name:p.read_text() for p in Path(temporary).glob('chunk_*.txt')}
        stop_marker=json.loads(marker.read_text()) if marker.is_file() else None
        cutoff_clock=(root/f"chunk_{stop_marker['sequence']:05d}.clock.json").read_text() if stop_marker else None
    return dict(reads=reads,writes=writes,timestamps=timestamps,wall_samples=stamps,
                stopped_by_request=witness.call_args.kwargs['stopped_by_request'],
                normal_stop_completed=witness.call_args.kwargs['normal_stop_completed'],
                first_marker_write=first_marker_write,stop_marker=stop_marker,cutoff_clock=cutoff_clock)


class CapturePacingRegressionTests(unittest.TestCase):
    def test_successful_read_two_ms_early_is_not_discarded(self):
        arrivals=[i/15+.000001 for i in range(18)]
        arrivals[0]=0;arrivals[7]=7/15-.002
        result=capture_fixture(arrivals)
        self.assertEqual(len(result['writes']),15)
        self.assertEqual(len(result['reads']),15)
        self.assertEqual([i for i,_ in result['writes']],list(range(15)))
        self.assertTrue(result['stopped_by_request'])

    def test_naive_pre_read_wait_would_queue_a_30fps_fifo_camera(self):
        # A counterexample for a proposed fix, not a claim about a real driver.
        clock=Clock();ages=[]
        for index in range(15):
            clock.sleep(max(0,index/15-clock.now))
            clock.now=max(clock.now,index/30)
            ages.append(clock.now-index/30)
        self.assertGreater(ages[-1],.4)

    def test_blocking_15fps_read_does_not_compound_latency(self):
        arrivals=[0]+[i/15+.000001 for i in range(1,35)]
        result=capture_fixture(arrivals,submitted=30)
        self.assertEqual([i for i,_ in result['writes']],list(range(30)))
        self.assertAlmostEqual(result['writes'][-1][1],29/15+.000001)

    def test_device_30fps_despite_requested_15_has_no_fifo_backlog(self):
        arrivals=[0]+[i/30+.000001 for i in range(1,100)]
        result=capture_fixture(arrivals,submitted=30)
        for index,submitted_at in result['writes']:
            self.assertLessEqual(submitted_at-arrivals[index],1/30+.00001)
        self.assertEqual(len({i for i,_ in result['writes']}),30)
        self.assertGreater(result['writes'][-1][0],50)  # Real faster samples were superseded.

    def test_slow_read_keeps_actual_observation_timestamps(self):
        arrivals=[0]+[i/15+(.2 if i>=4 else 0) for i in range(1,35)]
        result=capture_fixture(arrivals,submitted=31)
        self.assertEqual([i for i,_ in result['writes']],list(range(31)))
        self.assertEqual(result['timestamps']['chunk_00001.txt'],'2026-11-16T00:00:01.200Z')
        self.assertEqual(result['timestamps']['chunk_00002.txt'],'2026-11-16T00:00:02.200Z')

    def test_slow_pipe_uses_newest_frame_without_catchup_duplicates(self):
        arrivals=[i/30 for i in range(150)]
        result=capture_fixture(arrivals,submitted=16,write_seconds=.2)
        self.assertEqual(len({i for i,_ in result['writes']}),16)
        for prior,current in zip(result['writes'],result['writes'][1:]):
            self.assertGreaterEqual(current[1]-prior[1],.199999)
        self.assertEqual(result['timestamps']['chunk_00001.txt'],'2026-11-16T00:00:03.000Z')

    def test_stop_during_cadence_wait_is_prompt(self):
        result=capture_fixture([i/15 for i in range(20)],stop_at=.005)
        self.assertTrue(result['stopped_by_request'])
        self.assertEqual(len(result['writes']),1)

    def test_read_failure_is_not_normal_user_stop(self):
        result=capture_fixture([0,1/15],submitted=30)
        self.assertFalse(result['stopped_by_request'])
        self.assertEqual(len(result['writes']),2)

    def test_normal_stop_marks_first_frame_before_write_and_finishes_actual_tail(self):
        for boundary in (14,15):
            with self.subTest(boundary=boundary):
                result=capture_fixture([i/15 for i in range(60)],submitted=100,normal_at=boundary)
                self.assertEqual([i for i,_ in result['writes']],list(range(boundary+32)))
                self.assertEqual(result['first_marker_write'],boundary)
                self.assertEqual((result['stop_marker']['sequence'],result['stop_marker']['frame_index']),
                                 divmod(boundary,15))
                self.assertEqual(result['stop_marker']['proof_id'],json.loads(result['cutoff_clock'])['proof_id'])
                self.assertTrue(result['normal_stop_completed'])
                self.assertTrue(result['stopped_by_request'])

    def test_eof_and_immediate_fault_interrupt_the_tail_and_keep_stop_unconfirmed(self):
        for count,forced_stop in ((22,100),(60,22)):
            with self.subTest(count=count,forced_stop=forced_stop):
                result=capture_fixture([i/15 for i in range(count)],submitted=forced_stop,normal_at=15)
                self.assertEqual(len(result['writes']),22)
                self.assertEqual(result['first_marker_write'],15)
                self.assertFalse(result['normal_stop_completed'])
                self.assertFalse(result['stopped_by_request'])

    def test_unconfirmed_reader_shutdown_never_publishes_stop_proof(self):
        from video_sender.frame_reader import ReaderStopUnconfirmed
        result=Mock();cleanup={}
        with self.assertRaises(ReaderStopUnconfirmed):
            capture_fixture([i/15 for i in range(20)],submitted=1,
                close_error=ReaderStopUnconfirmed('camera_reader_stop_unconfirmed'),
                result_observer=result,cleanup_probe=cleanup)
        result.assert_not_called()
        cleanup['process'].stdin.close.assert_called_once()
        cleanup['process'].wait.assert_called_once()


class ReaderOwnershipTests(unittest.TestCase):
    def test_blocked_read_is_not_released_cross_thread_or_acknowledged(self):
        import threading
        from video_sender.frame_reader import LatestFrameReader,ReaderStopUnconfirmed
        started=threading.Event();finish=threading.Event()
        camera=Mock()
        def read():started.set();finish.wait(1);return False,None
        camera.read.side_effect=read
        reader=LatestFrameReader(camera);reader.start()
        self.assertTrue(started.wait(.5))
        try:
            with self.assertRaises(ReaderStopUnconfirmed):reader.close(timeout=.01)
            camera.release.assert_not_called()
        finally:
            finish.set();reader.thread.join(.5)
        camera.release.assert_called_once()

    def test_reader_exception_propagates_after_camera_release(self):
        from video_sender.frame_reader import LatestFrameReader
        camera=Mock();error=RuntimeError('camera_failure');camera.read.side_effect=error
        reader=LatestFrameReader(camera);reader.start();reader.thread.join(.5)
        with self.assertRaisesRegex(RuntimeError,'camera_failure'):
            reader.next_frame(0,lambda:False)
        camera.release.assert_called_once()


if __name__=='__main__':unittest.main()
