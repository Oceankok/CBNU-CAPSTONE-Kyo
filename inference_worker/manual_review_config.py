"""Bounded defaults for the configured manual-review whole-incident runner.

Clock zeros below are identity mappings for scheduling source-stamped frames,
NOT measured clock accuracy. Wire reports explicitly send unknown clock values.
"""
import hashlib
import os
from pathlib import Path


def configuration(args):
    cameras=list(dict.fromkeys([args.camera_id, *getattr(args,'reference_camera_id',[])]))
    endpoint=os.environ.get('PPE_SERVER_URL','http://127.0.0.1:8000').rstrip('/')
    key=hashlib.sha256((endpoint+'\0'+args.camera_id).encode()).hexdigest()[:24]
    state=Path(os.environ.get('PPE_CONNECTED_REVIEW_STATE_DIR','.private_media/connected-review')).absolute()/key
    return dict(state_directory=str(state),
        clocks={c:dict(offset=0,error_bound=0,verified=False,epoch='unverified-source-utc',valid_until=None) for c in cameras},
        capture=dict(max_clock_error=0,pre_seconds=2,post_seconds=2,discovery_delay_limit=6,
            prebuffer_seconds=8,prebuffer_bytes=args.buffer_mib*1024*1024,max_buffer_frames=512,max_events=16,
            global_spool_bytes=1024*1024*1024,manual_review=True,
            spool_config=dict(max_bytes=32*1024*1024,max_segments=512,max_age_seconds=3600,max_attempts=20),
            writer_config=dict(segment_seconds=30,output_fps=15,max_frame_pixels=640*480,max_input_gap_seconds=.25,encoder_timeout_seconds=20)),
        mux=dict(max_bytes=256*1024*1024,max_frames=512,max_frame_pixels=4096*2160,max_clock_error=0,manual_review=True),
        outbox=dict(max_db_bytes=32*1024*1024,max_records=20000,max_body_bytes=131072,source_ttl_seconds=3600),
        delivery=dict(max_delivery_records=10000,max_delivery_db_bytes=16*1024*1024),
        transport=dict(max_upload_bytes=32*1024*1024,timeout_seconds=20,max_response_bytes=131072,total_seconds=60),
        lifecycle=dict(missing_grace_seconds=args.episode_missing_grace_seconds,threshold_seconds=3,
            max_sample_gap_seconds=.5,progress_emit_interval_seconds=1,review_episode_continuity=True,
            evidence_mode='manual_review'),
        source=dict(max_chunk_bytes=32*1024*1024,max_chunk_gap_seconds=.25,require_capture_context=True,
                    tolerate_source_gaps=True),
        decoder=dict(max_bytes=32*1024*1024,max_frames=300,max_frame_pixels=4096*2160,min_fps=1,max_fps=60,
            duration_tolerance_ms=250,ttl_seconds=60,max_audit_records=1000,max_metadata_bytes=2*1024*1024),
        metadata_steps_per_pump=16,max_lag_seconds=60)
