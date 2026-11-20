"""Opt-in frame coordinator glue; no automatic runner registration.

Caller feeds verified common-epoch frames in mux order from one owner thread.
Network pumping is explicit and bounded per call. Detector/ROI policy is external.
"""
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
from inference_worker.continuous_payloads import segment_payload,capture_payload,WIRE_TIME_EPSILON
from inference_worker.continuous_transport import UploadPlan
from inference_worker.segment_spool import SpoolError


class ContinuousFrameRuntime:
    def __init__(self, *, journal, capture, dispatcher, metadata_steps_per_pump):
        if type(metadata_steps_per_pump) is not int or metadata_steps_per_pump<=0:
            raise ValueError('dispatch budget')
        if journal.lifecycle.threshold!=3 or capture.pre!=2 or capture.post!=2:
            raise ValueError('API three-second qualification and two-second roll contract')
        if capture.events:raise ValueError('new frame session requires empty coordinator')
        if journal.capture_output_fps != capture.capture_output_fps:
            raise ValueError('capture output grid contract mismatch')
        self.journal,self.capture,self.dispatcher=journal,capture,dispatcher
        self.outbox=journal.outbox;self.budget=metadata_steps_per_pump
        self.cameras=self.outbox._camera_ids(journal.snapshot)
        if set(self.cameras)!=set(capture.cameras):raise ValueError('camera snapshot mismatch')
        if capture.root.absolute()!=self.outbox.root:raise ValueError('private root mismatch')
        capture.before_spool_create=lambda event,camera,spool:self.outbox.begin_capture(
            event,camera=camera,spool_directory=spool)
        self.bound=set();self.manifests={};self.reports=set();self.provisional_reports=set();self.closed=False;self.media_watermark=None
        self.observation_cutoffs={};self.observation_closed=False;self.last_observation_timestamp=None

    def set_observation_cutoff(self,camera,timestamp):
        if camera not in self.cameras or isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp):
            raise ValueError('observation cutoff')
        if camera in self.observation_cutoffs:
            if self.observation_cutoffs[camera]!=timestamp:raise ValueError('observation cutoff changed')
            return
        if camera==self.journal.lifecycle.camera_id and self.last_observation_timestamp is not None and self.last_observation_timestamp>=timestamp:
            raise ValueError('observation cutoff arrived late')
        self.observation_cutoffs[camera]=timestamp

    def observing(self,camera,timestamp):
        return camera==self.journal.lifecycle.camera_id and timestamp<self.observation_cutoffs.get(camera,math.inf)

    def frame(self,camera,timestamp,frame,*,assessments=None,startup_evidence=False):
        if self.closed:raise ValueError('runtime closed')
        # Reader/mux already corrected the timestamp. Capture applies its own
        # immutable offset, so undo it here rather than double-correcting.
        capture_frame=frame
        if self.capture.manual_review and frame.shape[0]*frame.shape[1]>self.capture.wc['max_frame_pixels']:
            import cv2
            ratio=(self.capture.wc['max_frame_pixels']/(frame.shape[0]*frame.shape[1]))**.5
            capture_frame=cv2.resize(frame,(max(2,int(frame.shape[1]*ratio)//2*2),max(2,int(frame.shape[0]*ratio)//2*2)))
        self.capture.ingest(camera,timestamp-self.capture.cameras[camera].offset,capture_frame)
        self.media_watermark=max(timestamp,self.media_watermark) if self.media_watermark is not None else timestamp
        transitions=[]
        if not startup_evidence and self.observing(camera,timestamp):
            if assessments is None:raise ValueError('primary assessments required')
            transitions=self.journal.update(assessments,timestamp)
            self.last_observation_timestamp=timestamp
            for row in transitions:
                if row['kind']=='start':
                    self.capture.discover(row['event_id'],start=row['start_time'],
                        discovered_at=timestamp,camera_snapshot=self.cameras)
                elif row['kind']=='close':self.capture.end(row['event_id'],violation_end=row['end_time'])
        elif not startup_evidence and camera==self.journal.lifecycle.camera_id and not self.observation_closed:
            transitions=self.journal.close_all('operator_stop')
            for row in transitions:
                if row['kind']=='close':self.capture.end(row['event_id'],violation_end=row['end_time'])
            self.observation_closed=True
        self._bind_sources()
        return transitions

    def end_source(self,camera,reason='source_ended'):
        if camera in self.capture.ended_sources:return
        if camera==self.journal.lifecycle.camera_id:
            for row in self.journal.close_all('primary_source_ended'):
                if row['kind']=='close':self.capture.end(row['event_id'],violation_end=row['end_time'])
        self.capture.end_source(camera,reason)

    def _bind_sources(self):
        for event,record in self.capture.events.items():
            for camera,cap in record['captures'].items():
                if cap.spool is None:continue
                for segment in cap.segments:
                    if segment.segment_id in self.bound:continue
                    self.outbox.bind_segment(event,camera=camera,segment=segment,
                        relative_path=cap.spool.root.name+'/'+segment.segment_id+'.mp4')
                    self.bound.add(segment.segment_id)

    def _enqueue(self,event,kind,key,body,**kwargs):
        seq=self.outbox.event_status(event)['next_seq']
        return self.outbox.enqueue(event,seq=seq,kind=kind,idempotency_key=key,body=body,**kwargs)

    def _drain_metadata(self):
        for _ in range(self.budget):
            if self.media_watermark is not None:self.capture.maintain(now=self.media_watermark)
            self.outbox.reconcile()
            result=self.dispatcher.metadata_once(max_items=1)
            if any(state in ('metadata_response_binding','camera_snapshot_mismatch') for _,state in result):
                raise ValueError('server continuous contract or camera snapshot mismatch')
            if not result or any(state!='acknowledged' for _,state in result):break

    @staticmethod
    def _stamp(value):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    def _safe_segments(self, event, status):
        """Only complete, still-owned files wholly inside the final interval."""
        result={}
        for camera,cap in self.capture.events[event]['captures'].items():
            result[camera]=[]
            for segment in cap.segments:
                if self.outbox.segment_status(segment.segment_id)['state']!='present':continue
                if not (self._stamp(status['start'])-WIRE_TIME_EPSILON <= self._stamp(segment.capture_start)
                        < self._stamp(segment.capture_end) <= self._stamp(status['end'])+WIRE_TIME_EPSILON):
                    continue
                try:
                    path=cap.spool.path_for_upload(segment.segment_id)
                except SpoolError:
                    continue  # Expired/disposed source is never revived.
                if path is not None:result[camera].append(segment)
        return result

    def _partial_manifests(self,event,status,*,scoped_partial=False):
        if self.capture.manual_review or scoped_partial:
            rows=self.outbox.db.execute("SELECT body FROM requests WHERE event=? AND kind='manifest' ORDER BY seq",(event,)).fetchall()
            bodies=[json.loads(row['body']) for row in rows]
            known={(b['camera_id'],b['capture_start_at'],b['capture_end_at']) for b in bodies}
            from inference_worker.continuous_payloads import _time
            counts={camera:sum(b.get('camera_id')==camera for b in bodies) for camera in self.cameras}
            for camera,segments in self._safe_segments(event,status).items():
                for segment in segments:
                    key=(camera,_time(segment.capture_start),_time(segment.capture_end))
                    if key in known:continue
                    body=segment_payload(segment,session_id=self.outbox.session,event_sequence=counts[camera])
                    body['camera_id']=camera
                    self._enqueue(event,'manifest',event+':manifest:'+camera+':'+str(counts[camera]),body)
                    counts[camera]+=1;known.add(key)
            return counts[self.journal.lifecycle.camera_id]
        # Extend only a contiguous prefix. Never fabricate a missing camera file,
        # bridge a source gap, replace an existing manifest or truncate bytes.
        rows=self.outbox.db.execute(
            "SELECT body FROM requests WHERE event=? AND kind='manifest' ORDER BY seq",(event,)).fetchall()
        bodies=[json.loads(row['body']) for row in rows]
        cursor=self._stamp(status['start']) if not bodies else datetime.fromisoformat(bodies[-1]['capture_end_at'].replace('Z','+00:00'))
        safe=self._safe_segments(event,status)
        candidates=[s for segments in safe.values() for s in segments]
        count=len(bodies)
        while True:
            options=[s for s in candidates if self._stamp(s.capture_start)==cursor
                     or (count==0 and abs(self._stamp(s.capture_start)-cursor)<=WIRE_TIME_EPSILON)]
            if not options:break
            segment=max(options,key=lambda s:s.capture_end)
            self._enqueue(event,'manifest',event+':manifest:'+str(count),
                segment_payload(segment,session_id=self.outbox.session,event_sequence=count))
            count+=1;cursor=self._stamp(segment.capture_end)
        return count

    def _reconcile_final_boundary(self,event,status):
        # Future in-flight events only. Never revise advertised/terminal metadata.
        if self.outbox.db.execute("SELECT 1 FROM requests WHERE event=? AND kind IN ('manifest','close','incomplete')",
                                  (event,)).fetchone():return
        from inference_worker.final_capture_boundary import frame_overlap,trim_segment
        for camera,cap in self.capture.events[event]['captures'].items():
            if getattr(cap,'final_boundary_checked',False):continue
            cap.final_boundary_checked=True  # One bounded attempt; no encoder retry loop.
            kept=[]
            for segment in cap.segments:
                if status['start']<=segment.capture_start<segment.capture_end<=status['end']:
                    kept.append(segment);continue
                try:
                    if self.outbox.segment_status(segment.segment_id)['state']!='present':
                        kept.append(segment);continue
                    first,stop=frame_overlap(segment,status['start'],status['end'])
                    if stop>first:
                        derived=trim_segment(cap.spool,segment,first,stop,
                            timeout=self.capture.wc['encoder_timeout_seconds'],
                            max_pixels=self.capture.wc['max_frame_pixels'])
                        # Persist derived ownership BEFORE retiring its original.
                        self.outbox.bind_segment(event,camera=camera,segment=derived,
                            relative_path=cap.spool.root.name+'/'+derived.segment_id+'.mp4')
                        self.bound.add(derived.segment_id)
                        kept.append(derived)
                        reason='final_boundary_replaced_'+derived.segment_id
                    else:
                        reason='final_boundary_no_overlap'
                    self.outbox.request_cleanup(segment.segment_id,reason=reason)
                    self.bound.discard(segment.segment_id)
                except Exception:
                    # Original bytes/custody remain intact; advertise no false success.
                    kept.append(segment)
                    if 'final_boundary_trim_failed' not in cap.reasons:
                        cap.reasons.append('final_boundary_trim_failed')
                    self.outbox.mark_incomplete(event,reason='final_boundary_trim_failed')
            cap.segments=kept

    def pump(self,*,now):
        self.capture.maintain(now=now);self._bind_sources();self._drain_metadata()
        for intent in self.outbox.pending_close_intents():
            event=intent['event']
            if intent['state']!='pending' or event not in self.capture.events:continue
            status=self.capture.status(event)
            if not all(row['done'] for row in status['cameras'].values()):continue
            partial_strict=(not self.capture.manual_review and (not status['complete'] or self.outbox.event_status(event)['state']=='incomplete'))
            if self.capture.manual_review or partial_strict:
                self._reconcile_final_boundary(event,status)
                status=self.capture.status(event)
            if self.capture.manual_review or partial_strict:
                if not status['complete'] or self.outbox.event_status(event)['state']=='incomplete':
                    self.outbox.mark_incomplete(event,reason='camera_capture_incomplete')
                # No silently shortened success. Detailed raw/source failure is
                # retained by capture/outbox; complete close is never fabricated.
                if partial_strict:
                    # Close intent persisted the final progress before this report.
                    # Never submit ranges beyond the server's acknowledged end.
                    if self.outbox.db.execute("SELECT 1 FROM requests WHERE event=? AND kind IN ('start','progress') AND ack IS NULL",(event,)).fetchone():continue
                    safe=self._safe_segments(event,status)
                    report_status=deepcopy(status)
                    for camera in self.cameras:
                        report_status['cameras'][camera]['segments']=safe[camera]
                        cap_status=report_status['cameras'][camera]
                        if not cap_status['reasons'] and len(safe[camera])!=len(status['cameras'][camera]['segments']):
                            cap_status['reasons']=['source_segment_unavailable']
                        key=(event,camera)
                        if key not in self.reports and key not in self.provisional_reports:
                            # Validate true full coverage before advertising a healthy
                            # camera as collecting while the event is still open.
                            report=capture_payload(report_status,camera_id=camera,clock=self.capture.cameras[camera],
                                session_id=self.outbox.session,sequence=1,time_basis='source_utc_corrected',required_end=status['end'])
                            if report['state']=='complete':
                                cap_status['done']=False
                                report=capture_payload(report_status,camera_id=camera,clock=self.capture.cameras[camera],
                                    session_id=self.outbox.session,sequence=1,time_basis='source_utc_corrected',required_end=status['end'])
                            self._enqueue(event,'capture_report',camera+':'+event+':report1',report)
                            if report['state']=='collecting':self.provisional_reports.add(key)
                            else:self.reports.add(key)
                    self._drain_metadata()
                    if self.outbox.db.execute("SELECT 1 FROM requests WHERE event=? AND kind='capture_report' AND ack IS NULL",(event,)).fetchone():continue
                prior=self.outbox.db.execute("SELECT body FROM requests WHERE event=? AND kind='manifest' ORDER BY seq",(event,)).fetchall()
                has_incomplete_report=any(json.loads(row['body']).get('state')=='incomplete' for row in self.outbox.db.execute("SELECT body FROM requests WHERE event=? AND kind='capture_report'",(event,)))
                scoped_partial=partial_strict and has_incomplete_report and not any(json.loads(row['body']).get('camera_id') is None for row in prior)
                count=self._partial_manifests(event,status,scoped_partial=scoped_partial)
                self._drain_metadata()
                existing=self.outbox.db.execute("SELECT ack FROM requests WHERE event=? AND kind='manifest'",(event,)).fetchall()
                if any(row['ack'] is None for row in existing):continue
                if not (self.capture.manual_review or scoped_partial):count=len(existing)
                complete=(self.capture.manual_review and status['complete']
                          and self.outbox.event_status(event)['state']!='incomplete')
                self.outbox.finalize_pending_close(event,complete=complete,final_segment_count=count)
                continue
            rows=[status['cameras'][camera]['segments'] for camera in self.cameras]
            if len({len(row) for row in rows})!=1:raise ValueError('camera segment grid mismatch')
            count=len(rows[0]);registered=self.manifests.setdefault(event,set())
            for sequence in range(count):
                first=rows[0][sequence]
                if any((r[sequence].capture_start,r[sequence].capture_end)!=(first.capture_start,first.capture_end) for r in rows[1:]):
                    raise ValueError('camera interval mismatch')
                if sequence not in registered:
                    self._enqueue(event,'manifest',event+':manifest:'+str(sequence),
                        segment_payload(first,session_id=self.outbox.session,event_sequence=sequence))
                    registered.add(sequence)
            self._drain_metadata()
            # A bounded pump may not have drained all network work yet.
            if self.outbox.db.execute("SELECT 1 FROM requests WHERE event=? AND kind='manifest' AND ack IS NULL",(event,)).fetchone():
                continue
            # Network time may have expired a source since the earlier snapshot.
            # Re-enter the failure path next pass; never finalize a stale success.
            if self.outbox.event_status(event)['state']=='incomplete' or not self.capture.status(event)['complete']:
                continue
            # Finalizer requires every manifest ACK and atomically stores close.
            self.outbox.finalize_pending_close(event,complete=True,final_segment_count=count)
        self._drain_metadata()
        planned={row['segment'] for row in self.dispatcher.status()}
        for event,record in self.capture.events.items():
            local=self.outbox.event_status(event)
            status=self.capture.status(event)
            partial=local['state']=='incomplete'
            if local['state'] not in ('closed','incomplete') or status['end'] is None:continue
            if not all(row['done'] for row in status['cameras'].values()):continue
            if not self.outbox.db.execute(
                    "SELECT 1 FROM requests WHERE event=? AND kind IN ('close','incomplete') AND ack IS NOT NULL",(event,)).fetchone():continue
            if not partial and not status['complete']:continue
            # Exact acknowledged manifest response supplies immutable request IDs.
            requests={}
            intervals={}
            for row in self.outbox.db.execute("SELECT body,ack FROM requests WHERE event=? AND kind='manifest' AND ack IS NOT NULL",(event,)):
                body=json.loads(row['body'])
                intervals[(body.get('camera_id'),body['capture_start_at'],body['capture_end_at'])]=body['sequence']
                for segment in json.loads(row['ack'])['segments']:
                    requests[(segment['camera_id'],segment['sequence'])]=segment
            safe=self._safe_segments(event,status)
            report_status=deepcopy(status)
            for camera in self.cameras:
                report_status['cameras'][camera]['segments']=safe[camera]
                if partial and self.capture.manual_review and not report_status['cameras'][camera]['reasons']:
                    report_status['cameras'][camera]['reasons']=['event_capture_incomplete']
                if partial and not self.capture.manual_review and not report_status['cameras'][camera]['reasons'] and len(safe[camera])!=len(status['cameras'][camera]['segments']):
                    report_status['cameras'][camera]['reasons']=['source_segment_unavailable']
            for camera,cap in record['captures'].items():
                key=(event,camera)
                if key not in self.reports:
                    provisional=key in self.provisional_reports
                    sequence=2 if provisional else 1
                    report=capture_payload(report_status,camera_id=camera,clock=self.capture.cameras[camera],
                        session_id=self.outbox.session,sequence=sequence,time_basis='source_utc_unverified' if self.capture.manual_review else 'source_utc_corrected',required_end=status['end'])
                    if provisional and report['state']=='incomplete':
                        first=self.outbox.db.execute("SELECT body FROM requests WHERE event=? AND idem=? AND kind='capture_report'",(event,camera+':'+event+':report1')).fetchone()
                        if first is None:raise ValueError('provisional capture report missing')
                        historical=json.loads(first['body'])
                        # The capture report is cumulative: preserve prior observed
                        # ranges while reporting now-unavailable source as missing.
                        report['captured_ranges']=historical['captured_ranges']
                        report['earliest_available_at']=historical['earliest_available_at']
                        for missing in report['missing_ranges']:
                            missing['reason']='source_segment_unavailable'
                    self._enqueue(event,'capture_report',camera+':'+event+':report'+str(sequence),report,
                        **({'_final_collecting_report':True} if provisional else {}))
                    self.reports.add(key)
                for segment in safe[camera]:
                    from inference_worker.continuous_payloads import _time
                    a,b=_time(segment.capture_start),_time(segment.capture_end)
                    sequence=intervals.get((camera,a,b),intervals.get((None,a,b)))
                    if sequence is None:continue
                    if segment.segment_id in planned:continue
                    binding=requests.get((camera,sequence))
                    if not binding:raise ValueError('manifest request binding missing')
                    path=cap.spool.path_for_upload(segment.segment_id)
                    if not path:continue
                    digest=hashlib.sha256()
                    with path.open('rb') as source:
                        for chunk in iter(lambda:source.read(65536),b''):digest.update(chunk)
                    plan=UploadPlan(event,binding['request_id'],camera,
                        cap.spool.root.name+'/'+segment.segment_id+'.mp4',segment.byte_count,digest.hexdigest(),
                        segment.capture_start,segment.capture_end,status['start'],status['end'],True)
                    self.dispatcher.register_upload(segment.segment_id,plan,session_id=self.outbox.session,role=binding['role'])
        self._drain_metadata()
        return self.dispatcher.upload_once(max_items=1)

    def release_finished(self):
        released=[]
        for event in list(self.capture.events):
            record=self.capture.events[event]
            if self.outbox.event_status(event)['state'] not in ('closed','incomplete'):continue
            if not all(cap.done for cap in record['captures'].values()):continue
            ids={s.segment_id for cap in record['captures'].values() for s in cap.segments}
            try:self.capture.release(event)
            except ValueError:continue  # source/receipt obligations still pending
            self.bound.difference_update(ids);self.manifests.pop(event,None)
            self.reports={key for key in self.reports if key[0]!=event}
            self.provisional_reports={key for key in self.provisional_reports if key[0]!=event}
            released.append(event)
        return released

    def prepare_stop(self,reason):
        """Persist actual last observations and unavailable post-roll before tail."""
        if self.closed:return
        for row in self.journal.close_all(reason):
            if row["kind"]=="close":
                self.capture.end(row["event_id"],violation_end=row["end_time"])
        for camera in self.cameras:
            if camera not in self.capture.ended_sources:
                self.capture.end_source(camera,"source_failed")
        self._bind_sources()
        for event in self.capture.events:
            if self.outbox.event_status(event)["state"]=="active":
                self.outbox.mark_incomplete(event,reason=reason)

    def stop(self,reason):
        if self.closed:return
        try:
            self.prepare_stop(reason)
        finally:
            self.capture.close();self.closed=True
