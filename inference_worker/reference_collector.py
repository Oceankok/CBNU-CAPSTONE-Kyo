"""Collect group reference footage on the server PC; no detector or GPU required."""
import argparse
from datetime import timedelta
import logging
import math
from pathlib import Path
import tempfile
import time

import httpx

from inference_worker.event_pipeline import _retry
from inference_worker.time_utils import parse_capture_time
from inference_worker.video_client import VideoClient

logger = logging.getLogger(__name__)


class ReferenceUnavailable(ValueError):
    pass


def select_context_stream(request, streams):
    """Select exactly the request's stream/context tuple, never by camera only."""
    candidates = [row for row in streams if row.get('camera_id') == request.get('camera_id')]
    expected_stream = request.get('stream_id')
    expected = request.get('capture_context_id')
    if expected_stream is not None:
        candidates = [row for row in candidates if row.get('stream_id') == expected_stream]
    if expected is not None:
        candidates = [row for row in candidates if row.get('capture_context_id') == expected]
    elif expected_stream is None and any(row.get('capture_context_id') is not None for row in candidates):
        raise ReferenceUnavailable('reference_context_required')
    if len(candidates) != 1:
        raise ReferenceUnavailable('reference_stream_ambiguous')
    selected = candidates[0]
    for field in ('capture_context_id', 'capture_session_id', 'assignment_version'):
        if request.get(field) is not None and selected.get(field) != request[field]:
            raise ReferenceUnavailable('reference_stream_binding_mismatch')
    return selected


def select_chunks(chunks, start, end, *, final_sequence=None, capture_context_id=None):
    """Require one continuous interval, never fill missing footage with other time."""
    selected = sorted((row for row in chunks
        if parse_capture_time(row['capture_start_at']) < end
        and parse_capture_time(row['capture_start_at']) + timedelta(milliseconds=row['duration_ms']) > start
        and (final_sequence is None or row.get('sequence', final_sequence) <= final_sequence)
        and (capture_context_id is None or row.get('capture_context_id') == capture_context_id)),
        key=lambda row: parse_capture_time(row['capture_start_at']))
    covered = start
    for prior, row in zip(selected, selected[1:]):
        if row.get('sequence') is not None and prior.get('sequence') is not None:
            if row['sequence'] != prior['sequence'] + 1:
                return []
    for row in selected:
        stamp = parse_capture_time(row['capture_start_at'])
        if (stamp-covered).total_seconds() > 0.25:
            return []
        covered = max(covered, stamp+timedelta(milliseconds=row['duration_ms']))
    return selected if selected and (end-covered).total_seconds() <= 0.25 else []


def assemble(client, chunks, start, end, folder):
    """Stream frames into a bounded 5fps/640x480 reference clip; do not retain raw frames."""
    import cv2
    output = folder / 'reference.mp4'
    source = folder / 'chunk.mp4'
    writer = None
    shape = None
    last = first = None
    written = 0
    next_frame = start
    try:
        for row in chunks:
            source.write_bytes(client.download_chunk(row['chunk_id']))
            cap = cv2.VideoCapture(str(source))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                if not cap.isOpened() or not math.isfinite(fps) or not 5 <= fps <= 60:
                    raise ReferenceUnavailable('reference_decode_failed')
                index = 0
                stamp = parse_capture_time(row['capture_start_at'])
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    timestamp = stamp + timedelta(seconds=index/fps)
                    index += 1
                    if index > 180 or frame.shape[0]*frame.shape[1] > 1920*1080:
                        raise ReferenceUnavailable('reference_chunk_invalid')
                    if timestamp < start or timestamp > end or timestamp < next_frame:
                        continue
                    if last is not None and (timestamp-last).total_seconds() > 0.45:
                        raise ReferenceUnavailable('reference_frame_gap')
                    if writer is None:
                        height, width = frame.shape[:2]
                        ratio = min(640/width, 480/height, 1)
                        shape = (max(2, int(width*ratio)//2*2), max(2, int(height*ratio)//2*2))
                        writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*'mp4v'), 5, shape)
                        if not writer.isOpened():
                            raise RuntimeError('reference_encoder_unavailable')
                        first = timestamp
                    writer.write(cv2.resize(frame, shape))
                    written += 1
                    last = timestamp
                    next_frame = start + timedelta(seconds=written/5)
                if abs(index/fps*1000-row['duration_ms']) > 250:
                    raise ReferenceUnavailable('reference_incomplete_decode')
            finally:
                cap.release()
                source.unlink(missing_ok=True)
    finally:
        if writer:
            writer.release()
    if not written or (first-start).total_seconds() > 0.25 or (end-last).total_seconds() > 0.45:
        raise ReferenceUnavailable('reference_incomplete_clip')
    if not output.is_file() or not output.stat().st_size:
        raise RuntimeError('reference_encoder_failed')
    return output


class ReferenceCollector:
    def __init__(self, client, *, clock=time.time):
        self.client = client
        self.clock = clock

    def unavailable(self, request, code):
        _retry(lambda: self.client.report_clip_failure(request['event_id'], request['request_id'],
                                                       code, status='unavailable'))

    def _deadline_reached(self, request, end):
        return self.clock() >= request['deadline_at'] or self.clock() - end.timestamp() >= 30

    def collect(self, request, streams):
        start = parse_capture_time(request['clip_start_at'])
        end = parse_capture_time(request['clip_end_at'])
        if not 0 < (end-start).total_seconds() <= 20:
            self.unavailable(request, 'reference_window_unsupported')
            return
        if request.get('capture_context_state') == 'unavailable':
            self.unavailable(request, request.get('capture_context_error', 'reference_context_unavailable'))
            return
        if request.get('capture_context_state') == 'pending' and request.get('stream_id') is None:
            if self._deadline_reached(request, end):
                self.unavailable(request, 'reference_interval_unavailable')
            return
        try:
            stream = select_context_stream(request, streams)
        except ReferenceUnavailable as error:
            self.unavailable(request, str(error))
            return
        chunks = self.client.list_chunks(stream['stream_id'])
        expected_context = request.get('capture_context_id')
        if expected_context is not None and any(
                row.get('capture_context_id') != expected_context for row in chunks):
            self.unavailable(request, 'reference_chunk_context_mismatch')
            return
        chosen = select_chunks(
            chunks, start, end,
            final_sequence=stream.get('final_sequence') if stream.get('capture_stopped') else None,
            capture_context_id=expected_context,
        )
        if not chosen:
            if self._deadline_reached(request, end):
                self.unavailable(request, 'reference_interval_unavailable')
            return
        try:
            with tempfile.TemporaryDirectory(prefix='ppe_reference_') as directory:
                clip = assemble(self.client, chosen, start, end, Path(directory))
                data = clip.read_bytes()
                try:
                    _retry(lambda: self.client.upload_clip(request['event_id'], request['request_id'], data))
                except httpx.HTTPError:
                    # The upload may have committed. Leave uncertain requests for the next poll.
                    current = self.client.get_clip_requests(request['event_id'])
                    if not any(row['request_id'] == request['request_id'] and row['status'] == 'received'
                               for row in current['items']):
                        raise
        except ReferenceUnavailable as error:
            self.unavailable(request, str(error))

    def run_once(self):
        requests = self.client.list_reference_requests()
        if not requests:
            return
        streams = self.client.list_streams()
        for request in requests:
            try:
                self.collect(request, streams)
            except httpx.HTTPStatusError as error:
                # Deletion, concurrent upload and an unavailable source must not stop peers.
                if error.response.status_code not in {404, 409}:
                    logger.warning('Reference request deferred: %s', request['request_id'])
            except (httpx.TransportError, OSError, RuntimeError, ValueError):
                logger.warning('Reference collection deferred: %s', request['request_id'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--poll', type=float, default=1)
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.poll) or args.poll <= 0:
        parser.error('--poll must be finite and positive')
    logging.basicConfig(level=logging.INFO)
    client = VideoClient()
    collector = ReferenceCollector(client)
    try:
        while not (args.stop_file and args.stop_file.exists()):
            try:
                collector.run_once()
            except httpx.HTTPError:
                logger.warning('Reference server unavailable; will retry')
            if args.once:
                break
            time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == '__main__':
    main()
