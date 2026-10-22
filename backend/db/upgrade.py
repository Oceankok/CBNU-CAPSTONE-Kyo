"""Explicit PostgreSQL upgrade preflight/apply for a paused release deployment."""
import argparse
import json
import re

from backend.db.connection import connect, is_postgres, schema_version
from backend.db.migrations import LATEST_VERSION
from backend.db.postgres import initialize

def inspect(expected_version, target_version):
    if not is_postgres():
        raise ValueError('Release upgrades require PostgreSQL')
    if target_version != LATEST_VERSION or not 10 <= expected_version < target_version:
        raise ValueError('Unsupported upgrade range for this release')
    with connect() as conn:
        actual = schema_version(conn)
        if actual != expected_version:
            raise ValueError('Unexpected database schema version')
        if conn.execute("SELECT 1 FROM processing_job WHERE status IN ('queued','running') OR local_cleanup_status='pending' LIMIT 1").fetchone():
            raise ValueError('Drain processing jobs and worker cleanup before upgrade')
        tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename!='app_schema_version' ORDER BY tablename")]
        if any(not re.fullmatch(r'[a-z_][a-z0-9_]*', table) for table in tables):
            raise ValueError('Unsupported table name in upgrade scope')
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables}
    return {'from_version': actual, 'target_version': target_version, 'table_counts': counts}


def apply(expected_version, target_version, *, writers_paused):
    if not writers_paused:
        raise ValueError('Explicit writers-paused assertion required')
    before = inspect(expected_version, target_version)
    initialize(seed=False)
    with connect() as conn:
        if schema_version(conn) != target_version:
            raise ValueError('Upgrade schema verification failed')
        after = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in before['table_counts']}
    if after != before['table_counts']:
        raise ValueError('Upgrade row preservation verification failed; restore release backup')
    return {**before, 'result': 'PASS', 'rows_preserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('inspect', 'apply'))
    parser.add_argument('--expected-version', type=int, required=True)
    parser.add_argument('--target-version', type=int, required=True)
    parser.add_argument('--writers-paused', action='store_true')
    args = parser.parse_args()
    result = (apply(args.expected_version, args.target_version, writers_paused=args.writers_paused)
              if args.operation == 'apply' else inspect(args.expected_version, args.target_version))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
