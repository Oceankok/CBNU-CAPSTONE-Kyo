"""seed_events.py로 삽입한 테스트 이벤트의 전체 컬럼과 매핑을 확인하는 스크립트."""

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "ppe_system.db"


def print_event_rows(rows: list[sqlite3.Row]) -> None:
    print("\n[seed candidate_event rows]")

    for row in rows:
        print("-" * 80)

        for key in row.keys():
            print(f"{key}: {row[key]}")


def validate_event_row(row: sqlite3.Row) -> None:
    event_id = row["event_id"]

    expected_thumbnail_path = (
        f"storage/candidate_events/thumbnails/{event_id}.jpg"
    )
    expected_video_clip_path = (
        f"storage/candidate_events/clips/{event_id}.mp4"
    )

    if row["thumbnail_path"] != expected_thumbnail_path:
        raise AssertionError(
            f"{event_id} thumbnail_path mismatch: "
            f"expected={expected_thumbnail_path}, actual={row['thumbnail_path']}"
        )

    if row["video_clip_path"] != expected_video_clip_path:
        raise AssertionError(
            f"{event_id} video_clip_path mismatch: "
            f"expected={expected_video_clip_path}, actual={row['video_clip_path']}"
        )

    if not isinstance(row["ai_confidence"], float):
        raise AssertionError(
            f"{event_id} ai_confidence must be float: "
            f"actual={row['ai_confidence']} ({type(row['ai_confidence']).__name__})"
        )


def check_seed_events() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB file not found: {DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT *
            FROM candidate_event
            WHERE event_id BETWEEN 'EVT_0004' AND 'EVT_0018'
            ORDER BY event_id;
            """
        ).fetchall()

    if len(rows) != 15:
        raise AssertionError(f"Expected 15 seed events, but found {len(rows)}")

    print_event_rows(rows)

    for row in rows:
        validate_event_row(row)

    print("\n[OK] seed_events column mapping is valid.")
    print(f"[OK] checked events: {len(rows)}")


if __name__ == "__main__":
    check_seed_events()