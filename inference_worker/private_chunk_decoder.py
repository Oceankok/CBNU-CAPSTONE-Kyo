"""Opt-in private MP4 decoder; no legacy temp-directory or public output.

The caller owns an open CaptureOutbox lifetime lock and serializes calls.
All numeric limits are explicit. Run maintain even while a generator is paused.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from uuid import uuid4

import cv2
from inference_worker.capture_outbox import CaptureOutbox


class PrivateDecodeError(RuntimeError):
    pass


class PrivateChunkDecoder:
    def __init__(self, owner, *, max_bytes, max_frames, max_frame_pixels,
                 min_fps, max_fps, duration_tolerance_ms, ttl_seconds,
                 max_audit_records, max_metadata_bytes):
        if not isinstance(owner,CaptureOutbox) or owner.closed:
            raise ValueError('open outbox lock owner required')
        for value in (max_bytes,max_frames,max_frame_pixels,max_audit_records,max_metadata_bytes):
            if type(value) is not int or value<=0:raise ValueError('positive capacity required')
        for value in (min_fps,max_fps,duration_tolerance_ms,ttl_seconds):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('positive finite decoder policy required')
        if min_fps>max_fps or max_metadata_bytes<65536:raise ValueError('invalid decoder limits')
        existing=getattr(owner,'_private_decoder',None)
        if existing is not None and not existing.closed:raise PrivateDecodeError('decoder owner already active')
        self.owner=owner;self.root=owner.directory/'decode'
        owner._no_symlinks(self.root)
        self.root.mkdir(mode=0o700,exist_ok=True)
        self.max_bytes=max_bytes;self.max_frames=max_frames;self.max_pixels=max_frame_pixels
        self.min_fps=min_fps;self.max_fps=max_fps;self.tolerance=duration_tolerance_ms
        self.ttl=ttl_seconds;self.max_records=max_audit_records
        self.active=None;self.capture=None;self.closed=False
        self.db=None
        # Refuse any unknown directory contents before opening SQLite.
        for path in self.root.iterdir():
            if path.is_symlink() or (path.name not in ('ownership.sqlite3','ownership.sqlite3-journal')
                    and not re.fullmatch(r'[0-9a-f]{32}\.mp4',path.name)):
                raise PrivateDecodeError('manual recovery: unknown private decoder file')
        dbpath=self.root/'ownership.sqlite3'
        if not dbpath.exists():
            fd=os.open(dbpath,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        try:
            self.db=sqlite3.connect(dbpath)
            self.db.row_factory=sqlite3.Row
            self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
            pages=max_metadata_bytes//self.db.execute('PRAGMA page_size').fetchone()[0]
            if self.db.execute('PRAGMA page_count').fetchone()[0]>pages:raise PrivateDecodeError('metadata capacity')
            self.db.execute(f'PRAGMA max_page_count={pages}')
            self.db.execute('''CREATE TABLE IF NOT EXISTS files(
                id TEXT PRIMARY KEY, expected_bytes INTEGER NOT NULL, digest TEXT NOT NULL,
                created REAL NOT NULL, device INTEGER, inode INTEGER,
                state TEXT NOT NULL, outcome TEXT)''')
            self.db.commit()
            known={row['id'] for row in self.db.execute('SELECT id FROM files')}
            for path in self.root.glob('*.mp4'):
                if path.stem not in known:raise PrivateDecodeError('manual recovery: unowned raw file')
            # A prior process cannot be decoding under the new owner's lock.
            for row in self.db.execute("SELECT * FROM files").fetchall():
                self._cleanup(row['id'],'restart_interrupted')
            owner._private_decoder=self
        except BaseException:
            if self.db:self.db.close()
            raise

    def _check(self):
        if self.closed or self.owner.closed:raise PrivateDecodeError('decoder owner closed')

    def _now(self):
        value=self.owner.clock()
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise PrivateDecodeError('invalid retention clock')
        return value

    def _row(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):
            raise PrivateDecodeError('manual recovery: invalid manifest identifier')
        row=self.db.execute('SELECT * FROM files WHERE id=?',(identifier,)).fetchone()
        if row is None:raise PrivateDecodeError('unknown ownership record')
        return row

    def _owned_path(self,row):
        path=self.root/(row['id']+'.mp4')
        self.owner._no_symlinks(path)
        try:info=path.stat()
        except FileNotFoundError:return path
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or row['device'] is None
                or row['inode'] is None or info.st_dev!=row['device'] or info.st_ino!=row['inode']
                or info.st_size>row['expected_bytes']):
            raise PrivateDecodeError('manual recovery: raw identity mismatch')
        if row['state']=='ready':
            if info.st_size!=row['expected_bytes']:
                raise PrivateDecodeError('manual recovery: completed raw size mismatch')
            with path.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest!=row['digest']:raise PrivateDecodeError('manual recovery: raw digest mismatch')
        return path

    def _cleanup(self,identifier,outcome):
        row=self._row(identifier)
        path=self._owned_path(row)
        if row['state']=='done':
            if path.exists():raise PrivateDecodeError('manual recovery: file reappeared')
            return
        # Persist audit intent first; failed unlink remains retry-only.
        with self.db:
            self.db.execute("UPDATE files SET state='cleanup_pending',outcome=coalesce(outcome,?) WHERE id=?",(outcome,identifier))
        path.unlink(missing_ok=True)
        with self.db:self.db.execute("UPDATE files SET state='done' WHERE id=?",(identifier,))

    def maintain(self):
        self._check();now=self._now()
        for row in self.db.execute("SELECT * FROM files WHERE state!='done'").fetchall():
            expired=now<row['created'] or now-row['created']>=self.ttl
            if expired or row['state']=='cleanup_pending':
                if row['id']==self.active and self.capture is not None:
                    self.capture.release();self.capture=None
                self._cleanup(row['id'],'privacy_ttl_expired' if expired else 'cleanup_retry')

    def audit(self):
        self._check()
        return [dict(row) for row in self.db.execute('SELECT * FROM files ORDER BY created,id')]

    def acknowledge_audit(self,identifiers):
        """After caller durably reports outcomes, release bounded audit slots."""
        self._check()
        with self.db:
            for identifier in identifiers:
                row=self._row(identifier)
                if row['state']!='done':raise PrivateDecodeError('cleanup not complete')
                if self._owned_path(row).exists():raise PrivateDecodeError('raw file remains')
                self.db.execute('DELETE FROM files WHERE id=?',(identifier,))

    def __call__(self,data,*,with_timing=False,duration_ms=None):
        return self.decode(data,with_timing=with_timing,duration_ms=duration_ms)

    def decode(self,data,*,with_timing=False,duration_ms=None):
        self._check()
        if self.active is not None:raise PrivateDecodeError('one active decoder only')
        if not isinstance(data,bytes) or not data or len(data)>self.max_bytes:
            raise ValueError('invalid or oversized chunk')
        if duration_ms is not None and (isinstance(duration_ms,bool) or not isinstance(duration_ms,(int,float))
                or not math.isfinite(duration_ms) or duration_ms<=0):raise ValueError('duration_ms')
        self.maintain()
        if self.db.execute("SELECT 1 FROM files WHERE state!='done'").fetchone():
            raise PrivateDecodeError('unresolved source cleanup blocks admission')
        if self.db.execute('SELECT count(*) FROM files').fetchone()[0]>=self.max_records:
            raise PrivateDecodeError('audit admission capacity')
        identifier=uuid4().hex;path=self.root/(identifier+'.mp4');created=self._now()
        with self.db:
            self.db.execute("INSERT INTO files(id,expected_bytes,digest,created,state) VALUES(?,?,?,?,'reserved')",
                (identifier,len(data),hashlib.sha256(data).hexdigest(),created))
        self.active=identifier;outcome='decode_failed'
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
            try:
                info=os.fstat(fd)
                with self.db:self.db.execute("UPDATE files SET device=?,inode=?,state='writing' WHERE id=?",(info.st_dev,info.st_ino,identifier))
                view=memoryview(data)
                while view:
                    now=self._now()
                    if now<created or now-created>=self.ttl:
                        outcome='privacy_ttl_expired';raise PrivateDecodeError('source retention expired')
                    written=os.write(fd,view[:65536])
                    if written<=0:raise PrivateDecodeError('raw write failed')
                    view=view[written:]
                os.fsync(fd)
            finally:os.close(fd)
            with self.db:self.db.execute("UPDATE files SET state='ready' WHERE id=?",(identifier,))
            self.capture=cv2.VideoCapture(str(path))
            if not self.capture.isOpened():raise PrivateDecodeError('invalid MP4')
            width=self.capture.get(cv2.CAP_PROP_FRAME_WIDTH);height=self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if (not math.isfinite(width) or not math.isfinite(height) or width<=0 or height<=0
                    or width!=int(width) or height!=int(height) or width*height>self.max_pixels):
                raise PrivateDecodeError('frame dimension metadata limits')
            fps=self.capture.get(cv2.CAP_PROP_FPS);expected=self.capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if (not math.isfinite(fps) or not self.min_fps<=fps<=self.max_fps or not math.isfinite(expected)
                    or expected!=int(expected) or not 1<=expected<=self.max_frames):
                raise PrivateDecodeError('frame metadata limits')
            if duration_ms is not None:
                if abs(expected/fps*1000-duration_ms)>self.tolerance:raise PrivateDecodeError('chunk duration mismatch')
                fps=expected/(duration_ms/1000)
            index=0
            while True:
                self._check();now=self._now()
                if now<created or now-created>=self.ttl or self._row(identifier)['state']!='ready':
                    outcome='privacy_ttl_expired';raise PrivateDecodeError('source retention expired')
                ok,frame=self.capture.read()
                if not ok:break
                if index>=self.max_frames or frame.shape[0]*frame.shape[1]>self.max_pixels:
                    raise PrivateDecodeError('decoded frame limits')
                yield (index,fps,frame) if with_timing else frame
                index+=1
            if index!=int(expected):raise PrivateDecodeError('incomplete MP4 decode')
            outcome='decoded'
        finally:
            if self.capture is not None:self.capture.release();self.capture=None
            self.active=None
            if not self.closed:self._cleanup(identifier,outcome)

    def close(self):
        if self.closed:return
        if self.capture is not None:self.capture.release();self.capture=None
        try:
            if self.active:self._cleanup(self.active,'decoder_shutdown')
        finally:
            self.active=None;self.db.close();self.closed=True
