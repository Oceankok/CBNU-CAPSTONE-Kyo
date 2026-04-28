import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "ppe_system.db"


def print_rows(title: str, rows: list[tuple]) -> None:
    print(f"\n[{title}]")
    for row in rows:
        print(row)


def check_database() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB file not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print_rows("tables", tables)

        cursor.execute("SELECT * FROM camera_info;")
        cameras = cursor.fetchall()
        print_rows("camera_info", cameras)

        cursor.execute("SELECT event_id, camera_id, ppe_type, duration_sec, ai_confidence, event_status FROM candidate_event;")
        events = cursor.fetchall()
        print_rows("candidate_event", events)

    finally:
        conn.close()


if __name__ == "__main__":
    check_database()