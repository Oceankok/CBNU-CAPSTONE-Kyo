"""Explicit opt-in, single-owner dispatcher. Not imported by the live runner.

Uses CaptureOutbox's lifetime lock. The caller must serialize calls and reserve
both SQLite rollback budgets and upload snapshot space. No automatic looping.
"""
from dataclasses import asdict
import hashlib
import json
import sqlite3
import os
from inference_worker.continuous_transport import UploadPlan


class MetadataBindingError(RuntimeError):pass


class ContinuousDispatcher:
    def __init__(self, outbox, transport, *, enabled=False, max_delivery_records,
                 max_delivery_db_bytes):
        if not enabled:raise ValueError('dispatcher requires explicit enablement')
        if (type(max_delivery_records) is not int or max_delivery_records<=0
                or type(max_delivery_db_bytes) is not int or max_delivery_db_bytes<65536):
            raise ValueError('delivery capacity')
        self.outbox,self.transport=outbox,transport
        self.limit=max_delivery_records;self._metadata_cursor=None
        path=outbox.directory/'delivery.sqlite3'
        outbox._no_symlinks(path)
        for suffix in ('-journal','-wal','-shm'):outbox._no_symlinks(path.with_name(path.name+suffix))
        if not path.exists():
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        page_size=self.db.execute('PRAGMA page_size').fetchone()[0]
        pages=self.db.execute('PRAGMA page_count').fetchone()[0]
        if pages*page_size>max_delivery_db_bytes:
            self.db.close();raise ValueError('existing delivery ledger exceeds budget')
        self.db.execute('PRAGMA max_page_count='+str(max_delivery_db_bytes//page_size))
        self.db.execute('''CREATE TABLE IF NOT EXISTS uploads(
          segment TEXT PRIMARY KEY,event TEXT NOT NULL,session TEXT NOT NULL,role TEXT NOT NULL,
          plan TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,error TEXT)''')
        if 'last_probe' not in {r[1] for r in self.db.execute('PRAGMA table_info(uploads)')}:
            self.db.execute('ALTER TABLE uploads ADD COLUMN last_probe INTEGER NOT NULL DEFAULT 0')
        self.db.commit()
        if self.db.execute('SELECT count(*) FROM uploads').fetchone()[0]>self.limit:
            self.db.close();raise ValueError('existing delivery record count exceeds budget')

    def register_upload(self, segment_id, plan, *, session_id, role):
        if role not in ('clip','reference'):raise ValueError('upload role')
        event=self.outbox.event_status(plan.event_id)
        if event['session']!=session_id:raise ValueError('upload session binding')
        row=self.outbox.segment_status(segment_id)
        if (not row or row['event']!=plan.event_id or row['camera']!=plan.camera_id
                or row['path']!=plan.relative_path or row['bytes']!=plan.byte_count
                or row['start']!=plan.capture_start or row['end']!=plan.capture_end
                or row['state']!='present' or not plan.final_range_verified):
            raise ValueError('upload segment binding')
        payload=json.dumps(asdict(plan),sort_keys=True,separators=(',',':'),allow_nan=False)
        with self.db:
            old=self.db.execute('SELECT * FROM uploads WHERE segment=?',(segment_id,)).fetchone()
            if old:
                if (old['plan'],old['session'],old['role'])!=(payload,session_id,role):
                    raise ValueError('immutable upload binding')
                return
            if self.db.execute('SELECT count(*) FROM uploads').fetchone()[0]>=self.limit:
                raise ValueError('delivery record capacity')
            self.db.execute('INSERT INTO uploads(segment,event,session,role,plan,state,receipt,error) VALUES(?,?,?,?,?,\'queued\',NULL,NULL)',
                (segment_id,plan.event_id,session_id,role,payload))

    def _receipt(self,row,plan):
        return self.transport.recover_receipt(plan,session_id=row['session'],role=row['role'])

    def _accept(self,row,receipt):
        # Durable receipt precedes cleanup obligation. Restart replays this step.
        with self.db:
            self.db.execute("UPDATE uploads SET state='accepted',receipt=?,error=NULL WHERE segment=?",
                (json.dumps(receipt,sort_keys=True,separators=(',',':')),row['segment']))
        self.outbox.request_cleanup(row['segment'],reason='verified_upload_receipt')

    def upload_once(self, *, max_items=None):
        if max_items is not None and (type(max_items) is not int or max_items<=0):raise ValueError('upload step limit')
        self.outbox.reconcile()
        results=[]
        for row in self.db.execute("SELECT * FROM uploads WHERE state!='manual' ORDER BY last_probe,segment").fetchall():
            if max_items is not None and len(results)>=max_items:break
            self.outbox.reconcile()
            plan=UploadPlan(**json.loads(row['plan']))
            if row['state']=='accepted':
                # Do not recreate cleanup after it was already completed.
                source=self.outbox.segment_status(row['segment'])
                if source and source['state']=='present':self._accept(row,json.loads(row['receipt']))
                continue
            with self.db:
                self.db.execute('UPDATE uploads SET last_probe=(SELECT coalesce(max(last_probe),0)+1 FROM uploads) WHERE segment=?',(row['segment'],))
            source=self.outbox.segment_status(row['segment'])
            if not source or source['state']!='present':
                with self.db:self.db.execute("UPDATE uploads SET state='manual',error='source_unavailable' WHERE segment=?",(row['segment'],))
                results.append((row['segment'],'manual'));continue
            event=self.outbox.event_status(plan.event_id)
            close=self.outbox.db.execute("SELECT kind,ack,body FROM requests WHERE event=? AND kind IN ('close','incomplete') ORDER BY seq DESC LIMIT 1",(plan.event_id,)).fetchone()
            close_body=json.loads(bytes(close['body'])) if close else {}
            normal=(event['state']=='closed' and close and close['kind']=='close'
                    and close_body.get('complete') is True)
            partial=(event['state']=='incomplete' and close and close['kind']=='incomplete'
                     and close_body.get('kind')=='close' and close_body.get('complete') is False
                     and close_body.get('session_id')==row['session']==event['session'])
            if not close or close['ack'] is None or not (normal or partial):
                results.append((row['segment'],'waiting_verified_close'));continue
            try:
                receipt=self._receipt(row,plan)
                if receipt is None:
                    if row['state']!='queued':
                        with self.db:self.db.execute("UPDATE uploads SET state='unknown',error='receipt_not_found' WHERE segment=?",(row['segment'],))
                        results.append((row['segment'],'unknown'));continue
                    with self.db:self.db.execute("UPDATE uploads SET state='attempted' WHERE segment=?",(row['segment'],))
                    self.transport.upload(plan)
                    receipt=self._receipt(row,plan)
                if receipt is None:
                    with self.db:self.db.execute("UPDATE uploads SET state='unknown',error='receipt_not_found' WHERE segment=?",(row['segment'],))
                    results.append((row['segment'],'unknown'));continue
                self._accept(row,receipt)
                results.append((row['segment'],'accepted_private'))
            except Exception:
                # Never persist credentials, source paths or remote response bodies.
                with self.db:self.db.execute("UPDATE uploads SET error='delivery_or_recovery_failed' WHERE segment=?",(row['segment'],))
                results.append((row['segment'],'blocked'))
        return results

    def metadata_once(self, *, max_items=None):
        """One persisted head per event; exact bytes remain unchanged on failure."""
        import re,time
        if max_items is not None and (type(max_items) is not int or max_items<=0):raise ValueError('metadata step limit')
        results=[]
        rows=self.outbox.pending()
        if max_items is not None:
            if self._metadata_cursor is not None:
                rows=[r for r in rows if r['event']>self._metadata_cursor]+[r for r in rows if r['event']<=self._metadata_cursor]
            rows=rows[:max_items]
        for row in rows:
            event=row['event'];self._metadata_cursor=event
            if not re.fullmatch('[A-Za-z0-9_-]{1,100}',event):
                results.append((event,'invalid_event'));continue
            payload=bytes(row['body'])
            if hashlib.sha256(payload).hexdigest()!=row['digest']:
                results.append((event,'body_integrity_failed'));continue
            body=json.loads(payload)
            stored=self.outbox.event_status(event)
            if body.get('session_id')!=stored['session']:
                results.append((event,'session_mismatch'));continue
            base='/api/processing/continuous-events'
            if row['kind']=='start':
                if body.get('event_id')!=event:
                    results.append((event,'event_mismatch'));continue
                route=base
            elif row['kind'] in ('progress','close','incomplete'):
                expected_kind='close' if row['kind']=='incomplete' else row['kind']
                if (body.get('kind')!=expected_kind
                        or (row['kind']=='incomplete' and body.get('complete') is not False)):
                    results.append((event,'command_kind_mismatch'));continue
                route=base+'/'+event+'/updates'
            elif row['kind']=='manifest':route=base+'/'+event+'/segments'
            elif row['kind']=='capture_report':
                if body.get('state')=='complete' and stored['state']!='closed':
                    results.append((event,'capture_completion_blocked'));continue
                # Camera route binding is not part of the API report body. Keep
                # it in the durable idempotency key, prefixed by camera ID.
                camera=row['idem'].split(':',1)[0]
                if not re.fullmatch('[A-Za-z0-9_-]{1,100}',camera):
                    results.append((event,'camera_binding_invalid'));continue
                snapshot=json.loads(stored['snapshot'])
                if camera not in self.outbox._camera_ids(snapshot):
                    results.append((event,'camera_binding_invalid'));continue
                route=base+'/'+event+'/cameras/'+camera+'/capture-reports'
            else:
                results.append((event,'unsupported_kind'));continue
            try:
                deadline=time.monotonic()+self.transport.total_seconds
                with self.transport.client.stream('POST',route,headers={'Content-Type':'application/json'},content=payload) as response:
                    if response.status_code not in (200,201):raise ValueError('metadata refused')
                    if response.headers.get('content-encoding','identity').lower()!='identity':raise ValueError('encoded metadata')
                    raw=bytearray()
                    for chunk in response.iter_bytes(chunk_size=min(65536,self.transport.response_max+1)):
                        if time.monotonic()>deadline:raise TimeoutError('metadata deadline')
                        if len(raw)+len(chunk)>self.transport.response_max:raise ValueError('metadata response size')
                        raw.extend(chunk)
                    receipt=json.loads(raw)
                    expected={'kind':('close' if row['kind']=='incomplete' else row['kind']),
                        'session_id':stored['session'],
                        'sequence':0 if row['kind']=='start' else body.get('sequence'),
                        'camera_id':camera if row['kind']=='capture_report' else None}
                    if (not isinstance(receipt,dict) or receipt.get('event_id')!=event
                            or receipt.get('accepted_command')!=expected):
                        raise MetadataBindingError('metadata_response_binding')
                if row['kind']=='start':
                    cameras=receipt.get('cameras')
                    expected_ids=self.outbox._camera_ids(json.loads(stored['snapshot']))
                    if (not isinstance(cameras,list)
                            or sorted(c.get('camera_id','') for c in cameras)!=sorted(expected_ids)
                            or any(c.get('role')!=('clip' if c.get('camera_id')==body['camera_id'] else 'reference') for c in cameras)):
                        raise MetadataBindingError('camera_snapshot_mismatch')
                self.outbox.acknowledge(event,row['seq'],receipt=receipt)
                results.append((event,'acknowledged'))
            except MetadataBindingError as error:results.append((event,str(error)))
            except Exception:results.append((event,'blocked'))
        return results

    def verified_receipt(self,segment_id):
        row=self.db.execute("SELECT receipt FROM uploads WHERE segment=? AND state='accepted'",(segment_id,)).fetchone()
        return json.loads(row['receipt']) if row else None

    def cleanup_receipt_verified(self, segment):
        row=self.db.execute("SELECT * FROM uploads WHERE segment=? AND state='accepted'",(segment['id'],)).fetchone()
        if not row:return False
        plan=json.loads(row['plan']);receipt=json.loads(row['receipt'])
        return (receipt.get('accepted') is True and receipt.get('event_id')==segment['event']==plan['event_id']
            and receipt.get('camera_id')==segment['camera']==plan['camera_id']
            and receipt.get('request_id')==plan['request_id']
            and receipt.get('role')==row['role'] and bool(receipt.get('media_id'))
            and receipt.get('source_sha256')==plan['sha256']
            and receipt.get('source_bytes')==segment['bytes']==plan['byte_count'])

    def status(self):
        return [dict(row) for row in self.db.execute('SELECT segment,event,state,error FROM uploads ORDER BY segment')]

    def close(self):self.db.close()
