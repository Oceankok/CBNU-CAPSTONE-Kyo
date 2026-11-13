"""Private transactional capture ledger. No runner, HTTP, or public-media wiring.

One process owns the journal; callers serialize calls. Completed request bodies
are immutable bytes. Cleanup is delegated to SegmentSpool, never arbitrary unlink.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from uuid import uuid4


class OutboxError(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite timestamp required')
    return value


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or any(ord(c)<32 for c in value):
        raise ValueError('invalid identifier')
    return value


class CaptureOutbox:
    def __init__(self, directory, *, private_root, max_db_bytes, max_records,
                 max_body_bytes, source_ttl_seconds, clock=time.time):
        for value in (max_db_bytes, max_records, max_body_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('positive integer capacity required')
        if _number(source_ttl_seconds) <= 0 or max_db_bytes < 65536:
            raise ValueError('invalid capacity')
        self.directory = Path(directory).absolute()
        self.root = Path(private_root).absolute()
        self._no_symlinks(self.directory)
        self._no_symlinks(self.root)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit, self.body_limit = max_records, max_body_bytes
        self.ttl, self.clock = source_ttl_seconds, clock
        self.session = uuid4().hex
        self.closed = False
        self.db = None
        lock = self.directory / '.lock'
        if lock.is_symlink():
            raise OutboxError('unsafe lock')
        self.fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size == 0: os.write(self.fd,b'0')
                os.lseek(self.fd,0,os.SEEK_SET)
                msvcrt.locking(self.fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            dbpath = self.directory/'capture.sqlite3'
            for name in ('capture.sqlite3','capture.sqlite3-journal','capture.sqlite3-wal','capture.sqlite3-shm'):
                self._no_symlinks(self.directory/name)
            if not dbpath.exists():
                fd=os.open(dbpath,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
            self.db=sqlite3.connect(dbpath)
            self.db.row_factory=sqlite3.Row
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            pages=max_db_bytes//self.db.execute('PRAGMA page_size').fetchone()[0]
            if self.db.execute('PRAGMA page_count').fetchone()[0]>pages:
                raise OutboxError('existing database exceeds capacity')
            self.db.execute(f'PRAGMA max_page_count={pages}')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS source_failures(
                  session TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS spool_dispositions(
                  spool TEXT NOT NULL, receipt_seq INTEGER NOT NULL, event TEXT NOT NULL REFERENCES events(id),
                  camera TEXT NOT NULL, receipt BLOB NOT NULL, PRIMARY KEY(spool,receipt_seq));
                CREATE TABLE IF NOT EXISTS lifecycle_heads(
                  event TEXT PRIMARY KEY REFERENCES events(id), api_seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pending_closes(
                  event TEXT PRIMARY KEY REFERENCES events(id), session TEXT NOT NULL,
                  transition BLOB NOT NULL, next_api_seq INTEGER NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  id TEXT PRIMARY KEY, session TEXT NOT NULL, snapshot TEXT NOT NULL,
                  state TEXT NOT NULL, reason TEXT, next_seq INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS requests(
                  event TEXT NOT NULL REFERENCES events(id), seq INTEGER NOT NULL,
                  kind TEXT NOT NULL, idem TEXT UNIQUE NOT NULL, body BLOB NOT NULL,
                  digest TEXT NOT NULL, ack TEXT, PRIMARY KEY(event,seq));
                CREATE TABLE IF NOT EXISTS captures(
                  spool TEXT PRIMARY KEY, event TEXT NOT NULL REFERENCES events(id),
                  camera TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orphans(
                  path TEXT PRIMARY KEY, event TEXT NOT NULL REFERENCES events(id),
                  camera TEXT NOT NULL, reason TEXT NOT NULL, cleanup INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS segments(
                  id TEXT PRIMARY KEY, event TEXT NOT NULL REFERENCES events(id),
                  camera TEXT NOT NULL, path TEXT UNIQUE NOT NULL, bytes INTEGER NOT NULL,
                  created REAL NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                  state TEXT NOT NULL, reason TEXT, cleanup INTEGER NOT NULL DEFAULT 0);
            ''')
            with self.transaction():
                self.db.execute("UPDATE events SET state='interrupted',reason='process_restart' WHERE state='active'")
                self.db.execute("UPDATE captures SET state='interrupted' WHERE state='active'")
                self.db.execute("UPDATE pending_closes SET state='interrupted' WHERE state='pending'")
            self.reconcile()
        except BaseException:
            if self.db: self.db.close()
            os.close(self.fd)
            raise

    @contextmanager
    def transaction(self):
        """Nested calls share the owner transaction, never commit it early."""
        if self.db.in_transaction:
            yield
        else:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                yield

    def apply_lifecycle_batch(self, operations):
        """Commit all transitions from one input frame, or none of them.

        No transport calls occur here. API update sequence is independent from
        the global command sequence, which may include manifests/reports.
        """
        with self.transaction():
            for op in operations:
                event, kind = op['event_id'], op['kind']
                if kind == 'start':
                    self.create_event(event, camera_snapshot=op['camera_snapshot'])
                    self.enqueue(event, seq=0, kind='start', idempotency_key=op['idempotency_key'], body=op['body'])
                    self._room()
                    self.db.execute('INSERT INTO lifecycle_heads VALUES(?,0)',(event,))
                    continue
                row=self._event(event)
                if (row['state']!='active' and not (row['state']=='incomplete' and (kind=='pending_close' or (kind=='progress' and op.get('final_progress') is True)))) or row['session']!=self.session:
                    raise OutboxError('inactive lifecycle session')
                head=self.db.execute('SELECT api_seq FROM lifecycle_heads WHERE event=?',(event,)).fetchone()
                if head is None or op['api_sequence']!=head['api_seq']+1:
                    raise OutboxError('lifecycle API sequence conflict')
                if self.db.execute('SELECT 1 FROM pending_closes WHERE event=?',(event,)).fetchone():
                    raise OutboxError('lifecycle close already pending')
                if kind == 'progress':
                    self.enqueue(event, seq=row['next_seq'], kind='progress',
                                 idempotency_key=op['idempotency_key'], body=op['body'],
                                 _lifecycle_final_progress=op.get('final_progress') is True)
                    self.db.execute('UPDATE lifecycle_heads SET api_seq=? WHERE event=?',(op['api_sequence'],event))
                elif kind == 'pending_close':
                    encoded=_json(op['transition']).encode()
                    if len(encoded)>self.body_limit: raise OutboxError('close intent too large')
                    self._room()
                    self.db.execute("INSERT INTO pending_closes VALUES(?,?,?,?,'pending')",
                                    (event,self.session,encoded,op['api_sequence']))
                else:
                    raise OutboxError('invalid lifecycle operation')

    def finalize_pending_close(self,event,*,complete,final_segment_count):
        """Atomically consume a local close intent after manifest ACKs.

        This persists a command, never sends it. Interrupted sessions require
        explicit remote recovery elsewhere. Finalized exact retries return bytes.
        """
        from inference_worker.continuous_payloads import update_payload
        if type(complete) is not bool or type(final_segment_count) is not int or not 0 <= final_segment_count <= 10000:
            raise ValueError('explicit complete/count required')
        self.reconcile()  # Commit audit failures independently of finalization rejection.
        with self.transaction():
            intent=self.db.execute('SELECT * FROM pending_closes WHERE event=?',(event,)).fetchone()
            if intent is None: raise OutboxError('unknown close intent')
            body=update_payload(json.loads(intent['transition']),session_id=intent['session'],
                                sequence=intent['next_api_seq'],complete=complete,
                                final_segment_count=final_segment_count)
            idem='final-close:'+hashlib.sha256(event.encode()).hexdigest()
            encoded=_json(body).encode()
            if intent['state']=='finalized':
                saved=self.db.execute('SELECT body FROM requests WHERE idem=?',(idem,)).fetchone()
                if saved is None or saved['body']!=encoded:
                    raise OutboxError('finalized close conflict')
                return bytes(saved['body'])
            row=self._event(event)
            if intent['state']!='pending' or intent['session']!=self.session or row['session']!=self.session:
                raise OutboxError('close requires remote session recovery')
            if row['state'] not in ('active','incomplete'):
                raise OutboxError('event cannot finalize locally')
            if complete and row['state']=='incomplete':
                raise OutboxError('incomplete event cannot claim complete')
            manifests=self.db.execute("SELECT body,ack FROM requests WHERE event=? AND kind='manifest' ORDER BY seq",(event,)).fetchall()
            if len(manifests)!=final_segment_count:
                raise OutboxError('manifest count mismatch')
            sequences=[]
            for manifest in manifests:
                payload=json.loads(manifest['body'])
                sequence=payload.get('sequence')
                if (manifest['ack'] is None or type(sequence) is not int or
                        payload.get('session_id')!=intent['session']):
                    raise OutboxError('manifest unacknowledged or wrong session')
                sequences.append(sequence)
            if sorted(sequences)!=list(range(final_segment_count)):
                raise OutboxError('manifest prefix incomplete or duplicated')
            wire_kind='close' if complete else 'incomplete'
            exact=self.enqueue(event,seq=row['next_seq'],kind=wire_kind,idempotency_key=idem,body=body)
            self.db.execute("UPDATE pending_closes SET state='finalized' WHERE event=?",(event,))
            self.db.execute('UPDATE lifecycle_heads SET api_seq=? WHERE event=?',(intent['next_api_seq'],event))
            return exact

    def pending_close_intents(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM pending_closes ORDER BY event')]

    @staticmethod
    def _no_symlinks(path):
        for part in (path, *path.parents):
            if part.is_symlink(): raise OutboxError('symlink path forbidden')

    def _room(self):
        count=sum(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                  for table in ('events','requests','segments','captures','orphans','lifecycle_heads','pending_closes','spool_dispositions','source_failures'))
        if count>=self.limit: raise OutboxError('journal record capacity')

    def _event(self,event):
        row=self.db.execute('SELECT * FROM events WHERE id=?',(event,)).fetchone()
        if row is None: raise OutboxError('unknown event')
        return row

    @staticmethod
    def _camera_ids(snapshot):
        return [item['camera_id'] if isinstance(item,dict) else item for item in snapshot]

    def create_event(self,event,*,camera_snapshot):
        _key(event)
        camera_ids=self._camera_ids(camera_snapshot)
        if not camera_ids or len(set(camera_ids))!=len(camera_ids):
            raise ValueError('camera snapshot')
        for camera in camera_ids: _key(camera)
        snapshot=_json(list(camera_snapshot))
        if len(snapshot.encode())>self.body_limit: raise OutboxError('snapshot too large')
        with self.transaction():
            old=self.db.execute('SELECT * FROM events WHERE id=?',(event,)).fetchone()
            if old:
                if old['session']==self.session and old['snapshot']==snapshot and old['state']=='active': return
                raise OutboxError('event identity cannot be reused')
            self._room()
            self.db.execute('INSERT INTO events(id,session,snapshot,state) VALUES(?,?,?,?)',
                            (event,self.session,snapshot,'active'))

    def _incomplete_capture_open(self,row,*,camera=None):
        if row['state']!='incomplete' or row['session']!=self.session:
            return False
        intent=self.db.execute("SELECT 1 FROM pending_closes WHERE event=? AND session=? AND state='pending'",
                               (row['id'],self.session)).fetchone()
        if not intent:return False
        sql="SELECT 1 FROM captures WHERE event=? AND state='active'"
        params=[row['id']]
        if camera is not None:
            sql+=' AND camera=?';params.append(camera)
        return self.db.execute(sql,params).fetchone() is not None

    def enqueue(self,event,*,seq,kind,idempotency_key,body,_lifecycle_final_progress=False):
        """Canonical bytes are persisted once; retries never reconstruct payloads."""
        _key(idempotency_key)
        if type(seq) is not int or seq<0 or kind not in ('start','progress','close','incomplete','manifest','capture_report'):
            raise ValueError('request sequence/kind')
        payload=_json(body).encode()
        if len(payload)>self.body_limit: raise OutboxError('body too large')
        digest=hashlib.sha256(payload).hexdigest()
        with self.transaction():
            row=self._event(event)
            old=self.db.execute('SELECT * FROM requests WHERE event=? AND seq=?',(event,seq)).fetchone()
            if old:
                if (old['kind'],old['idem'],old['body'])!=(kind,idempotency_key,payload):
                    raise OutboxError('immutable request conflict')
                return bytes(old['body'])
            if (row['state']!='active' and kind!='incomplete'
                    and not (row['state']=='closed' and kind=='capture_report')
                    and not (row['state']=='incomplete' and kind=='progress' and _lifecycle_final_progress is True)
                    and not (kind=='manifest' and isinstance(body,dict) and body.get('session_id')==self.session
                             and self._incomplete_capture_open(row,camera=body.get('camera_id')))
                    and not (row['state'] in ('incomplete','interrupted') and kind=='capture_report'
                             and isinstance(body,dict) and body.get('state')=='incomplete')):
                raise OutboxError('inactive event cannot progress')
            if seq!=row['next_seq'] or (seq==0 and kind!='start' and not (kind=='incomplete' and row['state']!='active')) or (seq>0 and kind=='start'):
                raise OutboxError('noncontiguous sequence')
            self._room()
            self.db.execute('INSERT INTO requests(event,seq,kind,idem,body,digest) VALUES(?,?,?,?,?,?)',
                            (event,seq,kind,idempotency_key,payload,digest))
            self.db.execute('UPDATE events SET next_seq=next_seq+1 WHERE id=?',(event,))
            if kind=='close': self.db.execute("UPDATE events SET state='closed' WHERE id=?",(event,))
            if kind=='incomplete': self.db.execute("UPDATE events SET state='incomplete',reason=coalesce(reason,'capture_incomplete') WHERE id=?",(event,))
        return payload

    def pending(self):
        self.reconcile()
        # One unacknowledged head per event prevents delivery out of order.
        rows=self.db.execute('''SELECT r.* FROM requests r WHERE ack IS NULL
          AND NOT (r.kind='close' AND EXISTS (SELECT 1 FROM events e WHERE e.id=r.event AND e.state!='closed'))
          AND NOT EXISTS
          (SELECT 1 FROM requests p WHERE p.event=r.event AND p.seq<r.seq AND p.ack IS NULL)
          ORDER BY event,seq''').fetchall()
        return [dict(row) for row in rows]

    def blocked_terminal_requests(self):
        """Requires remote-state reconciliation, never rewrite or blindly retry."""
        return [dict(row) for row in self.db.execute("""SELECT r.*,e.reason AS block_reason
          FROM requests r JOIN events e ON e.id=r.event
          WHERE r.ack IS NULL AND r.kind='close' AND e.state!='closed'""")]

    def acknowledge(self,event,seq,*,receipt):
        encoded=_json(receipt)
        if len(encoded.encode())>self.body_limit: raise OutboxError('receipt too large')
        with self.transaction():
            row=self.db.execute('SELECT ack FROM requests WHERE event=? AND seq=?',(event,seq)).fetchone()
            if row is None: raise OutboxError('unknown request')
            if row['ack'] is not None and row['ack']!=encoded: raise OutboxError('receipt conflict')
            if self.db.execute('SELECT 1 FROM requests WHERE event=? AND seq<? AND ack IS NULL',(event,seq)).fetchone():
                raise OutboxError('earlier request unacknowledged')
            self.db.execute('UPDATE requests SET ack=? WHERE event=? AND seq=?',(encoded,event,seq))

    def _path(self,relative):
        # Only finalized spool UUID files in one UUID spool directory are sources.
        if not isinstance(relative,str) or not re.fullmatch(r'[0-9a-f]{32}/[0-9a-f]{32}\.mp4',relative):
            raise OutboxError('invalid private source path')
        path=self.root/relative
        self._no_symlinks(path)
        return path

    def begin_capture(self,event,*,camera,spool_directory):
        """Persist ownership BEFORE constructing the writer or creating raw files."""
        if not re.fullmatch(r'[0-9a-f]{32}',spool_directory):
            raise OutboxError('invalid spool directory')
        self._no_symlinks(self.root/spool_directory)
        with self.transaction():
            row=self._event(event)
            if row['state']!='active' or camera not in self._camera_ids(json.loads(row['snapshot'])):
                raise OutboxError('invalid capture owner')
            old=self.db.execute('SELECT * FROM captures WHERE spool=?',(spool_directory,)).fetchone()
            if old:
                if (old['event'],old['camera'],old['state'])!=(event,camera,'active'):
                    raise OutboxError('capture binding conflict')
                return
            self._room()
            self.db.execute("INSERT INTO captures VALUES(?,?,?,'active')",(spool_directory,event,camera))

    def _recover_orphans(self):
        # Only interrupted producers are scanned. A live encoder is never mistaken
        # for an orphan, and nothing is deleted by this ledger.
        for cap in self.db.execute("SELECT * FROM captures WHERE state IN ('interrupted','reconciliation_required')").fetchall():
            directory=self.root/cap['spool']
            self._no_symlinks(directory)
            if not directory.exists(): continue
            for path in directory.iterdir():
                if not re.fullmatch(r'[0-9a-f]{32}\.(mp4|part)',path.name):
                    if path.name not in ('.lock','journal.json','journal.tmp'):
                        raise OutboxError('unknown file in owned spool directory')
                    continue
                relative=cap['spool']+'/'+path.name
                if self.db.execute('SELECT 1 FROM segments WHERE path=?',(relative,)).fetchone(): continue
                if self.db.execute('SELECT 1 FROM orphans WHERE path=?',(relative,)).fetchone(): continue
                try:
                    self._room()
                except OutboxError:
                    self.db.execute("UPDATE captures SET state='reconciliation_required' WHERE spool=?",(cap['spool'],))
                    self.db.execute("UPDATE events SET state='incomplete',reason='orphan_inventory_capacity' WHERE id=?",(cap['event'],))
                    continue
                self.db.execute("INSERT INTO orphans VALUES(?,?,?,'restart_unfinished_or_unmapped',1)",
                                (relative,cap['event'],cap['camera']))
                self.db.execute("UPDATE events SET state='incomplete',reason='restart_unfinished_or_unmapped' WHERE id=?",(cap['event'],))

    def recovery_obligations(self):
        """Aggregate durable fallback when individual orphan inventory is full."""
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM captures WHERE state='reconciliation_required'")]

    def orphan_cleanup_obligations(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM orphans WHERE cleanup=1')]

    def acknowledge_orphan_cleanup(self,relative_path):
        with self.transaction():
            row=self.db.execute('SELECT * FROM orphans WHERE path=?',(relative_path,)).fetchone()
            if not row: raise OutboxError('unknown orphan obligation')
            path=self.root/row['path']
            self._no_symlinks(path)
            if path.exists(): raise OutboxError('orphan source still present')
            self.db.execute('UPDATE orphans SET cleanup=0 WHERE path=?',(relative_path,))

    def bind_segment(self,event,*,camera,segment,relative_path):
        path=self._path(relative_path)
        if path.stem!=segment.segment_id: raise OutboxError('source identity mismatch')
        _number(segment.created_at); _number(segment.capture_start); _number(segment.capture_end)
        if segment.capture_end<=segment.capture_start or type(segment.byte_count) is not int or segment.byte_count<=0:
            raise ValueError('segment metadata')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size!=segment.byte_count:
            raise OutboxError('source size/type mismatch')
        with self.transaction():
            eventrow=self._event(event)
            owner=self.db.execute('SELECT * FROM captures WHERE spool=?',(relative_path.split('/')[0],)).fetchone()
            if not owner or (owner['event'],owner['camera'])!=(event,camera):
                raise OutboxError('capture ownership must precede source creation')
            if camera not in self._camera_ids(json.loads(eventrow['snapshot'])): raise OutboxError('camera not in snapshot')
            values=(segment.segment_id,event,camera,relative_path,segment.byte_count,
                    segment.created_at,segment.capture_start,segment.capture_end)
            old=self.db.execute('SELECT id,event,camera,path,bytes,created,start,end FROM segments WHERE id=?',(segment.segment_id,)).fetchone()
            if old:
                if tuple(old)!=values: raise OutboxError('segment binding conflict')
                return
            if eventrow['state']!='active' and not (owner['state']=='active' and self._incomplete_capture_open(eventrow,camera=camera)):
                raise OutboxError('cannot add source to inactive event')
            self._room()
            self.db.execute("INSERT INTO segments(id,event,camera,path,bytes,created,start,end,state) VALUES(?,?,?,?,?,?,?,?,'present')",values)
        self.reconcile()

    def reconcile(self):
        """Persist failure + cleanup intent. Never unlink: spool owns cleanup."""
        now=_number(self.clock())
        with self.transaction():
            self._recover_orphans()
            for row in self.db.execute("SELECT * FROM segments WHERE state='present'").fetchall():
                reason=None
                try:
                    path=self._path(row['path'])
                    info=path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size!=row['bytes']:
                        reason='source_size_or_type_mismatch'
                except FileNotFoundError: reason='source_missing'
                except (OSError,OutboxError): reason='source_unsafe_or_inaccessible'
                if reason is None and (now<row['created'] or now-row['created']>=self.ttl):
                    reason='source_expired_or_clock_rollback'
                if reason:
                    self.db.execute("UPDATE segments SET state='unavailable',reason=?,cleanup=1 WHERE id=?",(reason,row['id']))
                    self.db.execute("UPDATE events SET state='incomplete',reason=? WHERE id=?",(reason,row['event']))

    def request_cleanup(self,segment_id,*,reason):
        _key(reason)
        with self.transaction():
            row=self.db.execute('SELECT event,state FROM segments WHERE id=?',(segment_id,)).fetchone()
            if not row: raise OutboxError('unknown segment')
            if reason=='verified_upload_receipt' and row['state']=='unavailable':
                raise OutboxError('cannot restore unavailable evidence')
            self.db.execute("UPDATE segments SET cleanup=1 WHERE id=?",(segment_id,))
            # This denotes a verified private upload receipt, not public visibility.
            if reason!='verified_upload_receipt':
                self.db.execute("UPDATE segments SET state='unavailable',reason=? WHERE id=?",(reason,segment_id))
                self.db.execute("UPDATE events SET state='incomplete',reason=? WHERE id=?",(reason,row['event']))
            else:
                self.db.execute("UPDATE segments SET state='uploaded_private',reason=? WHERE id=?",(reason,segment_id))

    def record_unbound_discard(self,spool_directory,receipt):
        """Join a removed partial's disposition to precommitted camera ownership."""
        if not isinstance(spool_directory,str) or not re.fullmatch(r'[0-9a-f]{32}',spool_directory):
            raise OutboxError('invalid spool owner')
        if (not isinstance(receipt,dict) or set(receipt)!={'receipt_sequence','segment_id','segment_sequence','reason','discarded_at','byte_count'}
                or type(receipt['receipt_sequence']) is not int or receipt['receipt_sequence']<0
                or not isinstance(receipt['segment_id'],str) or not re.fullmatch(r'[0-9a-f]{32}',receipt['segment_id'])):
            raise OutboxError('invalid discard receipt')
        _key(receipt['reason']);_number(receipt['discarded_at'])
        if receipt['segment_sequence'] is not None and (type(receipt['segment_sequence']) is not int or receipt['segment_sequence']<0):
            raise OutboxError('invalid segment sequence')
        if receipt['byte_count'] is not None and (type(receipt['byte_count']) is not int or receipt['byte_count']<0):
            raise OutboxError('invalid discarded byte count')
        encoded=_json(receipt).encode()
        if len(encoded)>self.body_limit:raise OutboxError('discard receipt too large')
        directory=self.root/spool_directory
        self._no_symlinks(directory)
        for suffix in ('.part','.mp4'):
            path=directory/(receipt['segment_id']+suffix)
            self._no_symlinks(path)
            if path.exists():raise OutboxError('discarded raw source still exists')
        with self.transaction():
            owner=self.db.execute('SELECT * FROM captures WHERE spool=?',(spool_directory,)).fetchone()
            if owner is None or owner['state']=='reconciliation_required':raise OutboxError('unknown or unresolved spool owner')
            if self.db.execute('SELECT 1 FROM segments WHERE id=?',(receipt['segment_id'],)).fetchone():
                return False  # Finalized segment cleanup has a separate audit path.
            old=self.db.execute('SELECT receipt FROM spool_dispositions WHERE spool=? AND receipt_seq=?',
                                (spool_directory,receipt['receipt_sequence'])).fetchone()
            if old:
                if old['receipt']!=encoded:raise OutboxError('discard receipt conflict')
                return True
            self._room()
            self.db.execute('INSERT INTO spool_dispositions VALUES(?,?,?,?,?)',
                            (spool_directory,receipt['receipt_sequence'],owner['event'],owner['camera'],encoded))
            self.db.execute("UPDATE events SET state='incomplete',reason=? WHERE id=?",
                            ('unbound_source_discard:'+receipt['reason'],owner['event']))
            return True

    def spool_dispositions(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM spool_dispositions ORDER BY spool,receipt_seq')]

    def segment_status(self,segment_id):
        row=self.db.execute('SELECT * FROM segments WHERE id=?',(segment_id,)).fetchone()
        if row is None: raise OutboxError('unknown segment')
        return dict(row)

    def cleanup_obligations(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM segments WHERE cleanup=1')]

    def acknowledge_cleanup(self,segment_id):
        with self.transaction():
            row=self.db.execute('SELECT * FROM segments WHERE id=?',(segment_id,)).fetchone()
            if not row or not row['cleanup']: raise OutboxError('unknown cleanup obligation')
            path=self._path(row['path'])
            if path.exists(): raise OutboxError('source still present')
            self.db.execute('UPDATE segments SET cleanup=0 WHERE id=?',(segment_id,))

    def record_source_failure(self,metadata):
        allowed={'reason','camera','stream','expected_sequence','availability_reason'}
        if not isinstance(metadata,dict) or set(metadata)!=allowed:raise ValueError('source failure metadata')
        for name in ('reason','camera','stream'):_key(metadata[name])
        if type(metadata['expected_sequence']) is not int or metadata['expected_sequence']<0:
            raise ValueError('source failure sequence')
        if metadata['availability_reason'] not in (None,'raw_expired','raw_missing','raw_unavailable','receipt_missing'):
            raise ValueError('source availability reason')
        body=_json(metadata)
        with self.transaction():
            if self.db.execute('SELECT 1 FROM source_failures WHERE session=?',(self.session,)).fetchone():return
            self._room()
            self.db.execute('INSERT INTO source_failures VALUES(?,?)',(self.session,body))

    def mark_incomplete(self,event,*,reason):
        """Persist a capture failure even when no segment has been produced."""
        _key(reason)
        with self.transaction():
            self._event(event)
            self.db.execute("UPDATE events SET state='incomplete',reason=? WHERE id=?",(reason,event))

    def event_status(self,event):
        return dict(self._event(event))

    def close(self):
        if not self.closed:
            self.db.close(); os.close(self.fd); self.closed=True
