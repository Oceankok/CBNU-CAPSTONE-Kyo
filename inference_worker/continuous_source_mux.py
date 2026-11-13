"""Bounded chronological two/multi-camera input boundary for opt-in runner.

Capture times must already use the explicitly verified common epoch. No detector,
network fetch, identity reassignment, or live runner registration occurs here.
Public methods are serialized. Caller must close in finally after journaling a
failure. A returned frame batch remains owned by caller and must be included in
the total process budget until released; downstream prebuffers/writers add copies.
"""
import heapq
import threading
from functools import wraps
import math
import numpy as np


class SourceMuxError(RuntimeError):pass


def locked(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.lock:return method(self,*args,**kwargs)
    return call


class SourceMux:
    def __init__(self, *, camera_ids, primary_camera, max_bytes, max_frames,
                 max_frame_pixels, max_clock_error, clock_error_by_camera):
        self.lock=threading.RLock()
        self.cameras=tuple(camera_ids)
        if not self.cameras or len(set(self.cameras))!=len(self.cameras) or primary_camera not in self.cameras:
            raise ValueError('camera snapshot')
        for value in (max_bytes,max_frames,max_frame_pixels):
            if type(value) is not int or value<=0:raise ValueError('positive mux limit')
        if not math.isfinite(max_clock_error) or max_clock_error<0:raise ValueError('clock policy')
        if set(clock_error_by_camera)!=set(self.cameras):raise ValueError('clock snapshot')
        if any(not math.isfinite(e) or not 0<=e<=max_clock_error for e in clock_error_by_camera.values()):
            raise ValueError('unverified clock error')
        self.primary=primary_camera;self.max_bytes=max_bytes;self.max_frames=max_frames
        self.max_pixels=max_frame_pixels;self.errors=dict(clock_error_by_camera)
        self.queue=[];self.latest={};self.bytes=0;self.serial=0;self.failure=None
        self.last_emitted=None;self.ended=set()

    @locked
    def push(self,camera,timestamp,frame):
        if self.failure:raise SourceMuxError(self.failure)
        if camera not in self.cameras:raise ValueError('unexpected camera')
        if camera in self.ended:raise SourceMuxError('frame_after_source_end')
        if isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp):
            raise ValueError('timestamp')
        if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
                or frame.shape[2]!=3 or frame.shape[0]*frame.shape[1]>self.max_pixels):
            raise ValueError('frame')
        if camera in self.latest and timestamp<=self.latest[camera]:
            self.failure='camera_clock_regression';raise SourceMuxError(self.failure)
        if len(self.queue)>=self.max_frames or self.bytes+frame.nbytes>self.max_bytes:
            self.failure='mux_budget_exhausted';raise SourceMuxError(self.failure)
        self.latest[camera]=timestamp;self.serial+=1
        # Reference frames at a common timestamp precede primary inference so
        # a newly qualifying primary event sees all available pre-roll.
        heapq.heappush(self.queue,(timestamp,camera==self.primary,self.serial,camera,frame.copy()))
        self.bytes+=frame.nbytes

    @locked
    def pop_ready(self):
        if self.failure:raise SourceMuxError(self.failure)
        remaining=[c for c in self.cameras if c not in self.ended]
        if any(c not in self.latest for c in remaining):return []
        watermark=min((self.latest[c] for c in remaining),default=math.inf)
        result=[]
        while self.queue and self.queue[0][0]<=watermark:
            timestamp,_,_,camera,frame=heapq.heappop(self.queue)
            self.bytes-=frame.nbytes
            if self.last_emitted is not None and timestamp<self.last_emitted:
                self.failure='mux_timestamp_regression';raise SourceMuxError(self.failure)
            self.last_emitted=timestamp;result.append((camera,timestamp,frame))
        return result

    @locked
    def check_stall(self,*,now,max_lag_seconds):
        if not math.isfinite(now) or not math.isfinite(max_lag_seconds) or max_lag_seconds<=0:
            raise ValueError('stall policy')
        if any(c not in self.latest or now-self.latest[c]>max_lag_seconds for c in self.cameras if c not in self.ended):
            self.failure='camera_missing_or_stalled';raise SourceMuxError(self.failure)

    @locked
    def end_source(self,camera):
        """Only the reader's verified, fully consumed final sequence authorizes EOS."""
        if self.failure:raise SourceMuxError(self.failure)
        if camera not in self.cameras:raise ValueError('unexpected camera')
        self.ended.add(camera)

    @locked
    def drained_sources(self):
        queued={row[3] for row in self.queue}
        return self.ended-queued

    @locked
    def close(self):
        self.queue.clear();self.bytes=0;self.failure='mux_closed'
