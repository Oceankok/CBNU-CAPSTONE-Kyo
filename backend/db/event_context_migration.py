"""Freeze available context and repair live review contributions at schema 21."""
from collections import Counter
from backend.event_time import quarter_for


def capture_missing_context(conn):
    # Old occurrence context cannot be recovered; label this as upgrade-time data.
    conn.execute("""UPDATE candidate_event SET
        zone_name_snapshot=COALESCE((SELECT zone_name FROM camera_info WHERE camera_id=candidate_event.camera_id),''),
        process_type_snapshot=COALESCE((SELECT process_type FROM camera_info WHERE camera_id=candidate_event.camera_id),''),
        context_source='upgrade_current' WHERE context_source IS NULL""")


def migrate_event_context(conn, *, postgres):
    if postgres:
        for column in ('zone_name_snapshot', 'process_type_snapshot', 'context_source'):
            conn.execute(f'ALTER TABLE candidate_event ADD COLUMN IF NOT EXISTS {column} TEXT')
    else:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(candidate_event)')}
        for column in ('zone_name_snapshot', 'process_type_snapshot', 'context_source'):
            if column not in columns:
                conn.execute(f'ALTER TABLE candidate_event ADD COLUMN {column} TEXT')
    capture_missing_context(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS legacy_false_positive_summary (
        quarter TEXT PRIMARY KEY, false_positive_count INTEGER NOT NULL CHECK(false_positive_count>=0))''')
    old = Counter()
    for row in conn.execute('SELECT quarter,SUM(false_positive_count) FROM false_positive_aggregate GROUP BY quarter').fetchall():
        old[row[0]] = row[1]
    # Previous code added one aggregate contribution for every false-positive review,
    # including re-reviews. Subtract all identifiable contributions; keep anonymous
    # contributions of physically removed legacy events separately.
    contributions = Counter()
    for row in conn.execute('''SELECT ce.timestamp_start FROM event_review er
        JOIN candidate_event ce ON ce.event_id=er.event_id WHERE er.review_result='false_positive'
        UNION ALL SELECT ce.timestamp_start FROM event_review_history er
        JOIN candidate_event ce ON ce.event_id=er.event_id WHERE er.review_result='false_positive' ''').fetchall():
        contributions[quarter_for(row[0])] += 1
    for quarter, count in old.items():
        conn.execute('INSERT INTO legacy_false_positive_summary(quarter,false_positive_count) VALUES(?,?)',
                     (quarter, max(0, count-contributions[quarter])))
    conn.execute('DELETE FROM false_positive_aggregate')
    counts = Counter()
    for row in conn.execute("SELECT timestamp_start,zone_name_snapshot,ppe_type FROM candidate_event WHERE event_status='false_positive'").fetchall():
        counts[(quarter_for(row[0]), row[1] or '', row[2])] += 1
    for (quarter, zone, ppe), count in counts.items():
        # New IDs avoid collisions between zones whose names normalize identically.
        from uuid import uuid4
        conn.execute('''INSERT INTO false_positive_aggregate
            (aggregate_id,quarter,zone_name,ppe_type,false_positive_count,updated_at) VALUES(?,?,?,?,?,?)''',
            (str(uuid4()),quarter,zone,ppe,count,'schema-21-repair'))
