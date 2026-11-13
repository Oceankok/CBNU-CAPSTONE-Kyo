"""SQLite development compatibility and explicit PostgreSQL connections."""
import os
from pathlib import Path
import re
import sqlite3
import time

try:
    import psycopg
except ImportError:
    psycopg = None

IntegrityError = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())
DatabaseError = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())


def is_postgres():
    url = os.environ.get("PPE_DATABASE_URL", "")
    if url and not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("PPE_DATABASE_URL must be a PostgreSQL URL")
    return bool(url)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def parameters_sql(statement):
    # Preserve quoted SQL literals/identifiers; translate only bind markers.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", statement)
    return ''.join(re.sub(r'(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', part.replace('?', '%s')) if index % 2 == 0 else part
                   for index, part in enumerate(parts))


def parameters_values(parameters):
    if parameters is None:
        return None
    if isinstance(parameters, dict):
        return {key: int(value) if isinstance(value, bool) else value for key, value in parameters.items()}
    return tuple(int(value) if isinstance(value, bool) else value for value in parameters)


class PostgresConnection:
    dialect = "postgresql"

    def __init__(self):
        if psycopg is None:
            raise RuntimeError("Install backend/requirements-postgres.txt for PostgreSQL")
        schema = os.environ.get("PPE_DB_SCHEMA", "public")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid PPE_DB_SCHEMA")
        self.raw = psycopg.connect(os.environ["PPE_DATABASE_URL"], row_factory=row_factory,
                                  options=f"-c search_path={schema}", connect_timeout=10)

    def execute(self, statement, parameters=None):
        sql = parameters_sql(statement.replace('%', '%%')) if parameters is not None else statement
        return self.raw.execute(sql, parameters_values(parameters))

    def executemany(self, statement, parameters):
        cursor = self.raw.cursor()
        cursor.executemany(parameters_sql(statement.replace('%', '%%')), [parameters_values(row) for row in parameters])
        return cursor

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.rollback() if kind else self.commit()
        finally:
            self.close()


def connect():
    if is_postgres():
        return PostgresConnection()
    path = os.environ.get("PPE_DB_PATH", str(Path(__file__).resolve().parent / "ppe_system.db"))
    conn = sqlite3.connect(path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def begin_write(conn):
    if getattr(conn, "dialect", "sqlite") == "postgresql":
        # Keep SQLite's serialized critical sections during the transition.
        # Schema-scoped: independent CI fixtures never share this lock.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 129))")
    else:
        conn.execute("BEGIN IMMEDIATE")


def run_stats_transaction(operation, *, write=False, connection_factory=connect):
    """Run DB-only statistics work against one snapshot, including its response.

    PostgreSQL's advisory lock can wait with an already established snapshot.
    Retry serialization failures with a new connection and snapshot, including
    failures raised by commit. Other errors and SQLite lock errors are not retried.
    Do not use this helper for operations with non-transactional side effects.
    """
    for attempt in range(3):
        postgres = False
        try:
            with connection_factory() as conn:
                postgres = getattr(conn, "dialect", "sqlite") == "postgresql"
                if postgres:
                    # Must precede the advisory-lock SELECT and every data read.
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                                 + ("" if write else " READ ONLY"))
                    if write:
                        begin_write(conn)
                elif write:
                    begin_write(conn)
                else:
                    conn.execute("BEGIN")
                result = operation(conn)
            # Return only after the context manager has committed successfully.
            return result
        except Exception as exc:
            if not postgres or getattr(exc, "sqlstate", None) != "40001" or attempt == 2:
                raise
            time.sleep(0.01 * (attempt + 1))


def schema_version(conn):
    if getattr(conn, "dialect", "sqlite") == "postgresql":
        return conn.execute("SELECT version FROM app_schema_version WHERE singleton=1").fetchone()[0]
    return conn.execute("PRAGMA user_version").fetchone()[0]


def week_expression(column):
    if is_postgres():
        return f"FLOOR((EXTRACT(DOY FROM {column}::timestamp)+7-EXTRACT(ISODOW FROM {column}::timestamp))/7)"
    return f"strftime('%W', {column})"
