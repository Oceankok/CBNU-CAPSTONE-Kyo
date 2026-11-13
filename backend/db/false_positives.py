"""False-positive cache derived from retained events, never review attempt counts."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json

from backend.event_time import quarter_for, local_time_sql


def group_for_event(conn, event_id):
    row = conn.execute('''SELECT e.timestamp_start,e.ppe_type,z.zone_name FROM candidate_event e
        LEFT JOIN event_zone_snapshot z ON z.event_id=e.event_id WHERE e.event_id=?''', (event_id,)).fetchone()
    if row is None:
        return None
    return quarter_for(row['timestamp_start']), row['zone_name'] or '', row['ppe_type']


def _insert_group(conn, group, count):
    quarter, zone, ppe = group
    digest = hashlib.sha256(json.dumps(group, ensure_ascii=False).encode()).hexdigest()
    conn.execute('''INSERT INTO false_positive_aggregate
        (aggregate_id,quarter,zone_name,ppe_type,false_positive_count,updated_at) VALUES(?,?,?,?,?,?)''',
        ('FP_'+digest, quarter, zone, ppe, count, datetime.now(timezone.utc).isoformat(timespec='seconds')))


def refresh_group(conn, group):
    if group is None:
        return
    quarter, zone, ppe = group
    year, number = int(quarter[:4]), int(quarter[-1])
    month = (number-1)*3+1
    start = f'{year:04d}-{month:02d}-01'
    end = f'{year+1:04d}-01-01' if number == 4 else f'{year:04d}-{month+3:02d}-01'
    stamp = local_time_sql('e.timestamp_start')
    count = conn.execute(f'''SELECT COUNT(*) FROM candidate_event e
        LEFT JOIN event_zone_snapshot z ON z.event_id=e.event_id
        WHERE e.event_status='false_positive' AND e.ppe_type=? AND COALESCE(z.zone_name,'')=?
        AND {stamp}>=? AND {stamp}<?''', (ppe, zone, start, end)).fetchone()[0]
    conn.execute('DELETE FROM false_positive_aggregate WHERE quarter=? AND zone_name=? AND ppe_type=?', group)
    if count:
        _insert_group(conn, group, count)


def refresh_event_group(conn, event_id):
    refresh_group(conn, group_for_event(conn, event_id))


def rebuild_false_positive_aggregates(conn):
    """Owner-approved reset from remaining events; deleted legacy events excluded."""
    groups = Counter()
    for row in conn.execute('''SELECT e.timestamp_start,e.ppe_type,z.zone_name FROM candidate_event e
        LEFT JOIN event_zone_snapshot z ON z.event_id=e.event_id
        WHERE e.event_status='false_positive' '''):
        groups[(quarter_for(row['timestamp_start']), row['zone_name'] or '', row['ppe_type'])] += 1
    conn.execute('DELETE FROM false_positive_aggregate')
    for group, count in groups.items():
        _insert_group(conn, group, count)
    # Preserve the summary parent and education content while correcting the
    # old false-positive cache. Other summary fields keep their prior values.
    conn.execute('''UPDATE quarterly_summary SET false_positive_count=COALESCE(
        (SELECT SUM(a.false_positive_count) FROM false_positive_aggregate a
         WHERE a.quarter=quarterly_summary.quarter),0)''')
