"""Opt-in capture completion evidence for the context runner, never for legacy use."""
import json
import os
from pathlib import Path
from uuid import UUID


def run_id_from_env():
    raw = os.environ.get('VIDEO_CAPTURE_RUN_ID')
    if raw is None:
        return None
    return str(UUID(raw))


def stop_requested(directory, run_id):
    return run_id is not None and (Path(directory) / f'.stop-{run_id}').is_file()


def publish_result(directory, run_id, *, stopped_by_request, ffmpeg_exit, start_sequence, frame_count, fps, protective_stop_reason=None):
    if run_id is None:
        return
    root=Path(directory)
    final = start_sequence + (frame_count-1)//fps if frame_count else start_sequence-1
    complete = (stopped_by_request is True and ffmpeg_exit == 0 and
                not any(root.glob('chunk_*.mp4.part')))
    record={'run_id':run_id,'capture_stopped':complete,'ffmpeg_exit':ffmpeg_exit,
            'start_sequence':start_sequence,'final_sequence':final,'frame_count':frame_count}
    if protective_stop_reason in ('spool_soft_stop_threshold', 'spool_free_space_reserve'):
        record['protective_stop_reason']=protective_stop_reason
    path=root / f'.capture-result-{run_id}.json'
    temp=path.with_suffix('.tmp')
    with temp.open('x',encoding='utf-8') as f:
        json.dump(record,f); f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
