import sqlite3
from pathlib import Path


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
    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.cursor()

        # 외래키 제약 조건 활성화
        cursor.execute("PRAGMA foreign_keys = ON;")

        run_sql_file(cursor, SCHEMA_PATH)
        run_sql_file(cursor, SEED_PATH)

        conn.commit()

        print(f"Database initialized successfully: {DB_PATH}")

    except Exception as error:
        conn.rollback()
        print(f"Database initialization failed: {error}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    init_database()