"""Opt-in frame coordinator glue; no automatic runner registration.

Caller feeds verified common-epoch frames in mux order from one owner thread.
Network pumping is explicit and bounded per call. Detector/ROI policy is external.
"""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
from inference_worker.continuous_payloads import segment_payload,capture_payload
from inference_worker.continuous_transport import UploadPlan
from inference_worker.segment_spool import SpoolError


class ContinuousFrameRuntime:
    def __init__(self, *, journal, capture, dispatcher, metadata_steps_per_pump):
        if type(metadata_steps_per_pump) is not int or metadata_steps_per_pump<=0:
            raise ValueError('dispatch budget')
        if journal.lifecycle.threshold!=3 or capture.pre!=2 or capture.post!=2:
            raise ValueError('API three-second qualification and two-second roll contract')
        if capture.events:raise ValueError('new frame session requires empty coordinator')
        self.journal,self.capture,self.dispatcher=journal,capture,dispatcher
        self.outbox=journal.outbox;self.budget=metadata_steps_per_pump
        self.cameras=self.outbox._camera_ids(journal.snapshot)
        if set(self.cameras)!=set(capture.cameras):raise ValueError('camera snapshot mismatch')
        if capture.root.absolute()!=self.outbox.root:raise ValueError('private root mismatch')
        capture.before_spool_create=lambda event,camera,spool:self.outbox.begin_capture(
            event,camera=camera,spool_directory=spool)
        self.bound=set();self.manifests={};self.reports=set();self.closed=False;self.media_watermark=None

    def frame(self,camera,timestamp,frame,*,assessments=None):
        if self.closed:raise ValueError('runtime closed')
        # Reader/mux already corrected the timestamp. Capture applies its own
        # immutable offset, so undo it here rather than double-correcting.
        self.capture.ingest(camera,timestamp-self.capture.cameras[camera].offset,frame)
        self.media_watermark=timestamp
        transitions=[]
        if camera==self.journal.lifecycle.camera_id:
            if assessments is None:raise ValueError('primary assessments required')
            warm=all(self.capture.buffers[c] and self.capture.buffers[c][0][0]<=timestamp-self.capture.pre
                     for c in self.cameras if c not in self.capture.ended_sources)
            transitions=self.journal.update(assessments if warm else [],timestamp)
            for row in transitions:
                if row['kind']=='start':
                    self.capture.discover(row['event_id'],start=row['start_time'],
                        discovered_at=timestamp,camera_snapshot=self.cameras)
                elif row['kind']=='close':self.capture.end(row['event_id'],violation_end=row['end_time'])
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

    def _enqueue(self,event,kind,key,body):
        seq=self.outbox.event_status(event)['next_seq']
        return self.outbox.enqueue(event,seq=seq,kind=kind,idempotency_key=key,body=body)

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
                if not (self._stamp(status['start']) <= self._stamp(segment.capture_start)
                        < self._stamp(segment.capture_end) <= self._stamp(status['end'])):
                    continue
                try:
                    path=cap.spool.path_for_upload(segment.segment_id)
                except SpoolError:
                    continue  # Expired/disposed source is never revived.
                if path is not None:result[camera].append(segment)
        return result

    def _partial_manifests(self,event,status):
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
            options=[s for s in candidates if self._stamp(s.capture_start)==cursor]
            if not options:break
            segment=max(options,key=lambda s:s.capture_end)
            self._enqueue(event,'manifest',event+':manifest:'+str(count),
                segment_payload(segment,session_id=self.outbox.session,event_sequence=count))
            count+=1;cursor=self._stamp(segment.capture_end)
        return count

    def pump(self,*,now):
        self.capture.maintain(now=now);self._bind_sources();self._drain_metadata()
        for intent in self.outbox.pending_close_intents():
            event=intent['event']
            if intent['state']!='pending' or event not in self.capture.events:continue
            status=self.capture.status(event)
            if not all(row['done'] for row in status['cameras'].values()):continue
            if not status['complete'] or self.outbox.event_status(event)['state']=='incomplete':
                self.outbox.mark_incomplete(event,reason='camera_capture_incomplete')
                # No silently shortened success. Detailed raw/source failure is
                # retained by capture/outbox; complete close is never fabricated.
                self._partial_manifests(event,status)
                self._drain_metadata()
                existing=self.outbox.db.execute("SELECT ack FROM requests WHERE event=? AND kind='manifest'",(event,)).fetchall()
                if any(row['ack'] is None for row in existing):continue
                self.outbox.finalize_pending_close(event,complete=False,final_segment_count=len(existing))
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
                intervals[(body['capture_start_at'],body['capture_end_at'])]=body['sequence']
                for segment in json.loads(row['ack'])['segments']:
                    requests[(segment['camera_id'],segment['sequence'])]=segment
            safe=self._safe_segments(event,status)
            report_status=deepcopy(status)
            for camera in self.cameras:
                report_status['cameras'][camera]['segments']=safe[camera]
                if partial and not report_status['cameras'][camera]['reasons']:
                    report_status['cameras'][camera]['reasons']=['event_capture_incomplete']
            for camera,cap in record['captures'].items():
                key=(event,camera)
                if key not in self.reports:
                    self._enqueue(event,'capture_report',camera+':'+event+':report1',
                        capture_payload(report_status,camera_id=camera,clock=self.capture.cameras[camera],
                            session_id=self.outbox.session,sequence=1,time_basis='source_utc_corrected',required_end=status['end']))
                    self.reports.add(key)
                for segment in safe[camera]:
                    from inference_worker.continuous_payloads import _time
                    sequence=intervals.get((_time(segment.capture_start),_time(segment.capture_end)))
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
            released.append(event)
        return released

    def stop(self,reason):
        if self.closed:return
        try:
            self.journal.close_all(reason)
            for event in self.capture.events:
                if self.outbox.event_status(event)['state']=='active':
                    self.outbox.mark_incomplete(event,reason=reason)
        finally:
            self.capture.close();self.closed=True
