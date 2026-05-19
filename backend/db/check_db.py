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

        cursor.execute(
            """
            SELECT
                event_id,
                camera_id,
                ppe_type,
                duration_sec,
                ai_confidence,
                event_status
            FROM candidate_event;
            """
        )
        events = cursor.fetchall()
        print_rows("candidate_event", events)

        cursor.execute(
            """
            SELECT
                quarter,
                candidate_count,
                confirmed_count,
                false_positive_count,
                hold_count,
                created_at
            FROM quarterly_summary;
            """
        )
        quarterly_summary = cursor.fetchall()
        print_rows("quarterly_summary", quarterly_summary)

        cursor.execute(
            """
            SELECT
                stat_id,
                quarter,
                ppe_type,
                confirmed_count,
                priority_score
            FROM quarterly_ppe_stats
            ORDER BY priority_score DESC;
            """
        )
        quarterly_ppe_stats = cursor.fetchall()
        print_rows("quarterly_ppe_stats", quarterly_ppe_stats)

        cursor.execute(
            """
            SELECT
                stat_id,
                quarter,
                zone_name,
                confirmed_count,
                priority_score
            FROM quarterly_zone_stats
            ORDER BY priority_score DESC;
            """
        )
        quarterly_zone_stats = cursor.fetchall()
        print_rows("quarterly_zone_stats", quarterly_zone_stats)

        cursor.execute(
            """
            SELECT
                trend_id,
                target_quarter,
                quarter,
                helmet,
                vest
            FROM quarterly_trend_stats
            ORDER BY quarter ASC;
            """
        )
        quarterly_trend_stats = cursor.fetchall()
        print_rows("quarterly_trend_stats", quarterly_trend_stats)

        cursor.execute(
            """
            SELECT
                recommendation_id,
                quarter,
                recommendation_rank,
                ppe_type,
                zone_name,
                education_topic,
                priority_score
            FROM education_recommendation
            ORDER BY recommendation_rank ASC;
            """
        )
        recommendations = cursor.fetchall()
        print_rows("education_recommendation", recommendations)

        cursor.execute(
            """
            SELECT
                aggregate_id,
                quarter,
                zone_name,
                ppe_type,
                false_positive_count,
                updated_at
            FROM false_positive_aggregate
            ORDER BY quarter, zone_name, ppe_type;
            """
        )
        false_positive_aggregate = cursor.fetchall()
        print_rows("false_positive_aggregate", false_positive_aggregate)

        cursor.execute(
            """
            SELECT
                setting_id,
                enabled,
                default_language,
                cooldown_sec,
                updated_at
            FROM broadcast_setting;
            """
        )
        broadcast_setting = cursor.fetchall()
        print_rows("broadcast_setting", broadcast_setting)

        cursor.execute(
            """
            SELECT
                template_id,
                setting_id,
                ppe_type,
                zone_name,
                language,
                message
            FROM broadcast_message_template
            ORDER BY template_id;
            """
        )
        broadcast_templates = cursor.fetchall()
        print_rows("broadcast_message_template", broadcast_templates)

    finally:
        conn.close()


if __name__ == "__main__":
    check_database()