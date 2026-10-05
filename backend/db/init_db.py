import os
import sqlite3
from pathlib import Path

if __package__:
    from .migrations import LATEST_VERSION, apply_migrations
else:  # Supports the documented `python backend/db/init_db.py` command.
    from migrations import LATEST_VERSION, apply_migrations


BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "ppe_system.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
SEED_PATH = BASE_DIR / "seed.sql"


def run_sql_file(cursor: sqlite3.Cursor, file_path: Path) -> None:
    """SQL 파일을 읽어서 실행한다."""
    if not file_path.exists():
        raise FileNotFoundError(f"SQL file not found: {file_path}")

    sql = file_path.read_text(encoding="utf-8")
    cursor.executescript(sql)


def init_database() -> None:
    """SQLite DB 파일을 생성하고 초기 테이블 및 더미 데이터를 삽입한다."""
    db_path = os.environ.get("PPE_DB_PATH", str(DB_PATH))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.cursor()
        if cursor.execute("PRAGMA user_version").fetchone()[0] > LATEST_VERSION:
            raise RuntimeError("Database version is newer than this application")

        # 외래키 제약 조건 활성화
        cursor.execute("PRAGMA foreign_keys = ON;")

        run_sql_file(cursor, SCHEMA_PATH)
        run_sql_file(cursor, SEED_PATH)

        # New seed rows and pre-existing rows both receive media records once.
        apply_migrations(conn)

        conn.commit()

        print(f"Database initialized successfully: {db_path}")

    except Exception as error:
        conn.rollback()
        print(f"Database initialization failed: {error}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
