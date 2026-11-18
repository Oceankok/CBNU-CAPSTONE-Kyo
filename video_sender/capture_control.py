"""Opt-in capture completion evidence for the context runner, never for legacy use."""
import json
import math
import os
from pathlib import Path
import time
from uuid import UUID, uuid4


PAIRED_STOP_TIMEOUT_SECONDS = 10


def run_id_from_env():
    raw = os.environ.get('VIDEO_CAPTURE_RUN_ID')
    if raw is None:
        return None
    return str(UUID(raw))


def stop_requested(directory, run_id):
    return run_id is not None and (Path(directory) / f'.stop-{run_id}').is_file()


def atomic_json(path, value):
    # Independent writers must never share a partially written temporary file.
    temporary=path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(value,f);f.flush();os.fsync(f.fileno())
        for attempt in range(3):
            try:
                os.replace(temporary,path)
                break
            except OSError as error:
                # Windows sharing/lock violations can clear when a reader closes.
                if getattr(error,'winerror',None) not in (32,33) or attempt == 2:
                    raise
                time.sleep(.05 * (attempt+1))
    except OSError as error:
        error.sender_stage='atomic_json'
        raise
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass  # Never mask the original failure.


def normal_stop_path(directory, run_id):
    return Path(directory) / f'.normal-stop-{run_id}'


def normal_stop_requested(directory, run_id):
    return normal_stop_path(directory, run_id).is_file()


def read_stop_peer(directory, run_id):
    body=normal_stop_path(directory, run_id).read_text(encoding='utf-8')
    if not body:
        return None
    peer=json.loads(body)
    if (not isinstance(peer,dict) or set(peer)!={'directory','run_id'} or
        not isinstance(peer['directory'],str) or not Path(peer['directory']).is_absolute() or
        not isinstance(peer['run_id'],str) or str(UUID(peer['run_id']))!=peer['run_id'] or peer['run_id']==run_id or
        Path(peer['directory']).resolve()==Path(directory).resolve()):
        raise ValueError('normal_stop_peer_mismatch')
    return peer


def observation_stop_time_path(directory, run_id):
    return Path(directory) / f'.observation-stop-time-{run_id}.json'


def observation_stop_path(directory, run_id):
    return Path(directory) / f'.observation-stop-{run_id}.json'


class PostRoll:
    def __init__(self, directory, run_id, stream_id, start_sequence, fps, *, monotonic=None):
        self.monotonic = monotonic or time.monotonic
        self.directory = directory
        self.run_id = run_id
        self.stream_id = str(UUID(stream_id))
        self.start_sequence = start_sequence
        self.fps = fps
        self.first_frame = None
        self.first_time = None
        self.first_source_time = None
        self.request_seen = False
        self.peer = None
        self.peer_boundary = None
        self.deadline = None

    def check_deadline(self):
        if not self.request_seen and normal_stop_requested(self.directory, self.run_id):
            self.peer = read_stop_peer(self.directory, self.run_id)
            self.request_seen = True
            if self.peer is not None:
                self.deadline = self.monotonic() + PAIRED_STOP_TIMEOUT_SECONDS
        if self.deadline is not None and self.monotonic() >= self.deadline:
            raise RuntimeError('normal_stop_peer_timeout')

    def read_peer_boundary(self):
        directory, run_id = self.peer['directory'], self.peer['run_id']
        try:
            boundary = json.loads(observation_stop_time_path(directory, run_id).read_text(encoding='utf-8'))
            session = json.loads((Path(directory) / 'session.json').read_text(encoding='utf-8'))
            reciprocal = read_stop_peer(directory, run_id)
        except FileNotFoundError:
            return None
        if (not isinstance(session,dict) or not isinstance(boundary,dict) or
            session.get('run_id') != run_id or session.get('normal_stop_enabled') is not True or
            reciprocal != {'directory':str(Path(self.directory).resolve()),'run_id':self.run_id} or
            any(type(boundary.get(field)) not in (int,float) or not math.isfinite(boundary[field])
                for field in ('monotonic', 'source_time'))):
            raise ValueError('normal_stop_peer_mismatch')
        return boundary

    def before_frame(self, frame_count, observed_monotonic, proof_id, source_time=None):
        self.check_deadline()
        if self.first_frame is not None or not self.request_seen:
            return
        chunk_offset, frame_index = divmod(frame_count, self.fps)
        marker = observation_stop_path(self.directory, self.run_id)
        if marker.exists():
            raise ValueError('observation_stop_marker_already_exists')
        atomic_json(marker, {
            'stream_id': self.stream_id,
            'run_id': self.run_id,
            'sequence': self.start_sequence + chunk_offset,
            'frame_index': frame_index,
            'fps': self.fps,
            'proof_id': proof_id,
        })
        self.first_frame = frame_count
        self.first_time = observed_monotonic
        self.first_source_time = source_time
        if self.peer is not None:
            atomic_json(observation_stop_time_path(self.directory,self.run_id),{
                'monotonic':observed_monotonic,'source_time':source_time,
            })

    def complete(self, frame_count, observed_monotonic, source_time=None):
        if self.first_frame is None or frame_count - self.first_frame < 2 * self.fps + 2:
            return False
        if observed_monotonic - self.first_time < 2:
            return False
        if self.peer is None:
            return True
        if self.peer_boundary is None:
            self.peer_boundary = self.read_peer_boundary()
        if self.peer_boundary is None:
            return False
        # Both timestamps use clocks shared by the two local capture processes.
        duration = 2 + 2 / self.fps
        return (observed_monotonic >= max(self.first_time,self.peer_boundary['monotonic']) + duration and
                source_time >= max(self.first_source_time,self.peer_boundary['source_time']) + duration)


def publish_result(directory, run_id, *, stopped_by_request, ffmpeg_exit, start_sequence, frame_count, fps, protective_stop_reason=None, normal_stop_completed=False):
    if run_id is None:
        return
    root=Path(directory)
    final = start_sequence + (frame_count-1)//fps if frame_count else start_sequence-1
    complete = (stopped_by_request is True and ffmpeg_exit == 0 and
                not any(root.glob('chunk_*.mp4.part')))
    record={'run_id':run_id,'capture_stopped':complete,'ffmpeg_exit':ffmpeg_exit,
            'start_sequence':start_sequence,'final_sequence':final,'frame_count':frame_count}
    if normal_stop_completed and complete:
        record['normal_stop_completed']=True
    if protective_stop_reason in ('spool_soft_stop_threshold', 'spool_free_space_reserve'):
        record['protective_stop_reason']=protective_stop_reason
    path=root / f'.capture-result-{run_id}.json'
    temp=path.with_suffix('.tmp')
    with temp.open('x',encoding='utf-8') as f:
        json.dump(record,f); f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
