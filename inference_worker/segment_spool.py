"""Opt-in private raw-video spool; not connected to the live runner.

All limits are caller choices, not production defaults. MP4 files in this
spool are UNREDACTED: never return their paths as public media URLs. A single
OS lock prevents another process from cleaning live files. Completed files
survive restart only within the configured age/attempt/byte limits. Partial
or orphan files are discarded, never submitted as evidence.
"""
from dataclasses import dataclass, asdict
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from uuid import uuid4

import numpy as np


class SpoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    segment_id: str
    sequence: int
    capture_start: float
    capture_end: float  # exclusive output-frame boundary
    frame_count: int
    fps: int
    byte_count: int
    created_at: float
    attempts: int = 0


def _positive_int(name, value):
    if type(value) is not int or value <= 0:
        raise ValueError(name)


def _positive_number(name, value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(name)


def _secure_open(path, flags):
    return os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0), 0o600)


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return wrapped


class SegmentSpool:
    """One camera's bounded raw-media ownership and upload journal.

    max_bytes bounds media plus this spool's journal/lock metadata (not OS
    cache or encoder-internal memory). Metadata reservation is explicit.
    mark_attempt must be persisted BEFORE an upload, ack only after a verified
    idempotent server receipt. exhausted attempts delete raw bytes fail-closed.
    """
    VERSION = 1
    _owned = re.compile(r'^[0-9a-f]{32}\.(?:mp4|part)$')

    def __init__(self, root, *, max_bytes, max_segments, max_age_seconds,
                 max_attempts, clock=time.time):
        for name, value in [('max_bytes', max_bytes), ('max_segments', max_segments),
                            ('max_attempts', max_attempts)]:
            _positive_int(name, value)
        _positive_number('max_age_seconds', max_age_seconds)
        self._state_lock = threading.RLock()
        self.root = Path(root)
        if self.root.is_symlink():
            raise SpoolError('spool_symlink')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_bytes, self.max_segments = max_bytes, max_segments
        self.receipt_limit = 2 * max_segments
        self.journal_limit = 4096 + max_segments * 1024 + self.receipt_limit * 512
        self.metadata_reserve = 2 * self.journal_limit + 1
        if max_bytes <= self.metadata_reserve:
            raise ValueError('max_bytes must exceed journal reservation')
        self.max_age, self.max_attempts, self.clock = max_age_seconds, max_attempts, clock
        self._active = None
        self._inflight = set()
        self._cleanup_pending = set()
        self._active_started_at = None
        self._closed = False
        self._lock_fd = _secure_open(self.root / '.lock', os.O_CREAT | os.O_RDWR)
        self.entries = {}
        self._discards = []
        self.discard_overflow = 0
        self.next_receipt_sequence = 0
        self.next_sequence = 0
        try:
            self._lock()
            self._load()
            self.prune()
        except BaseException:
            os.close(self._lock_fd)
            self._closed = True
            raise

    def _lock(self):
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self._lock_fd).st_size == 0:
                    os.write(self._lock_fd, b'0')
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                msvcrt.locking(self._lock_fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SpoolError('spool_in_use') from exc

    def _load(self):
        marker = self.root / 'journal.json'
        existing = list(self.root.iterdir())
        if any(path.is_symlink() for path in existing):
            raise SpoolError('spool_contains_symlink')
        if not marker.exists():
            if any(path.name != '.lock' for path in existing):
                raise SpoolError('unrecognized_spool_directory')
            self._save()
            return
        try:
            if marker.stat().st_size > self.journal_limit:
                raise ValueError('journal size')
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data['version'] != self.VERSION:
                raise ValueError('version')
            self.next_sequence = data['next_sequence']
            if type(self.next_sequence) is not int or self.next_sequence < 0:
                raise ValueError('sequence')
            self._load_receipts(data)
            raw_entries = data['segments']
            if not isinstance(raw_entries, list) or len(raw_entries) > self.max_segments:
                raise ValueError('segment count')
            for entry in raw_entries:
                row = Segment(**entry)
                if not re.fullmatch('[0-9a-f]{32}', row.segment_id):
                    raise ValueError('segment id')
                if row.segment_id in self.entries:
                    raise ValueError('duplicate segment')
                for field in ('frame_count', 'fps', 'byte_count'):
                    _positive_int(field, getattr(row, field))
                if (type(row.sequence) is not int or not 0 <= row.sequence < self.next_sequence
                        or type(row.attempts) is not int or row.attempts < 0
                        or not all(math.isfinite(x) for x in (row.capture_start, row.capture_end, row.created_at))
                        or row.capture_end <= row.capture_start):
                    raise ValueError('invalid metadata')
                self.entries[row.segment_id] = row
            cleanup = data.get('cleanup_pending', [])
            if (not isinstance(cleanup, list) or len(cleanup) > self.max_segments
                    or any(not isinstance(key, str) or key not in self.entries for key in cleanup)):
                raise ValueError('cleanup_pending')
            self._cleanup_pending = set(cleanup)
        except (KeyError, TypeError, ValueError) as exc:
            raise SpoolError('invalid_spool_journal') from exc
        allowed = {'.lock', 'journal.json', 'journal.tmp'}
        if any(path.name not in allowed and not self._owned.fullmatch(path.name) for path in existing):
            raise SpoolError('unexpected_spool_file')
        # Lock is held: no live producer can own these uncommitted files.
        for path in existing:
            if path.name == 'journal.tmp':
                path.unlink()
            elif path.suffix == '.part':
                size = path.stat().st_size
                self._record_discard(path.stem, 'restart_partial', byte_count=size)
                self._save()
                path.unlink()
            elif path.suffix == '.mp4' and path.stem not in self.entries:
                size = path.stat().st_size
                self._record_discard(path.stem, 'restart_orphan', byte_count=size)
                self._save()
                path.unlink()
        for key, row in list(self.entries.items()):
            path = self.root / (key + '.mp4')
            if not path.is_file() or path.stat().st_size != row.byte_count:
                reason = 'source_missing' if not path.is_file() else 'source_size_mismatch'
                if key not in self._cleanup_pending:
                    self._record_discard(key, reason, row=row)
                    self._cleanup_pending.add(key)
                    self._save()
                path.unlink(missing_ok=True)
                del self.entries[key]
                self._cleanup_pending.discard(key)
        self._save()
        if self.media_bytes > self.max_bytes - self.metadata_reserve:
            # Config was reduced. Discard oldest raw files until bounded again.
            for row in sorted(self.entries.values(), key=lambda row: row.created_at):
                self._remove(row.segment_id, reason=None if row.segment_id in self._cleanup_pending else 'quota_reduced')
                if self.media_bytes <= self.max_bytes - self.metadata_reserve:
                    break

    def _load_receipts(self, data):
        receipts = data.get('discard_receipts', [])
        overflow = data.get('discard_overflow', 0)
        next_sequence = data.get('next_receipt_sequence', 0)
        if (not isinstance(receipts, list) or type(overflow) is not int or overflow < 0
                or type(next_sequence) is not int or next_sequence < 0):
            raise ValueError('discard receipts')
        seen = set()
        for receipt in receipts:
            if (not isinstance(receipt, dict)
                    or set(receipt) != {'receipt_sequence', 'segment_id', 'segment_sequence',
                                       'reason', 'discarded_at', 'byte_count'}
                    or type(receipt['receipt_sequence']) is not int
                    or not 0 <= receipt['receipt_sequence'] < next_sequence
                    or receipt['receipt_sequence'] in seen
                    or not re.fullmatch('[0-9a-f]{32}', receipt['segment_id'])
                    or not isinstance(receipt['reason'], str) or len(receipt['reason']) > 80
                    or not math.isfinite(receipt['discarded_at'])
                    or (receipt['segment_sequence'] is not None and
                        (type(receipt['segment_sequence']) is not int or receipt['segment_sequence'] < 0))
                    or (receipt['byte_count'] is not None and
                        (type(receipt['byte_count']) is not int or receipt['byte_count'] < 0))):
                raise ValueError('invalid discard receipt')
            seen.add(receipt['receipt_sequence'])
        dropped = max(0, len(receipts) - self.receipt_limit)
        self._discards = receipts[-self.receipt_limit:]
        self.discard_overflow = overflow + dropped
        self.next_receipt_sequence = next_sequence

    def _record_discard(self, segment_id, reason, *, row=None, byte_count=None):
        # This is a bounded raw-media disposition log, not an event mapping.
        if any(row['segment_id'] == segment_id and row['reason'] == reason for row in self._discards):
            return
        receipt = {'receipt_sequence': self.next_receipt_sequence,
                   'segment_id': segment_id,
                   'segment_sequence': row.sequence if row is not None else None,
                   'reason': reason, 'discarded_at': self.clock(),
                   'byte_count': row.byte_count if row is not None else byte_count}
        self.next_receipt_sequence += 1
        if len(self._discards) == self.receipt_limit:
            self._discards.pop(0)
            self.discard_overflow += 1
        self._discards.append(receipt)

    @_synchronized
    def discard_receipts(self):
        """Persistent dispositions; overflow REQUIRES full upper-journal reconciliation."""
        self._check()
        return {'items': [dict(row) for row in self._discards],
                'overflow_count': self.discard_overflow,
                'reconcile_required': self.discard_overflow > 0}

    @_synchronized
    def acknowledge_discards(self, receipt_sequences, *, reconciled_overflow=False):
        """Call only after durable incomplete-reporting/event-map reconciliation."""
        self._check()
        sequences = set(receipt_sequences)
        if any(type(value) is not int or value < 0 for value in sequences):
            raise ValueError('receipt_sequences')
        self._discards = [row for row in self._discards if row['receipt_sequence'] not in sequences]
        if reconciled_overflow:
            self.discard_overflow = 0
        self._save()

    @property
    @_synchronized
    def active_segment_id(self):
        return self._active[0] if self._active is not None else None

    def _save(self):
        payload = json.dumps({'version': self.VERSION, 'next_sequence': self.next_sequence,
                              'segments': [asdict(row) for row in self.entries.values()],
                              'cleanup_pending': sorted(self._cleanup_pending),
                              'discard_receipts': self._discards,
                              'discard_overflow': self.discard_overflow,
                              'next_receipt_sequence': self.next_receipt_sequence}).encode('utf-8')
        if len(payload) > self.journal_limit:
            raise SpoolError('journal_size_limit')
        path = self.root / 'journal.tmp'
        fd = _secure_open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, self.root / 'journal.json')

    def _check(self):
        if self._closed:
            raise SpoolError('spool_closed')

    @property
    @_synchronized
    def media_bytes(self):
        return sum(row.byte_count for row in self.entries.values())

    @property
    @_synchronized
    def bytes_used(self):
        return sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())

    def _remove(self, segment_id, *, reason=None):
        row = self.entries.get(segment_id)
        if reason is not None:
            self._record_discard(segment_id, reason, row=row)
            self._cleanup_pending.add(segment_id)
            self._save()  # Persist unusable/cleanup intent before destructive I/O.
        (self.root / (segment_id + '.mp4')).unlink(missing_ok=True)
        self.entries.pop(segment_id, None)
        self._inflight.discard(segment_id)
        self._cleanup_pending.discard(segment_id)
        self._save()

    @_synchronized
    def prune(self):
        self._check()
        now = self.clock()
        if not math.isfinite(now):
            raise SpoolError('invalid_clock')
        removed = []
        for row in list(self.entries.values()):
            if row.segment_id in self._cleanup_pending:
                self._remove(row.segment_id)
                continue
            if now < row.created_at or now - row.created_at >= self.max_age or (row.attempts >= self.max_attempts and row.segment_id not in self._inflight):
                reason = ('clock_rollback' if now < row.created_at else
                          'source_expired' if now - row.created_at >= self.max_age else
                          'attempts_exhausted')
                self._remove(row.segment_id, reason=reason)
                removed.append(row.segment_id)
        return removed

    @_synchronized
    def pending(self):
        self.prune()
        return sorted((row for row in self.entries.values() if row.segment_id not in self._inflight and row.segment_id not in self._cleanup_pending),
                      key=lambda row: row.sequence)

    @_synchronized
    def path_for_upload(self, segment_id):
        self.prune()
        if segment_id not in self.entries or segment_id in self._cleanup_pending:
            raise SpoolError('segment_not_pending')
        return self.root / (segment_id + '.mp4')

    @_synchronized
    def mark_attempt(self, segment_id):
        self.prune()
        row = self.entries.get(segment_id)
        if row is None or segment_id in self._cleanup_pending:
            raise SpoolError('segment_not_pending')
        if segment_id in self._inflight:
            raise SpoolError('segment_upload_in_progress')
        updated = Segment(**{**asdict(row), 'attempts': row.attempts + 1})
        self.entries[segment_id] = updated
        self._save()
        self._inflight.add(segment_id)
        # Retry exhaustion is applied when this attempt fails or after restart.
        # Age expiry still applies: caller must bound upload time and stop on expiry.
        return self.root / (segment_id + '.mp4')

    @_synchronized
    def mark_failed(self, segment_id):
        self._check()
        self._inflight.discard(segment_id)
        self.prune()

    @_synchronized
    def acknowledge(self, segment_id):
        self._check()
        self._inflight.discard(segment_id)
        if segment_id in self.entries:
            self._cleanup_pending.add(segment_id)
            self._save()  # Persist receipt before deleting; never re-upload on unlink failure.
            self._remove(segment_id)

    @_synchronized
    def discard(self, segment_id, *, reason):
        """Delete a known owned source with durable non-delivery disposition.

        Caller must persist event incomplete/cleanup intent first. Unknown IDs
        are never unlinked. Successful upload must use acknowledge instead.
        """
        self._check()
        if not isinstance(reason, str) or not re.fullmatch(r'[a-z0-9_]{1,80}',reason):
            raise ValueError('invalid discard reason')
        if segment_id not in self.entries:
            return False
        self._remove(segment_id, reason=reason)
        return True

    @_synchronized
    def begin(self, start, fps, width, height, *, encoder_timeout=30):
        self._check()
        if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(start):
            raise ValueError('start')
        for name, value in [('fps', fps), ('width', width), ('height', height)]:
            _positive_int(name, value)
        if width % 2 or height % 2:
            raise ValueError('even dimensions required')
        _positive_number('encoder_timeout', encoder_timeout)
        self.prune()
        if self._active is not None:
            raise SpoolError('encoder_already_active')
        if len(self.entries) >= self.max_segments:
            raise SpoolError('segment_count_limit')
        available = self.max_bytes - self.metadata_reserve - self.media_bytes
        if available <= 0:
            raise SpoolError('spool_byte_limit')
        key = uuid4().hex
        encoder = _BoundedEncoder(self.root / (key + '.part'), width, height, fps,
                                  available, encoder_timeout)
        self._active = (key, start, fps, encoder)
        self._active_started_at = self.clock()
        return encoder

    @_synchronized
    def check_active_age(self):
        if self._active is not None and (self.clock() < self._active_started_at or
                                         self.clock() - self._active_started_at >= self.max_age):
            self.abort_active(reason='active_source_expired')
            raise SpoolError('active_source_expired')

    @_synchronized
    def finalize(self, frame_count):
        self._check()
        if self._active is None:
            raise SpoolError('no_active_encoder')
        self.check_active_age()
        key, start, fps, encoder = self._active
        try:
            encoder.finish()
            self.check_active_age()
            path = self.root / (key + '.part')
            size = path.stat().st_size
            if not size or frame_count <= 0:
                raise SpoolError('empty_segment')
            os.replace(path, self.root / (key + '.mp4'))
            row = Segment(key, self.next_sequence, start, start + frame_count / fps,
                          frame_count, fps, size, self._active_started_at)
            self.next_sequence += 1
            self.entries[key] = row
            self._save()
            return row
        except BaseException as error:
            encoder.abort()
            reason = ('byte_quota_exceeded' if 'spool_byte_limit' in str(error) else
                      'encoder_timeout' if 'timeout' in str(error) else 'encoding_failed')
            # check_active_age may already have recorded and removed this file.
            if self._active is not None:
                self._record_discard(key, reason)
                if key in self.entries:
                    self._cleanup_pending.add(key)
                self._save()
            (self.root / (key + '.part')).unlink(missing_ok=True)
            (self.root / (key + '.mp4')).unlink(missing_ok=True)
            self.entries.pop(key, None)
            self._cleanup_pending.discard(key)
            self._save()
            raise
        finally:
            self._active = None

    @_synchronized
    def abort_active(self, *, reason='capture_aborted'):
        if not isinstance(reason, str) or not re.fullmatch('[a-z0-9_]{1,80}', reason):
            raise ValueError('discard reason code')
        if self._active is not None:
            key, _, _, encoder = self._active
            encoder.abort()
            self._record_discard(key, reason)
            self._save()
            (self.root / (key + '.part')).unlink(missing_ok=True)
            self._active = None

    @_synchronized
    def maintain(self):
        """Owner must call periodically even if capture/upload has stalled."""
        self._check()
        removed = self.prune()
        self.check_active_age()
        return removed

    @_synchronized
    def close(self):
        if not self._closed:
            self.abort_active()
            os.close(self._lock_fd)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _BoundedEncoder:
    """Pipe output to an exact byte-capped private file, never encoder file I/O."""
    def __init__(self, path, width, height, fps, byte_limit, timeout):
        executable = shutil.which('ffmpeg')
        if not executable:
            raise SpoolError('ffmpeg_missing')
        self.path, self.limit, self.timeout = path, byte_limit, timeout
        self.error = None
        self.written = 0
        self.process = subprocess.Popen([
            executable, '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo',
            '-pix_fmt', 'bgr24', '-s', f'{width}x{height}', '-r', str(fps),
            '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-threads', '1',
            '-preset', 'ultrafast', '-tune', 'zerolatency', '-pix_fmt', 'yuv420p',
            '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1',
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        try:
            fd = _secure_open(self.path, os.O_CREAT | os.O_WRONLY | os.O_EXCL)
            with os.fdopen(fd, 'wb') as stream:
                while True:
                    chunk = self.process.stdout.read(65536)
                    if not chunk:
                        break
                    if self.written + len(chunk) > self.limit:
                        raise SpoolError('spool_byte_limit')
                    stream.write(chunk)
                    self.written += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException as exc:
            self.error = exc
            if self.process.poll() is None:
                self.process.kill()

    def write(self, frame):
        if self.error is not None:
            raise SpoolError(str(self.error))
        failure = []
        done = threading.Event()
        payload = memoryview(frame).cast('B')

        def feed():
            try:
                remaining = payload
                while remaining:
                    written = os.write(self.process.stdin.fileno(), remaining)
                    if written <= 0:
                        raise BrokenPipeError('encoder stdin closed')
                    remaining = remaining[written:]
            except (BrokenPipeError, OSError, ValueError) as exc:
                failure.append(exc)
            finally:
                done.set()

        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
        if not done.wait(self.timeout):
            self.process.kill()  # Closing the child read end releases blocked os.write.
            self.process.wait(timeout=self.timeout)
            feeder.join(timeout=self.timeout)
            raise SpoolError('encoder_write_timeout')
        feeder.join()
        if failure:
            raise SpoolError(str(self.error or 'encoder_write_failed')) from failure[0]

    def finish(self):
        try:
            self.process.stdin.close()
            code = self.process.wait(timeout=self.timeout)
            self.reader.join(timeout=self.timeout)
            if self.reader.is_alive():
                raise SpoolError('encoder_sink_timeout')
            if self.error is not None:
                raise SpoolError(str(self.error))
            if code != 0:
                raise SpoolError('encoder_failed')
        except (subprocess.TimeoutExpired, BrokenPipeError) as exc:
            raise SpoolError('encoder_timeout_or_closed') from exc
        finally:
            if self.process.poll() is not None:
                self.process.stdout.close()

    def abort(self):
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        self.process.wait(timeout=self.timeout)
        self.reader.join(timeout=self.timeout)
        self.process.stdout.close()


class TimestampSegmentWriter:
    """Timestamp-based sample-and-hold encoding with one retained input frame.

    Gaps beyond max_input_gap_seconds fail closed; no synthetic content bridges
    an unknown long interval. Sampling can repeat/select observed frames, never
    changes detector inputs. finish(end_time) is an EXCLUSIVE frame boundary.
    The caller supplies pre/post-roll frames and maps segments to events.
    """
    def __init__(self, spool, *, segment_seconds, output_fps,
                 max_frame_pixels, max_input_gap_seconds, encoder_timeout_seconds):
        _positive_number('segment_seconds', segment_seconds)
        if segment_seconds > 60:
            raise ValueError('segment_seconds must not exceed current 60-second processing cap')
        for name, value in [('output_fps', output_fps), ('max_frame_pixels', max_frame_pixels)]:
            _positive_int(name, value)
        _positive_number('max_input_gap_seconds', max_input_gap_seconds)
        _positive_number('encoder_timeout_seconds', encoder_timeout_seconds)
        self.encoder_timeout = encoder_timeout_seconds
        frames = math.floor(segment_seconds * output_fps)
        if frames < 1:
            raise ValueError('segment shorter than one frame')
        self.spool, self.fps, self.max_frames = spool, output_fps, frames
        self.max_pixels, self.max_gap = max_frame_pixels, max_input_gap_seconds
        self.previous = None
        self.last_timestamp = None
        self.origin = None
        self.sample_index = 0
        self.segment_frames = 0
        self.encoder = None
        self.shape = None
        self.closed = False

    def _validate_frame(self, frame):
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim != 3 or frame.shape[2] != 3
                or frame.shape[0] < 2 or frame.shape[1] < 2
                or frame.shape[0] % 2 or frame.shape[1] % 2
                or frame.shape[0] * frame.shape[1] > self.max_pixels):
            raise ValueError('invalid or oversized BGR frame')
        if self.shape is not None and frame.shape != self.shape:
            raise ValueError('frame resolution changed')

    def _emit_before(self, boundary):
        self.spool.check_active_age()
        rows = []
        while self.origin + self.sample_index / self.fps < boundary - 1e-9:
            timestamp = self.origin + self.sample_index / self.fps
            if self.encoder is None:
                height, width = self.shape[:2]
                self.encoder = self.spool.begin(timestamp, self.fps, width, height, encoder_timeout=self.encoder_timeout)
            self.encoder.write(self.previous)
            self.sample_index += 1
            self.segment_frames += 1
            if self.segment_frames == self.max_frames:
                rows.append(self.spool.finalize(self.segment_frames))
                self.encoder = None
                self.segment_frames = 0
        return rows

    def push(self, timestamp, frame):
        if self.closed:
            raise SpoolError('writer_closed')
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError('timestamp')
        self._validate_frame(frame)
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            raise ValueError('strict timestamp order required')
        if self.last_timestamp is not None and timestamp - self.last_timestamp > self.max_gap:
            self.abort(reason='input_gap')
            raise SpoolError('input_gap')
        try:
            rows = [] if self.previous is None else self._emit_before(timestamp)
            if self.origin is None:
                self.origin = timestamp
                self.shape = frame.shape
            self.previous = frame.copy()
            self.last_timestamp = timestamp
            return rows
        except BaseException as error:
            reason = ('byte_quota_exceeded' if 'spool_byte_limit' in str(error) else
                      'encoder_timeout' if 'timeout' in str(error) else 'capture_failed')
            self.abort(reason=reason)
            raise

    def finish(self, end_time):
        if self.closed:
            raise SpoolError('writer_closed')
        if (self.previous is None or isinstance(end_time, bool)
                or not isinstance(end_time, (int, float)) or not math.isfinite(end_time)
                or not self.last_timestamp < end_time <= self.last_timestamp + self.max_gap):
            raise ValueError('invalid exclusive end boundary')
        sample_count = (end_time - self.origin) * self.fps
        if abs(sample_count - round(sample_count)) > 1e-5:
            raise ValueError('end boundary must align to output frame grid')
        try:
            rows = self._emit_before(end_time)
            if self.segment_frames:
                rows.append(self.spool.finalize(self.segment_frames))
                self.encoder = None
                self.segment_frames = 0
            self.previous = None
            self.closed = True
            return rows
        except BaseException as error:
            reason = ('byte_quota_exceeded' if 'spool_byte_limit' in str(error) else
                      'encoder_timeout' if 'timeout' in str(error) else 'capture_failed')
            self.abort(reason=reason)
            raise

    def abort(self, *, reason='capture_aborted'):
        self.spool.abort_active(reason=reason)
        self.encoder = None
        self.previous = None
        self.closed = True
