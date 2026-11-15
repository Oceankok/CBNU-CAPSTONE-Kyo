"""One latest-frame slot between a blocking camera and the paced encoder.

Only the reader thread reads/releases the camera. Faster input may supersede an
unconsumed frame; this is explicit bounded downsampling, never a timestamp edit.
Observed UTC is read-completion time, not a hardware/sensor capture timestamp.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import threading
import time


@dataclass(frozen=True)
class FrameSample:
    frame: object
    observed_monotonic: float
    observed_utc: datetime


@dataclass(frozen=True)
class ReadResult:
    status: str
    sample: FrameSample | None = None


class ReaderStopUnconfirmed(RuntimeError):
    pass


class LatestFrameReader:
    def __init__(self, camera, *, clock=None, utcnow=None, wait=None):
        self.camera = camera
        self.clock = clock or time.perf_counter
        self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self.condition = threading.Condition()
        self.wait = wait or self.condition.wait
        self.stop = threading.Event()
        self.thread = None
        self.pending = None
        self.ended = False
        self.error = None
        self.frames_read = self.superseded = 0

    def start(self):
        if self.thread is not None:
            raise RuntimeError('camera_reader_already_started')
        self.thread = threading.Thread(target=self._run, name='camera-frame-reader', daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.thread = None  # Camera ownership was not transferred.
            raise

    def _publish(self, sample):
        with self.condition:
            self.frames_read += 1
            if self.pending is not None:
                self.superseded += 1
            self.pending = sample
            self.condition.notify_all()

    def _run(self):
        try:
            while not self.stop.is_set():
                success, frame = self.camera.read()
                if not success or self.stop.is_set():
                    break
                self._publish(FrameSample(frame, self.clock(), self.utcnow()))
                # Blocking cameras supply their own pacing. A broken/instant
                # backend must still yield, rather than spin at full CPU speed.
                self.stop.wait(.001)
        except BaseException as error:
            with self.condition:
                self.error = error
        finally:
            try:
                self.camera.release()
            except BaseException as error:
                with self.condition:
                    self.error = self.error or error
            with self.condition:
                self.ended = True
                self.condition.notify_all()

    def next_frame(self, deadline, should_stop):
        with self.condition:
            while True:
                if self.error is not None:
                    raise self.error
                if should_stop():
                    return ReadResult('stopped')
                remaining = deadline-self.clock()
                if self.pending is not None and remaining <= 0:
                    sample, self.pending = self.pending, None
                    return ReadResult('frame', sample)
                if self.ended and self.pending is None:
                    return ReadResult('ended')
                self.wait(min(.02, remaining) if remaining > 0 else .02)

    def close(self, timeout=2):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is None:
            self.camera.release()
            return
        self.thread.join(timeout)
        if self.thread.is_alive():
            # Do not assume a cross-thread camera.release could unblock read.
            # The caller must not publish successful capture completion.
            raise ReaderStopUnconfirmed('camera_reader_stop_unconfirmed')
        if self.error is not None:
            raise self.error
