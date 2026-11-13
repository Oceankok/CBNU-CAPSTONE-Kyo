"""Enforce an existing retain deadline without waiting for housekeeping."""
import time


def expired(conn,event_id):
    row=conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?',(event_id,)).fetchone()
    return bool(row and row['decision']=='retain' and row['retain_until'] is not None and row['retain_until']<=time.time())


def require_unexpired(conn,event_id):
    if expired(conn,event_id):raise ValueError('event_media_expired')
