"""Validate an administrative capture context without guessing physical location.

The caller must load the context and clock assessment from trusted server state,
not event payload fields. This pure module does not issue tokens, authenticate
clients, persist contexts, or modify event_zone_snapshot/statistics.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math


@dataclass(frozen=True)
class CaptureContext:
    context_id: str
    camera_id: str
    session_id: str
    assignment_version: int
    zone_name: str | None
    valid_from: datetime
    # Explicit server-defined effective boundary. Never inferred from receipt time.
    valid_until: datetime | None = None


@dataclass(frozen=True)
class Attribution:
    status: str
    reason: str
    zone_name: str | None = None
    context_id: str | None = None
    assignment_version: int | None = None
    basis: str = 'administrative_capture_context'


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone_aware_timestamp_required')
    return value.astimezone(timezone.utc)


def resolve_capture_context(*, context, context_id, camera_id, session_id,
                            assignment_version, capture_start, capture_end,
                            clock_verified=False, clock_error_seconds=None):
    """Return unknown unless every supplied evidence boundary is supported.

    Capture interval is conservatively inclusive. A sample exactly on a context
    retirement boundary is ambiguous. Clock error must bound BOTH endpoints.
    A result means the interval fits recorded administrative context, not proof
    of the camera's physical placement. Unknown never falls back to current zone.
    """
    def unknown(reason):
        return Attribution('unknown', reason)
    if not isinstance(context, CaptureContext):
        return unknown('missing_trusted_context')
    if not all(isinstance(v, str) and v.strip() for v in
               (context.context_id, context.camera_id, context.session_id)):
        return unknown('invalid_trusted_context')
    if type(context.assignment_version) is not int or context.assignment_version < 1:
        return unknown('invalid_assignment_version')
    if (type(assignment_version) is not int or
        (context_id, camera_id, session_id, assignment_version) !=
        (context.context_id, context.camera_id, context.session_id, context.assignment_version)):
        return unknown('context_binding_mismatch')
    if clock_verified is not True:
        return unknown('unverified_clock')
    if (type(clock_error_seconds) not in (int, float) or
        not math.isfinite(clock_error_seconds) or clock_error_seconds < 0):
        return unknown('invalid_clock_error_bound')
    try:
        start, end = utc(capture_start), utc(capture_end)
        lower = utc(context.valid_from)
        upper = utc(context.valid_until) if context.valid_until is not None else None
        if end < start or (upper is not None and upper <= lower):
            return unknown('invalid_interval')
        margin = timedelta(seconds=clock_error_seconds)
        earliest, latest = start - margin, end + margin
    except (ValueError, TypeError, OverflowError):
        return unknown('invalid_timestamp')
    if earliest < lower:
        return unknown('capture_before_context_or_uncertain_boundary')
    if upper is not None and latest >= upper:
        return unknown('capture_crosses_retirement_or_uncertain_boundary')
    if context.zone_name is None:
        return unknown('context_has_no_zone')
    if not isinstance(context.zone_name, str) or not context.zone_name.strip():
        return unknown('invalid_context_zone')
    return Attribution('supported', 'interval_within_trusted_context', context.zone_name,
                       context.context_id, context.assignment_version)
