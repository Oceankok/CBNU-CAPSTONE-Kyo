"""Prototype prioritization weights; these are not industrial risk assessments."""
import json
import math
import os

DEFAULT_PROCESS_WEIGHTS = {'프레스': 1.5, '절단': 1.5, 'press': 1.5, 'cutting': 1.5}


def process_risk_weight(process_type):
    weights = dict(DEFAULT_PROCESS_WEIGHTS)
    raw = os.environ.get('PPE_PROCESS_RISK_WEIGHTS', '')
    if raw:
        overrides = json.loads(raw)
        if not isinstance(overrides, dict) or any(
            not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 5
            for key, value in overrides.items()
        ):
            raise ValueError('PPE_PROCESS_RISK_WEIGHTS must map process names to finite weights between 0 and 5')
        weights.update({key.strip().casefold(): value for key, value in overrides.items()})
    return float(weights.get((process_type or '').strip().casefold(), 1.0))
