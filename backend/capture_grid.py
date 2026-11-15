"""Shared, opt-in CFR capture obligation; observation timestamps stay unchanged."""
from datetime import timedelta


MAX_CAPTURE_OUTPUT_FPS = 120


def validate_output_fps(value):
    if type(value) is not int or not 1 <= value <= MAX_CAPTURE_OUTPUT_FPS:
        raise ValueError('capture_output_fps')
    return value


def required_capture_end(timestamp_start, timestamp_end, output_fps=None):
    """First output boundary covering post-roll at existing wire precision.

    Use integer microseconds, matching wire/storage precision. The grid starts
    at the two-second pre-roll boundary. Legacy sessions keep their exact end.
    An extended obligation is not evidence: callers must still observe it.
    """
    nominal = timestamp_end + timedelta(seconds=2)
    if output_fps is None:
        return nominal
    fps = validate_output_fps(output_fps)
    origin = timestamp_start - timedelta(seconds=2)
    span_us = (nominal - origin) // timedelta(microseconds=1)
    if span_us <= 0:
        raise ValueError('capture_interval')
    product = span_us * fps
    nearest = (product + 500_000) // 1_000_000
    # Preserve the existing one-microsecond outer-end wire tolerance. A
    # serialized exact grid point must not acquire a whole extra frame.
    frames = (nearest if abs(product-nearest*1_000_000) <= fps
              else (product + 999_999) // 1_000_000)
    # Round the rational output boundary to the existing microsecond wire.
    offset_us = (frames * 1_000_000 + fps // 2) // fps
    return origin + timedelta(microseconds=offset_us)
