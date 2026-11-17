"""Opt-in clock proof v1. Bounds are engineering policy, not measured drift.

The authenticated capture agent is trusted to report real source samples.
No signature or nonce can independently authenticate its physical clock.
"""
from dataclasses import dataclass, asdict
import math
import time

VERSION = 1


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('clock_number_invalid')
    return float(value)


@dataclass(frozen=True)
class ClockPolicy:
    ttl_seconds: float
    max_rtt_seconds: float
    drift_ppm: float
    sample_error_seconds: float
    max_error_seconds: float
    step_tolerance_seconds: float

    def __post_init__(self):
        values = asdict(self)
        for value in values.values(): number(value)
        if not (0 < self.max_rtt_seconds < self.ttl_seconds <= 300
                and 0 <= self.drift_ppm <= 1000
                and 0 < self.sample_error_seconds <= self.max_error_seconds <= .25
                and 0 < self.step_tolerance_seconds <= self.max_error_seconds):
            raise ValueError('clock_policy_invalid')

    def drift(self):
        return self.ttl_seconds * self.drift_ppm / 1_000_000

    def public(self): return asdict(self)


class AnchoredClock:
    """One fixed UTC mapping; wall steps never silently change timestamps."""
    def __init__(self, anchor, policy, *, wall=time.time, mono=time.monotonic):
        if set(anchor) != {'utc', 'monotonic'}: raise ValueError('clock_anchor_invalid')
        self.utc, self.monotonic = number(anchor['utc']), number(anchor['monotonic'])
        self.policy, self.wall, self.mono = policy, wall, mono
        self.last = self.monotonic
        self.last_wall = self.utc

    def now(self):
        current = number(self.mono())
        if current < self.last: raise ValueError('clock_monotonic_regressed')
        stamp = self.utc + current - self.monotonic
        wall=number(self.wall());elapsed=current-self.last
        allowance = self.policy.step_tolerance_seconds + elapsed*self.policy.drift_ppm/1_000_000
        if abs(wall-self.last_wall-elapsed) > allowance: raise ValueError('clock_wall_step')
        self.last,self.last_wall=current,wall
        return stamp


def sample_interval(server, sent, received, mono_sent, mono_received, policy):
    server, sent, received, mono_sent, mono_received = map(number, (server, sent, received, mono_sent, mono_received))
    elapsed = mono_received-mono_sent
    if not (0 <= elapsed <= policy.max_rtt_seconds) or abs((received-sent)-elapsed) > policy.sample_error_seconds:
        raise ValueError('clock_sample_timing_invalid')
    low = server-received-policy.sample_error_seconds
    high = server-sent+policy.sample_error_seconds
    if not all(math.isfinite(value) for value in (low,high,high-low)) or (high-low)/2 + policy.drift() > policy.max_error_seconds:
        raise ValueError('clock_sample_uncertain')
    return low, high


def covers(proof, start, end, policy):
    start, end = number(start), number(end)
    return (start >= proof['source_received_at'] and end > start
            and end+proof['offset_high']+policy.drift() <= proof['valid_until'])


def within_envelope(proof, offset, error, policy):
    return (max(abs(number(proof['offset_low'])-number(offset)),
                abs(number(proof['offset_high'])-offset)) + policy.drift() <= number(error))
