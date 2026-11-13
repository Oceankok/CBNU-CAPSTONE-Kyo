"""Explicit opt-in continuous runner. Legacy runner and detector are unchanged.

Requires a fully specified local JSON configuration; no operating grace, clock,
retention or memory defaults are invented here. Never enables automatic judgment.
"""
import json,math,os,time,inspect
from pathlib import Path
from inference_worker.video_client import VideoClient
from inference_worker.person_tracker import PersonTracker
from inference_worker.ppe_rules import assess_ppe,assess_helmets
from inference_worker.work_area_policy import resolve_person_policy
from inference_worker.policy import ppe_policy_status
from inference_worker.event_validation import valid_ppe_confidence
from inference_worker.inference_load import InferenceLoad
from inference_worker.time_utils import parse_capture_time
from inference_worker.capture_outbox import CaptureOutbox
from inference_worker.lifecycle_journal import LifecycleJournal
from inference_worker.multicamera_capture import MultiCameraCapture,CameraClock
from inference_worker.continuous_source_mux import SourceMux,SourceMuxError
from inference_worker.continuous_source_reader import ContinuousSourceReader,SourceUnavailableError
from inference_worker.continuous_runtime import ContinuousFrameRuntime
from inference_worker.continuous_dispatcher import ContinuousDispatcher
from inference_worker.continuous_transport import PrivateClipTransport
from inference_worker.cleanup_supervisor import CleanupSupervisor
from inference_worker.private_chunk_decoder import PrivateChunkDecoder


def check_source_progress(mux,*,now,elapsed,max_lag_seconds):
    """Bound initial no-frame waiting too; timeout is never an EOS signal."""
    if mux.latest:mux.check_stall(now=now,max_lag_seconds=max_lag_seconds)
    elif elapsed>=max_lag_seconds:raise SourceMuxError('source_initial_frame_timeout')


def policy_assessments(rows,policy,shape,*,review_only):
    height,width=shape[:2]
    for row in rows:
        try:
            resolved=resolve_person_policy(policy,row['person_bbox'],width,height)
            scope=(resolved.get('policy_version'),resolved.get('scope'),
                   tuple(resolved.get('area_ids') or []),tuple(sorted(resolved.get('required_ppe') or [])))
            row['policy_scope_key']=scope
        except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError):resolved={}
        states=row.get('ppe') or {'helmet':row}
        for ppe,state in states.items():
            enabled=ppe_policy_status(resolved,ppe)[0]
            if not enabled:state['status']='unknown';state['verified_ppe_confidence']=None
            elif review_only:state['verified_ppe_confidence']=None
            elif state.get('status') not in (ppe,'unknown') and not valid_ppe_confidence(state.get('verified_ppe_confidence')):
                state['status']='unknown';state['verified_ppe_confidence']=None
    return rows


def read_config(path,primary,*,now):
    config=json.loads(Path(path).read_text())
    required={'state_directory','clocks','capture','mux','outbox','delivery','transport',
              'lifecycle','source','decoder','metadata_steps_per_pump','max_lag_seconds'}
    if not isinstance(config,dict) or set(config)!=required:raise ValueError('continuous config keys must be explicit')
    clocks=config['clocks']
    if not isinstance(clocks,dict) or primary not in clocks or len(clocks)<2:
        raise ValueError('primary and reference clock snapshot required')
    for camera,row in clocks.items():
        if (not isinstance(camera,str) or not camera or set(row)!=
                {'offset','error_bound','verified','epoch','valid_until'} or row['verified'] is not True
                or not isinstance(row['epoch'],str) or not row['epoch']
                or parse_capture_time(row['valid_until']).timestamp()<=now
                or not math.isfinite(row['offset']) or not math.isfinite(row['error_bound']) or row['error_bound']<0):
            raise ValueError('explicit current camera calibration required')
    directory=Path(config['state_directory'])
    if not directory.is_absolute():raise ValueError('absolute private state directory required')
    if not math.isfinite(config['max_lag_seconds']) or config['max_lag_seconds']<=0:
        raise ValueError('explicit source lag limit required')
    from inference_worker.segment_spool import SegmentSpool,TimestampSegmentWriter
    checks=[(CaptureOutbox,dict(directory=None,private_root=None),config['outbox']),
            (PrivateChunkDecoder,dict(owner=None),config['decoder']),
            (MultiCameraCapture,dict(root=None,cameras={}),config['capture']),
            (SourceMux,dict(camera_ids=[],primary_camera=primary,clock_error_by_camera={}),config['mux']),
            (ContinuousDispatcher,dict(outbox=None,transport=None,enabled=True),config['delivery']),
            (PrivateClipTransport,dict(server_url=None,token=None,private_root=None),config['transport']),
            (LifecycleJournal,dict(outbox=None,camera_id=primary,camera_snapshot=[],model_version='check'),config['lifecycle']),
            (ContinuousSourceReader,dict(client=None,mux=None,offsets={}),config['source']),
            (SegmentSpool,dict(root=None),config['capture'].get('spool_config',{})),
            (TimestampSegmentWriter,dict(spool=None),config['capture'].get('writer_config',{}))]
    for cls,fixed,values in checks:
        if not isinstance(values,dict) or set(values)&({'clock','transport','decoder','downloader','before_spool_create'}|set(fixed)):
            raise ValueError('configuration cannot override dependencies')
        try:inspect.signature(cls).bind(**fixed,**values)
        except TypeError as exc:raise ValueError('incomplete or unknown configuration fields') from exc
    if config['lifecycle']['threshold_seconds']!=3 or config['capture']['pre_seconds']!=2 or config['capture']['post_seconds']!=2:
        raise ValueError('continuous API threshold/roll contract')
    return config


def run(args):
    if not (args.submit_events or args.submit_review_candidates) or args.replay_buffer:
        raise ValueError('continuous mode requires explicit submission and forbids replay')
    config=read_config(args.continuous_config,args.camera_id,now=time.time())
    from inference_worker.run_server import create_detector
    detector=create_detector(args.model_path) if args.model_path is not None else create_detector()
    review=args.submit_review_candidates
    if (review and getattr(detector,'supports_no_helmet',None) is not False) or (
            args.submit_events and getattr(detector,'supports_no_helmet',None) is False):
        raise ValueError('submission mode does not match existing detector capability')
    client=VideoClient();priority=InferenceLoad(client)
    box=transport=dispatcher=capture=runtime=mux=decoder=None
    stop_reason='runner_stopped_or_failed'
    try:
        state=Path(config['state_directory']);clocks=config['clocks'];ids=list(clocks)
        box=CaptureOutbox(state/'journal',private_root=state/'raw',**config['outbox'])
        transport=PrivateClipTransport(server_url=client.server_url,token=os.environ['PPE_PROCESSING_NODE_TOKEN'],
            private_root=state/'raw',**config['transport'])
        dispatcher=ContinuousDispatcher(box,transport,enabled=True,**config['delivery'])
        capture=MultiCameraCapture(state/'raw',cameras={c:CameraClock(r['offset'],r['error_bound'],True) for c,r in clocks.items()},**config['capture'])
        journal=LifecycleJournal(box,camera_id=args.camera_id,camera_snapshot=ids,
            model_version=detector.model_version,**config['lifecycle'])
        runtime=ContinuousFrameRuntime(journal=journal,capture=capture,dispatcher=dispatcher,
            metadata_steps_per_pump=config['metadata_steps_per_pump'])
        mux=SourceMux(camera_ids=ids,primary_camera=args.camera_id,
            clock_error_by_camera={c:r['error_bound'] for c,r in clocks.items()},**config['mux'])
        decoder=PrivateChunkDecoder(box,**config['decoder'])
        reader=ContinuousSourceReader(client,mux,decoder=decoder,offsets={c:r['offset'] for c,r in clocks.items()},**config['source'])
        tracker=PersonTracker();policy_signature=None;processed=0;primary_sequence=None
        source_started=time.monotonic()
        while processed<args.max_chunks and not(args.stop_file and args.stop_file.exists()):
            now=time.time()
            if any(parse_capture_time(r['valid_until']).timestamp()<=now for r in clocks.values()):
                raise ValueError('camera calibration expired')
            policy=client.get_ppe_policy(args.camera_id)
            signature=json.dumps(policy,sort_keys=True,allow_nan=False)
            if policy_signature is not None and signature!=policy_signature:
                raise ValueError('policy changed; fresh capture session required')
            policy_signature=signature
            try:
                batch=reader.poll()
            except SourceMuxError as error:
                if (str(error) not in ('session_stopped','primary_source_ended')
                        or set(mux.ended)!=set(mux.cameras) or mux.queue):
                    raise
                # Every final frame has passed through runtime before the reader
                # signals EOS. Finish bounded delivery, never poll a new stream.
                from inference_worker.eos_delivery import drain_delivery
                result=drain_delivery(runtime)
                print(json.dumps({'continuous_delivery_tail':result},sort_keys=True))
                if result['state']!='drained':
                    raise SourceMuxError('eos_delivery_pending')
                stop_reason=str(error)
                return result
            journal.bind_capture_context(reader.primary_capture_context())
            remaining={c:sum(1 for row in batch if row[0]==c) for c in ids}
            ended=mux.drained_sources()
            for camera in ended:
                if not remaining[camera]:runtime.end_source(camera,reader.end_reasons[camera])
            for camera,timestamp,frame in batch:
                assessments=None
                if camera==args.camera_id:
                    priority.refresh();detections=detector.detect(frame)
                    rows=tracker.update(assess_ppe(detections,frame.shape,getattr(detector,'supported_ppe',('helmet','vest')))
                        if review else assess_helmets(detections))
                    assessments=policy_assessments(rows,policy,frame.shape,review_only=review)
                runtime.frame(camera,timestamp,frame,assessments=assessments)
                remaining[camera]-=1
                if camera in ended and not remaining[camera]:runtime.end_source(camera,reader.end_reasons[camera])
            had_frames=bool(batch)
            batch.clear();priority.release()
            if mux.last_emitted is not None:
                runtime.pump(now=mux.last_emitted)
            spools={cap.spool.root.name:cap.spool for event in capture.events.values()
                    for cap in event['captures'].values() if cap.spool}
            CleanupSupervisor(box,spools=spools,verified_receipt=dispatcher.cleanup_receipt_verified).run_once()
            runtime.release_finished()
            current=reader.sequence.get(args.camera_id)
            if current!=primary_sequence:
                if primary_sequence is not None:processed+=1
                primary_sequence=current
            if not had_frames:
                check_source_progress(mux,now=now,elapsed=time.monotonic()-source_started,
                                      max_lag_seconds=config['max_lag_seconds'])
                time.sleep(args.poll)
    except SourceMuxError as error:
        stop_reason=str(error)
        if isinstance(error,SourceUnavailableError) and box:
            box.record_source_failure({key:getattr(error,key) for key in
                ('reason','camera','stream','expected_sequence','availability_reason')})
        raise
    finally:
        # Attempt every cleanup even if one disk/decoder operation fails.
        cleanup_error=None
        callbacks=[]
        if runtime:callbacks.append(lambda:runtime.stop(stop_reason))
        elif capture:callbacks.append(capture.close)
        if mux:callbacks.append(mux.close)
        if decoder:callbacks.append(decoder.close)
        callbacks.append(priority.release)
        if dispatcher:callbacks.append(dispatcher.close)
        if transport:callbacks.append(transport.close)
        if box:callbacks.append(box.close)
        callbacks.append(client.close)
        for cleanup in callbacks:
            try:cleanup()
            except Exception as error:
                if cleanup_error is None:cleanup_error=error
        if cleanup_error is not None:raise cleanup_error
