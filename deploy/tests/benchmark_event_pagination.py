"""Compare real event HTTP responses using synthetic rows in disposable PG schemas.

Run this script in separate processes with --checkout pointing to the historical
and proposed checkouts. No live server, port, private files, camera or GPU is used.
PPE_TEST_POSTGRES_URL must identify a dedicated kyo_test* database; isolated_api
creates and drops its own random schema for every size.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import UUID


def seed(count, connect):
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    events, media = [], []
    for index in range(count):
        ident = f'EVT_BENCH_{index:06}'
        stamp = (start + timedelta(seconds=index)).isoformat(timespec='microseconds')
        events.append((ident, 'CAM_001', str(index), 'helmet' if index % 2 else 'vest',
                       stamp, stamp, 1, 1, '', '', .8, 1, 0, 'synthetic-benchmark',
                       'pending' if index % 5 < 3 else ('confirmed' if index % 5 == 3 else 'hold')))
        if index % 10 == 0:
            # One metadata-only image per ten events: no real/raw/processed files.
            media.append((str(UUID(int=index+1)), ident, 'CAM_001', 'image', 'thumbnail',
                          'failed', 'failed', stamp, 'synthetic_fixture'))
    with connect() as conn:
        conn.execute('DELETE FROM candidate_event')
        conn.executemany('''INSERT INTO candidate_event(event_id,camera_id,tracking_id,ppe_type,
            timestamp_start,timestamp_end,duration_sec,frame_sample_count,thumbnail_path,
            video_clip_path,ai_confidence,person_detected,ppe_detected,model_version,event_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', events)
        conn.executemany('INSERT INTO event_retention(event_id) VALUES(?)', [(event[0],) for event in events])
        conn.executemany('''INSERT INTO event_media(media_id,event_id,camera_id,kind,role,
            status,redaction_status,created_at,error_code) VALUES(?,?,?,?,?,?,?,?,?)''', media)
        conn.executemany('INSERT INTO processing_review_only_event(event_id,review_only) VALUES(?,1)',
                         [(events[index][0],) for index in range(0, count, 10)])
        conn.execute('ANALYZE')


def measure(client, endpoint, headers, adapter, repeats, expected, name):
    counters = {'sql': 0, 'connections': 0}
    execute, initialize = adapter.execute, adapter.__init__

    def counted_execute(connection, *args, **kwargs):
        counters['sql'] += 1
        return execute(connection, *args, **kwargs)

    def counted_init(connection, *args, **kwargs):
        counters['connections'] += 1
        return initialize(connection, *args, **kwargs)

    samples = []
    with patch.object(adapter, 'execute', counted_execute), patch.object(adapter, '__init__', counted_init):
        for index in range(repeats+1):
            counters.update(sql=0, connections=0)
            started = time.perf_counter()
            response = client.get(endpoint, headers=headers)
            elapsed = time.perf_counter()-started
            if response.status_code != 200:
                raise RuntimeError(f'{name} HTTP {response.status_code}')
            payload = response.json()
            if payload['total'] != expected:
                raise RuntimeError('Benchmark total mismatch')
            if name == 'legacy_full' and len(payload['items']) != expected:
                raise RuntimeError('Legacy baseline must return the complete set')
            if name == 'default_page' and (payload['limit'] != 50 or len(payload['items']) != 50 or not payload['has_more']):
                raise RuntimeError('Proposed default must return only the first 50 events')
            if name == 'summary' and (sum(payload['by_status'].values()) != expected or len(payload['recent_pending']) != 10):
                raise RuntimeError('Summary contract mismatch')
            if index:  # Exclude one warm-up request per endpoint/size.
                samples.append({'seconds': elapsed, 'response_bytes': len(response.content),
                                'application_sql_calls': counters['sql'], 'connections': counters['connections']})
    timings = [item['seconds'] for item in samples]
    return {'endpoint': endpoint, 'name': name, 'samples': samples,
            'median_seconds': statistics.median(timings), 'min_seconds': min(timings),
            'max_seconds': max(timings), 'response_bytes': samples[0]['response_bytes'],
            'bytes_per_minute_at_20s_poll': samples[0]['response_bytes']*3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--variant', choices=['legacy', 'paged'], default='paged')
    parser.add_argument('--counts', type=int, nargs='+', default=[1000, 10000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('PPE_TEST_POSTGRES_URL'):
        parser.error('Dedicated PostgreSQL test URL required; no SQLite or production fallback')
    if not 1 <= args.repeats <= 10 or any(count not in (1000, 10000) for count in args.counts):
        parser.error('Use 1000/10000 rows and 1..10 measured repetitions')
    checkout = args.checkout.resolve()
    sys.path.insert(0, str(checkout))
    from backend.tests.support import isolated_api, login
    from backend.db.event_repository import get_connection
    from backend.db.connection import PostgresConnection
    revision = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(checkout), 'diff', '--quiet']).returncode != 0
    report = {'variant': args.variant, 'revision': revision, 'tracked_changes': dirty,
              'platform': platform.platform(), 'python': platform.python_version(), 'repeats': args.repeats,
              'transport': 'FastAPI TestClient; PostgreSQL on localhost; no TLS/Cloudflare/browser',
              'fixture': 'metadata-only image for 10% of events; 10% review-only; 60% pending', 'cases': []}
    for count in args.counts:
        print(f'Benchmark {args.variant}: {count} events', flush=True)
        with isolated_api() as client:
            headers = login(client)
            seed(count, get_connection)
            with get_connection() as conn:
                report['postgres'] = conn.execute('SELECT version()').fetchone()[0]
            endpoints = [('legacy_full', '/api/events')] if args.variant == 'legacy' else [
                ('default_page', '/api/events'), ('summary', '/api/events/summary')]
            case = {'event_count': count, 'requests': []}
            for name, endpoint in endpoints:
                result = measure(client, endpoint, headers, PostgresConnection, args.repeats, count, name)
                case['requests'].append(result)
                print(f"  {name}: median={result['median_seconds']:.4f}s bytes={result['response_bytes']}", flush=True)
            report['cases'].append(case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
