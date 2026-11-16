"""In-memory, opt-in prototype. NOT a live integration or durable event outbox.

One coordinator owns a dedicated, initially empty root. All public calls must
be serialized by its owner. Raw files must never be served as public media.
Offsets map camera timestamps to the event clock; validation is external.
"""
from collections import deque
from dataclasses import dataclass, field
import math
from pathlib import Path
from uuid import uuid4
from types import MappingProxyType

import numpy as np
from datetime import datetime, timezone
from backend.capture_grid import required_capture_end, validate_output_fps
from inference_worker.segment_spool import SegmentSpool, TimestampSegmentWriter


@dataclass(frozen=True)
class CameraClock:
    offset: float
    error_bound: float
    verified: bool


@dataclass
class Capture:
    spool: object = None
    writer: object = None
    segments: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    done: bool = False
    seed: dict = field(default_factory=dict)
    last_source_at: float = None


class MultiCameraCapture:
    def __init__(self, root, *, cameras, max_clock_error, pre_seconds,
                 post_seconds, discovery_delay_limit, prebuffer_seconds,
                 prebuffer_bytes, max_buffer_frames, max_events,
                 global_spool_bytes, spool_config, writer_config, before_spool_create=None,
                 capture_output_fps=None, manual_review=False):
        self.manual_review=manual_review
        numeric = dict(max_clock_error=max_clock_error, pre_seconds=pre_seconds,
                       post_seconds=post_seconds, discovery_delay_limit=discovery_delay_limit,
                       prebuffer_seconds=prebuffer_seconds)
        if any(not math.isfinite(v) or v < 0 for v in numeric.values()):
            raise ValueError('invalid time policy')
        for v in (prebuffer_bytes, max_buffer_frames, max_events, global_spool_bytes):
            if type(v) is not int or v <= 0:
                raise ValueError('invalid capacity')
        if not cameras or prebuffer_seconds < pre_seconds + discovery_delay_limit:
            raise ValueError('prebuffer must cover pre-roll plus discovery delay')
        if before_spool_create is not None and not callable(before_spool_create):
            raise ValueError('spool ownership callback')
        self.before_spool_create=before_spool_create
        self.root = Path(root)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if any(self.root.iterdir()):
            raise ValueError('prototype requires empty private root; recover old mappings externally')
        self.cameras = MappingProxyType(dict(cameras))
        self.error = max_clock_error
        self.pre, self.post, self.delay = pre_seconds, post_seconds, discovery_delay_limit
        self.buffer_seconds, self.buffer_bytes = prebuffer_seconds, prebuffer_bytes
        self.max_frames, self.max_events = max_buffer_frames, max_events
        self.global_bytes = global_spool_bytes
        self.sc, self.wc = dict(spool_config), dict(writer_config)
        self.capture_output_fps = (validate_output_fps(capture_output_fps)
                                   if capture_output_fps is not None else None)
        if self.capture_output_fps is not None and (self.pre != 2 or self.post != 2
                or self.capture_output_fps != self.wc['output_fps']):
            raise ValueError('capture output grid contract mismatch')
        self.reserved = 0
        self.buffers = {c: deque() for c in cameras}
        self.last = {}
        self.events = {}
        self.ended_sources = {}

    @staticmethod
    def _time(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('invalid timestamp')
        return value

    def _fail(self, cap, reason):
        if reason not in cap.reasons:
            cap.reasons.append(reason)
        cap.done = True
        if cap.writer and not cap.writer.closed:
            if self.manual_review:
                try:
                    self._manual_flush(cap)
                    if reason in ('source_ended','reference_drain_cutoff','source_failed','coordinator_shutdown'):
                        cap.reasons.append('missing_post_roll')
                    return
                except Exception as exc:
                    # Persist a bounded code, never raw encoder errors or paths.
                    failure = ('capture_flush_grid_error' if isinstance(exc, ValueError)
                               and str(exc) == 'end boundary must align to output frame grid'
                               else 'capture_flush_failed')
                    cap.reasons.append(failure)
                    cap.writer.abort(reason=failure)
                    return
            cap.writer.abort(reason='coordinator_incomplete')

    def _feed(self, event, cap, timestamp, frame):
        if cap.done:
            return
        if self.manual_review:
            return self._manual_feed(event,cap,timestamp,frame)
        try:
            boundary = event['end']
            if boundary is not None and timestamp >= boundary:
                if (boundary-cap.last_source_at > self.wc['max_input_gap_seconds']
                        or timestamp-cap.last_source_at > self.wc['max_input_gap_seconds']):
                    raise ValueError('source_end_gap')
                cap.segments.extend(cap.writer.finish(boundary))
                cap.done = True
            else:
                if cap.last_source_at is not None and timestamp-cap.last_source_at > self.wc['max_input_gap_seconds']:
                    raise ValueError('source_input_gap')
                cap.segments.extend(cap.writer.push(timestamp, frame))
                cap.last_source_at = timestamp
        except Exception as exc:
            self._fail(cap, 'capture_failure:' + str(exc)[:100])

    def _manual_flush(self,cap,boundary=None):
        if cap.writer and not cap.writer.closed and cap.writer.last_timestamp is not None:
            writer=cap.writer
            # Close the last observed frame on its original output grid. Chunk
            # jitter may put the source sample between grid points; never extend
            # a run across an unknown gap or invent a longer final frame.
            count=math.floor((writer.last_timestamp-writer.origin)*writer.fps+1e-5)+1
            end=writer.origin+count/writer.fps
            if boundary is not None:
                limit=writer.origin+math.floor((boundary-writer.origin)*writer.fps+1e-5)/writer.fps
                end=min(end,limit)
            cap.segments.extend(cap.writer.finish(end))

    def _manual_feed(self,event,cap,timestamp,frame):
        try:
            boundary=event['end']
            if boundary is not None and cap.writer.origin is not None:
                # An encoded frame is indivisible. Seal on this run's actual
                # output grid before the shared obligation, reporting the short
                # residual as missing instead of overrunning or inventing it.
                grid_end=cap.writer.origin+math.floor((boundary-cap.writer.origin)*cap.writer.fps+1e-5)/cap.writer.fps
                if timestamp>=grid_end-1e-6 and cap.writer.last_timestamp is not None:
                    cap.segments.extend(cap.writer.finish(grid_end))
                    if boundary-grid_end>1e-6 and 'missing_post_roll' not in cap.reasons:cap.reasons.append('missing_post_roll')
                    cap.done=True
                    return
            gap=(cap.last_source_at is not None and timestamp-cap.last_source_at>1.5/self.wc['output_fps'])
            if gap:
                self._manual_flush(cap,boundary)
                if 'source_input_gap' not in cap.reasons:cap.reasons.append('source_input_gap')
                cap.writer=TimestampSegmentWriter(cap.spool,**self.wc)
            if boundary is not None and timestamp>=boundary:
                if not gap and cap.writer.last_timestamp is not None:
                    cap.segments.extend(cap.writer.finish(boundary))
                cap.done=True
                return
            cap.segments.extend(cap.writer.push(timestamp,frame))
            cap.last_source_at=timestamp
        except Exception as exc:self._fail(cap,'capture_failure:'+str(exc)[:100])

    def ingest(self, camera, timestamp, frame):
        if camera in self.ended_sources:raise ValueError('frame_after_source_end')
        clock = self.cameras[camera]
        t = self._time(self._time(timestamp) + self._time(clock.offset))
        if camera in self.last and t <= self.last[camera]:
            for e in self.events.values():
                if camera in e['captures']:
                    self._fail(e['captures'][camera], 'source_clock_regression')
            raise ValueError('source clock regression')
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3
                or frame.shape[2] != 3 or frame.shape[0]*frame.shape[1] > self.wc['max_frame_pixels']):
            raise ValueError('invalid frame')
        self.last[camera] = t
        for event in self.events.values():
            if camera in event['captures']:
                self._feed(event, event['captures'][camera], t, frame)
        q = self.buffers[camera]
        # No temporary copy larger than the global prebuffer cap.
        if frame.nbytes > self.buffer_bytes:
            q.clear()
            return
        while (sum(len(b) for b in self.buffers.values()) >= self.max_frames
               or sum(f.nbytes for b in self.buffers.values() for _, f in b) + frame.nbytes > self.buffer_bytes):
            oldest = min((k for k,b in self.buffers.items() if b), key=lambda k:self.buffers[k][0][0])
            self.buffers[oldest].popleft()
        q.append((t, frame.copy()))
        while q and t-q[0][0] > self.buffer_seconds:
            q.popleft()
        while (sum(len(b) for b in self.buffers.values()) > self.max_frames
               or sum(f.nbytes for b in self.buffers.values() for _, f in b) > self.buffer_bytes):
            oldest = min((k for k,b in self.buffers.items() if b), key=lambda k:self.buffers[k][0][0])
            self.buffers[oldest].popleft()

    def end_source(self, camera, reason='source_ended'):
        """Called only after all decoded final frames for this source are emitted."""
        if camera not in self.cameras:raise ValueError('unexpected camera')
        if reason not in ('source_ended','reference_drain_cutoff','source_failed'):raise ValueError('source end reason')
        self.ended_sources[camera]=reason
        for event in self.events.values():
            cap=event['captures'].get(camera)
            if cap is not None and not cap.done:self._fail(cap,reason)

    def discover(self, event_id, *, start, discovered_at, camera_snapshot):
        start, discovered_at = self._time(start), self._time(discovered_at)
        if event_id in self.events or len(self.events) >= self.max_events:
            raise ValueError('duplicate event or event admission exhausted')
        snapshot = tuple(camera_snapshot)
        if not snapshot or len(set(snapshot)) != len(snapshot) or any(c not in self.cameras for c in snapshot):
            raise ValueError('invalid camera snapshot')
        required = len(snapshot) * self.sc['max_bytes']
        admitted = self.reserved + required <= self.global_bytes
        event = dict(start=start-self.pre, end=None, captures={}, reservation=required if admitted else 0)
        self.events[event_id] = event
        if admitted:
            self.reserved += required
        for camera in snapshot:
            cap = Capture()
            event['captures'][camera] = cap
            clock = self.cameras[camera]
            reason = None
            if camera in self.ended_sources:
                reason = self.ended_sources[camera]
            elif not admitted:
                reason = 'global_spool_admission'
            elif not self.manual_review and (not clock.verified or not math.isfinite(clock.error_bound) or not 0 <= clock.error_bound <= self.error):
                reason = 'clock_unverified_or_skew'
            elif not start <= discovered_at <= start + self.delay:
                reason = 'late_discovery'
            buffered = list(self.buffers[camera])
            predecessors = [(t,f) for t,f in buffered if t <= event['start']]
            seed = predecessors[-1] if predecessors else None
            frames = [(t,f) for t,f in buffered if t > event['start']]
            if reason is None and (seed is None or
                    event['start']-seed[0] > self.wc['max_input_gap_seconds'] or
                    (buffered and buffered[-1][0] > discovered_at + 1e-8)):
                if not self.manual_review:
                    reason = 'missing_prebuffer_or_discovery_order'
                elif not buffered:
                    reason='missing_prebuffer'
                elif seed is None:
                    cap.reasons.append('missing_pre_roll')
                    seed=buffered[0]
                    frames=buffered[1:]
            if reason:
                self._fail(cap, reason)
                continue
            try:
                spool_id=uuid4().hex
                if self.before_spool_create is not None:
                    self.before_spool_create(event_id,camera,spool_id)
                cap.spool = SegmentSpool(self.root / spool_id, **self.sc)
                cap.writer = TimestampSegmentWriter(cap.spool, **self.wc)
                cap.seed = dict(source_sample_at=seed[0], output_sample_at=event['start'],
                                hold_seconds=event['start']-seed[0], clock_error_bound=clock.error_bound)
                self._feed(event, cap, max(event['start'],seed[0]) if self.manual_review else event['start'], seed[1])
                cap.last_source_at = seed[0]
                for t, frame in frames:
                    self._feed(event, cap, t, frame)
            except Exception as exc:
                self._fail(cap, 'capture_setup_failure:' + str(exc)[:100])
        return self.status(event_id)

    def end(self, event_id, *, violation_end):
        event = self.events[event_id]
        boundary = self._time(violation_end) + self.post
        if self.capture_output_fps is not None:
            wire_boundary = required_capture_end(
                datetime.fromtimestamp(event['start'] + self.pre, tz=timezone.utc),
                datetime.fromtimestamp(violation_end, tz=timezone.utc),
                self.capture_output_fps).timestamp()
            # Keep the encoder on its original grid. Only serialization rounds
            # this actual boundary to the shared microsecond wire obligation.
            frames = round((wire_boundary-event['start']) * self.capture_output_fps)
            boundary = event['start'] + frames / self.capture_output_fps
        if event['end'] is not None or violation_end < event['start'] + self.pre:
            raise ValueError('invalid or repeated end')
        event['end'] = boundary
        for cap in event['captures'].values():
            if not cap.done and cap.writer.last_timestamp >= boundary:
                self._fail(cap, 'end_arrived_after_capture_boundary')
        return self.status(event_id)

    def maintain(self, *, now):
        now = self._time(now)
        for q in self.buffers.values():
            while q and (now - q[0][0] > self.buffer_seconds or q[0][0] > now):
                q.popleft()
        for event in self.events.values():
            for cap in event['captures'].values():
                if cap.spool:
                    try:
                        cap.spool.maintain()
                        if cap.spool.discard_receipts()['items'] or cap.spool.discard_receipts()['reconcile_required']:
                            self._fail(cap, 'source_discarded_or_expired')
                    except Exception as exc:
                        self._fail(cap, 'maintenance_failure:' + str(exc)[:100])
                if not self.manual_review and not cap.done and now-cap.last_source_at > self.wc['max_input_gap_seconds']:
                    self._fail(cap, 'source_gap_or_missing_postbuffer')

    def status(self, event_id):
        event = self.events[event_id]
        details = {c:dict(done=p.done, reasons=list(p.reasons), segments=list(p.segments), seed=dict(p.seed))
                   for c,p in event['captures'].items()}
        complete = event['end'] is not None and all(p.done and not p.reasons for p in event['captures'].values())
        return dict(start=event['start'], end=event['end'], complete=complete, cameras=details)

    def release(self, event_id):
        """Release reservation only after caller has drained all raw sources.

        No upload is performed here. Caller owns durable mapping, redaction,
        acknowledgment and disposition reporting; never use this prototype live.
        """
        event = self.events[event_id]
        for cap in event['captures'].values():
            if not cap.done or (cap.spool and (cap.spool.entries or cap.spool.active_segment_id)):
                raise ValueError('capture or raw media still owned')
        for cap in event['captures'].values():
            if cap.spool:
                receipts = cap.spool.discard_receipts()
                if receipts['items'] or receipts['reconcile_required']:
                    raise ValueError('unreported dispositions remain')
        for cap in event['captures'].values():
            if cap.spool:
                cap.spool.close()
                for name in ('journal.json', 'journal.tmp', '.lock'):
                    (cap.spool.root / name).unlink(missing_ok=True)
                cap.spool.root.rmdir()
        self.reserved -= event['reservation']
        del self.events[event_id]

    def close(self):
        for q in self.buffers.values():
            q.clear()
        for event in self.events.values():
            for cap in event['captures'].values():
                if not cap.done:
                    self._fail(cap, 'coordinator_shutdown')
                if cap.spool:
                    cap.spool.close()
