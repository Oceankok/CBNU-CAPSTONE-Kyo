"""Secret-free best-effort failure breadcrumbs for an existing session only."""
import json
import os
from pathlib import Path
from uuid import UUID, uuid4
import requests

CODES=frozenset('''source_clock_configuration_required source_clock_version source_clock_proof_expired
source_clock_server_contract_changed source_clock_server_restarted source_clock_proof_binding_invalid
source_clock_gate_wait_expired source_clock_historical_proof_required source_clock_historical_observation_invalid
source_clock_anchor_changed source_clock_observation_invalid source_clock_chunk_proof_expired
source_clock_fresh_sample_required clock_number_invalid clock_policy_invalid clock_anchor_invalid
clock_monotonic_regressed clock_wall_step clock_sample_timing_invalid clock_sample_uncertain
capture_stop_unconfirmed_context_kept_open capture_process_failed_context_kept_open
capture_completion_unverified camera_reader_stop_unconfirmed archive_capacity_exceeded
observation_stop_marker_already_exists observation_stop_marker_missing observation_stop_marker_binding_mismatch
observation_stop_acknowledgement_mismatch observation_stop_acknowledgement_missing normal_stop_peer_mismatch normal_stop_peer_timeout
acknowledged_clip_cleanup_pending cleanup_pending'''.split())
TYPES=(requests.exceptions.Timeout,requests.exceptions.ConnectionError,requests.exceptions.HTTPError,
       requests.exceptions.ChunkedEncodingError,requests.exceptions.ContentDecodingError,
       requests.exceptions.RequestException,
       ValueError,RuntimeError,TimeoutError,BrokenPipeError,PermissionError,FileNotFoundError,
       FileExistsError,IsADirectoryError,NotADirectoryError,OSError)


def record_failure(root,run_id,component,error):
    """A failed diagnostic must never change original cleanup or exit behavior."""
    try:
        if component not in ('sender','capture') or str(UUID(run_id))!=run_id:return
        root=Path(root).resolve();state=json.loads((root/'session.json').read_text(encoding='utf-8'))
        if state.get('run_id')!=run_id:return
        name=next((cls.__name__ for cls in TYPES if isinstance(error,cls)),'OtherError')
        code=str(error);code=code if code in CODES else None
        result={'run_id':run_id,'component':component,'exception_type':name,'code':code}
        if isinstance(error,OSError):
            for field in ('errno','winerror'):
                value=getattr(error,field,None)
                if type(value) is int:result[field]=value
            if getattr(error,'sender_stage',None) == 'atomic_json':result['stage']='atomic_json'
        if isinstance(error,requests.exceptions.HTTPError):
            status=getattr(error.response,'status_code',None)
            if type(status) is int and 100<=status<=599:result['http_status']=status
        dest=root/f'.failure-{component}-{run_id}.json';tmp=root/f'.failure-{component}-{uuid4().hex}.tmp'
        try:
            with tmp.open('x',encoding='utf-8') as stream:
                json.dump(result,stream);stream.flush();os.fsync(stream.fileno())
            # Publish a complete file once; a later recovery failure cannot
            # erase the first cause. Windows rename refuses an existing target.
            try:
                if os.name=='nt':tmp.rename(dest)
                else:os.link(tmp,dest)
            except FileExistsError:
                pass
        finally:tmp.unlink(missing_ok=True)
    except Exception:
        return


def record_runner_failure(runner, error):
    """Runner lookup is diagnostic too and must not mask the original error."""
    try:
        record_failure(runner.root,runner.state['run_id'],'sender',error)
    except Exception:
        return
