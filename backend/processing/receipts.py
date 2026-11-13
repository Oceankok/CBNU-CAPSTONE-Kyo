"""Owner-only historical byte-delivery receipts, never a raw-media download API."""
import hashlib
import time
from fastapi import HTTPException
from backend.processing import lifecycle as lc


def measure(path):
    digest=hashlib.sha256(); size=0
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk);size+=len(chunk)
    return digest.hexdigest(),size


def record(conn,event_id,request_id,media_id,measurement):
    # Called only in the same transaction that acknowledges the accepted request.
    if not conn.execute('SELECT 1 FROM processing_event_lifecycle WHERE event_id=?',(event_id,)).fetchone():return
    lc.require_segment_upload(conn,event_id,request_id)
    row=conn.execute('SELECT camera_id,role FROM event_clip_request WHERE event_id=? AND request_id=?',(event_id,request_id)).fetchone()
    digest,size=measurement
    conn.execute('''INSERT INTO processing_clip_receipt(request_id,event_id,camera_id,role,media_id,source_sha256,source_bytes,accepted_at)
        VALUES(?,?,?,?,?,?,?,?)''',(request_id,event_id,row['camera_id'],row['role'],media_id,digest,size,time.time()))
    from backend.auto_decision.writer import on_event_ready
    on_event_ready(conn,event_id)


def read(event_id,request_id,node_id,session_id):
    lc.enabled()
    with lc.get_connection() as conn:
        lc._owner(conn,event_id,node_id,session_id)
        receipt=conn.execute('SELECT * FROM processing_clip_receipt WHERE event_id=? AND request_id=?',(event_id,request_id)).fetchone()
        if not receipt:raise HTTPException(404,'continuous_delivery_receipt_not_found')
        media=conn.execute('SELECT status,redaction_status,source_path,source_expires_at FROM event_media WHERE media_id=?',(receipt['media_id'],)).fetchone()
        retention=conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?',(event_id,)).fetchone()
        state='available'
        if retention and retention['decision']=='delete': state='deleted'
        elif retention and retention['decision']=='retain' and retention['retain_until'] is not None and retention['retain_until']<=time.time():state='retention_expired'
        elif not media or not media['source_path']:state='unavailable'
        elif media['source_expires_at'] is None or media['source_expires_at']<=time.time():state='source_expired'
        else:
            from backend.media.paths import private_source_path
            try:
                if not private_source_path(media['source_path']).is_file():state='unavailable'
            except (ValueError,OSError):state='unavailable'
        return {**dict(receipt),'accepted':True,'source_state':state,
                'media_status':media['status'] if media else None,
                'redaction_status':media['redaction_status'] if media else None}
