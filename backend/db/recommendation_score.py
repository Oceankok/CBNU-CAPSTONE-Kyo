"""Approved zone/PPE scoring with calendar-aligned KST week coverage."""
from datetime import date, timedelta
from backend.db.connection import is_postgres

SCORE_VERSION = 'zone_ppe_v2'


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def period_week_count(start: str, end: str) -> int:
    """Count Monday buckets intersecting [start,end), including partial weeks."""
    first, stop = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    if stop <= first:
        raise ValueError('Scoring period must be nonempty')
    return (monday(stop-timedelta(days=1))-monday(first)).days // 7 + 1


def monday_expression(local_wall_clock: str) -> str:
    if is_postgres():
        return f"to_char(date_trunc('week', ({local_wall_clock})::timestamp),'YYYY-MM-DD')"
    return f"date(({local_wall_clock}), '-' || ((CAST(strftime('%w', ({local_wall_clock})) AS INTEGER)+6)%7) || ' days')"


def risk_multiplier(level: int | None) -> float:
    if level is None:
        return 1.0
    if type(level) is not int or not 1 <= level <= 5:
        raise ValueError('Stored zone risk level must be an integer from 1 through 5')
    return 1.0 + (level-1)*0.25


def score(count: int, active_weeks: int, period_weeks: int, risk_level: int | None) -> float:
    if period_weeks < 1 or not 0 <= active_weeks <= period_weeks:
        raise ValueError('Invalid occurrence-week coverage')
    return count * (1 + .2 * active_weeks / period_weeks) * risk_multiplier(risk_level)
