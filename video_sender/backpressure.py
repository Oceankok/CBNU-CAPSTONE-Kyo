"""Opt-in bounded retry and soft spool protection. Not an OS disk quota."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import shutil
import time


@dataclass(frozen=True)
class Policy:
    threshold: int
    reserve: int
    min_free: int

    @classmethod
    def from_env(cls):
        if os.environ.get('VIDEO_BACKPRESSURE_ENABLED', '').lower() != 'true':
            return None
        try:
            values = [int(os.environ[k]) for k in
                      ('VIDEO_SPOOL_THRESHOLD_BYTES', 'VIDEO_SPOOL_RESERVE_BYTES', 'VIDEO_SPOOL_MIN_FREE_BYTES')]
        except (KeyError, ValueError):
            raise ValueError('backpressure_requires_explicit_threshold_reserve_min_free') from None
        if not 0 < values[1] < values[0] or values[2] < values[1]:
            raise ValueError('invalid_backpressure_budget')
        return cls(*values)

    def reason(self, directory):
        root = Path(directory)
        used = 0
        for p in root.iterdir():
            try:
                if p.is_file(): used += p.stat().st_size
            except FileNotFoundError: pass  # Concurrent acknowledged cleanup.
        if used >= self.threshold-self.reserve:
            return 'spool_soft_stop_threshold'
        if shutil.disk_usage(root).free <= self.min_free+self.reserve:
            return 'spool_free_space_reserve'
        return None


def retry_delay(header, now=None):
    if header is None:
        return 1.0
    value = str(header).strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
        if moment.tzinfo is None: raise ValueError()
        return max(0.0, (moment-(now or datetime.now(timezone.utc))).total_seconds())
    except (ValueError, TypeError, OverflowError):
        raise ValueError('invalid_retry_after') from None


def retry_upload(attempt, *, policy, guard=lambda: None, monotonic=time.monotonic, sleep=time.sleep):
    if policy is None:
        return attempt(20)
    started = monotonic()
    for number in range(5):
        guard()  # Observes stop/budget while preserving permission to drain.
        remaining = 60-(monotonic()-started)
        if remaining <= 0: raise TimeoutError('backpressure_retry_budget_exhausted')
        response = attempt(min(20, remaining))
        if response.status_code != 429:
            return response
        if number == 4: return response
        delay = retry_delay(response.headers.get('Retry-After'))
        remaining = 60-(monotonic()-started)
        if delay >= remaining:
            raise TimeoutError('retry_after_exceeds_remaining_budget')
        until = monotonic()+delay
        while monotonic() < until:
            guard()
            remaining_wait=until-monotonic()
            if remaining_wait <= 0:break
            sleep(min(.2, remaining_wait))
    raise AssertionError('unreachable')
