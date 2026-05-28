"""임시 테스트용 이벤트 15개 삽입 스크립트."""

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "ppe_system.db"

EVENTS = [
    ("EVT_0004", "CAM_001", "TRK_004", "helmet", "2026-04-29 08:12:05", "2026-04-29 08:12:09", 4, 92, 0.91),
    ("EVT_0005", "CAM_002", "TRK_005", "vest",   "2026-04-29 09:35:40", "2026-04-29 09:35:44", 4, 88, 0.78),
    ("EVT_0006", "CAM_001", "TRK_006", "helmet", "2026-04-30 10:21:17", "2026-04-30 10:21:20", 3, 72, 0.85),
    ("EVT_0007", "CAM_002", "TRK_007", "vest",   "2026-04-30 13:50:02", "2026-04-30 13:50:07", 5, 112, 0.62),
    ("EVT_0008", "CAM_001", "TRK_008", "helmet", "2026-05-02 07:58:33", "2026-05-02 07:58:36", 3, 68, 0.93),
    ("EVT_0009", "CAM_002", "TRK_009", "vest",   "2026-05-02 11:14:55", "2026-05-02 11:15:01", 6, 136, 0.74),
    ("EVT_0010", "CAM_001", "TRK_010", "helmet", "2026-05-05 09:03:28", "2026-05-05 09:03:31", 3, 76, 0.88),
    ("EVT_0011", "CAM_002", "TRK_011", "helmet", "2026-05-06 14:22:11", "2026-05-06 14:22:15", 4, 100, 0.67),
    ("EVT_0012", "CAM_001", "TRK_012", "vest",   "2026-05-07 08:45:00", "2026-05-07 08:45:04", 4, 84, 0.96),
    ("EVT_0013", "CAM_002", "TRK_013", "vest",   "2026-05-08 10:30:20", "2026-05-08 10:30:25", 5, 108, 0.71),
    ("EVT_0014", "CAM_001", "TRK_014", "helmet", "2026-05-09 07:15:44", "2026-05-09 07:15:47", 3, 64, 0.89),
    ("EVT_0015", "CAM_002", "TRK_015", "vest",   "2026-05-12 13:08:37", "2026-05-12 13:08:42", 5, 116, 0.76),
    ("EVT_0016", "CAM_001", "TRK_016", "helmet", "2026-05-13 09:55:10", "2026-05-13 09:55:14", 4, 96, 0.83),
    ("EVT_0017", "CAM_002", "TRK_017", "vest",   "2026-05-14 11:40:05", "2026-05-14 11:40:09", 4, 80, 0.65),
    ("EVT_0018", "CAM_001", "TRK_018", "helmet", "2026-05-15 08:20:33", "2026-05-15 08:20:37", 4, 90, 0.92),
]

SQL = """
INSERT OR IGNORE INTO candidate_event (
    event_id,
    camera_id,
    tracking_id,
    ppe_type,
    timestamp_start,
    timestamp_end,
    duration_sec,
    frame_sample_count,
    thumbnail_path,
    video_clip_path,
    ai_confidence,
    person_detected,
    ppe_detected,
    model_version,
    event_status
)
VALUES (
    :event_id,
    :camera_id,
    :tracking_id,
    :ppe_type,
    :timestamp_start,
    :timestamp_end,
    :duration_sec,
    :frame_sample_count,
    :thumbnail_path,
    :video_clip_path,
    :ai_confidence,
    :person_detected,
    :ppe_detected,
    :model_version,
    :event_status
);
"""


def make_event_row(ev: tuple) -> dict:
    """EVENTS 튜플을 candidate_event 저장용 dict로 변환함."""
    (
        event_id,
        camera_id,
        tracking_id,
        ppe_type,
        timestamp_start,
        timestamp_end,
        duration_sec,
        frame_sample_count,
        ai_confidence,
    ) = ev

    return {
        "event_id": event_id,
        "camera_id": camera_id,
        "tracking_id": tracking_id,
        "ppe_type": ppe_type,
        "timestamp_start": timestamp_start,
        "timestamp_end": timestamp_end,
        "duration_sec": duration_sec,
        "frame_sample_count": frame_sample_count,
        "thumbnail_path": f"storage/candidate_events/thumbnails/{event_id}.jpg",
        "video_clip_path": f"storage/candidate_events/clips/{event_id}.mp4",
        "ai_confidence": ai_confidence,
        "person_detected": 1,
        "ppe_detected": 0,
        "model_version": "yolov8n_v1",
        "event_status": "pending",
    }


def main() -> None:
    inserted = 0

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")

        for ev in EVENTS:
            row = make_event_row(ev)
            cursor = conn.execute(SQL, row)

            # INSERT OR IGNORE 때문에 이미 존재하는 event_id는 실제 삽입되지 않을 수 있음.
            inserted += cursor.rowcount

        total_pending = conn.execute(
            "SELECT COUNT(*) FROM candidate_event WHERE event_status = 'pending';"
        ).fetchone()[0]

        total_all = conn.execute(
            "SELECT COUNT(*) FROM candidate_event;"
        ).fetchone()[0]

    print(
        f"삽입 완료: {inserted}개 신규 삽입 / "
        f"전체 이벤트 {total_all}개 / pending {total_pending}개"
    )


if __name__ == "__main__":
    main()