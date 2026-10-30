"""Event instants are stored in UTC; reporting uses contemporary Korean time."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), 'Asia/Seoul')
REPORTING_TIMEZONE = 'Asia/Seoul'


def parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def stored_timestamp(value):
    if not value:
        return value
    stamp = parse(value)
    # Preserve legacy wall-clock values. Their source timezone is not known.
    return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds') if stamp.tzinfo else value


def public_timestamp(value):
    if not value:
        return value
    stamp = parse(value)
    return stamp.astimezone(KST).isoformat(timespec='microseconds') if stamp.tzinfo else value


def local_date(value):
    if not value:
        return None
    stamp = parse(value)
    return stamp.astimezone(KST).date().isoformat() if stamp.tzinfo else None


def quarter_for(value):
    stamp = parse(value)
    if stamp.tzinfo:
        stamp = stamp.astimezone(KST)
    return f'{stamp.year}-Q{(stamp.month-1)//3+1}'


def local_time_sql(column):
    """A local wall-clock projection, keeping unknown legacy values as recorded.

    Column names are internal constants, never client input. Drop subseconds before
    SQL date parsing so SQLite rounding cannot move 23:59:59.999999 across a day.
    """
    from backend.db.connection import is_postgres
    offset = f"(substr({column},-6,1) IN ('+','-') OR substr({column},-1)='Z')"
    if is_postgres():
        converted = f"to_char(({column}::timestamptz AT TIME ZONE 'Asia/Seoul'),'YYYY-MM-DD HH24:MI:SS')"
    else:
        suffix = f"CASE WHEN substr({column},-1)='Z' THEN 'Z' ELSE substr({column},-6) END"
        converted = f"datetime(substr({column},1,19)||({suffix}),'+9 hours')"
    return f"CASE WHEN {offset} THEN {converted} ELSE replace(substr({column},1,19),'T',' ') END"
