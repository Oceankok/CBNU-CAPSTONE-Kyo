"""Offline, explicitly labelled event-score evaluation. Never enables decisions.

Usage: python -m backend.auto_decision.evaluate_dataset labels.jsonl --threshold 0.9
No database, provider, detector, model, or network access is performed.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import json
import math
from pathlib import Path
import sys

from backend.zones.policy import ALLOWED_PPE

_FIELDS = frozenset({'event_id', 'model_version', 'ppe_type', 'label', 'score'})
_LABELS = frozenset({'violation', 'non_violation'})


def _unit_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be a finite number in [0,1]')
    try:
        valid = math.isfinite(value) and 0 <= value <= 1
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f'{field} must be a finite number in [0,1]')
    return float(value)


def _record(value):
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError('each record must contain exactly event_id, model_version, ppe_type, label, score')
    for field in ('event_id', 'model_version', 'ppe_type'):
        item = value[field]
        if not isinstance(item, str) or not item or item != item.strip() or not item.strip():
            raise ValueError(f'{field} must be a nonblank exact string')
    if value['ppe_type'] not in ALLOWED_PPE:
        raise ValueError('ppe_type is not in the supported PPE vocabulary')
    if not isinstance(value['label'], str) or value['label'] not in _LABELS:
        raise ValueError('label must be violation or non_violation')
    return {**value, 'score': _unit_number(value['score'], 'score')}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('non-finite JSON number')


def read_jsonl(lines: Iterable[str]):
    """Strict JSONL input. Blank lines, duplicate keys and non-finite values fail."""
    for number, line in enumerate(lines, 1):
        try:
            if not line.strip():
                raise ValueError('blank lines are not records')
            value = json.loads(line, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            yield _record(value)
        except (ValueError, TypeError) as exc:
            # Do not echo record content, which may contain private identifiers.
            if isinstance(exc, json.JSONDecodeError):
                raise ValueError(f'line {number}: invalid JSON') from None
            raise ValueError(f'line {number}: {exc}') from None


def _counts():
    return {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}


def _metrics(counts):
    tp, fp, tn, fn = (counts[key] for key in ('tp', 'fp', 'tn', 'fn'))
    return {**counts, 'sample_count': tp + fp + tn + fn,
            'labelled_violation_count': tp + fn,
            'labelled_non_violation_count': tn + fp,
            'predicted_violation_count': tp + fp,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None}


def evaluate(records: Iterable[Mapping], *, threshold):
    """One supplied score per event. Validate everything before returning a report.

    The threshold is experimental input, not an approved operating policy. Metrics
    are empirical sample metrics; no calibration or deployment readiness is implied.
    """
    threshold = _unit_number(threshold, 'threshold')
    seen = set()
    grouped = {}
    total = _counts()
    for number, value in enumerate(records, 1):
        try:
            row = _record(value)
        except ValueError as exc:
            raise ValueError(f'record {number}: {exc}') from None
        if row['event_id'] in seen:
            raise ValueError(f'record {number}: duplicate event_id')
        seen.add(row['event_id'])
        counts = grouped.setdefault((row['model_version'], row['ppe_type']), _counts())
        predicted_positive = row['score'] >= threshold
        labelled_positive = row['label'] == 'violation'
        key = ('tp' if labelled_positive else 'fp') if predicted_positive else ('fn' if labelled_positive else 'tn')
        counts[key] += 1
        total[key] += 1
    if not seen:
        raise ValueError('at least one labelled event is required')
    return {'report_version': 1, 'mode': 'offline_labelled_evaluation',
            'automatic_confirmation_enabled': False,
            'threshold': threshold, 'positive_rule': 'score >= threshold',
            'overall': _metrics(total),
            'groups': [{'model_version': model, 'ppe_type': ppe, **_metrics(counts)}
                       for (model, ppe), counts in sorted(grouped.items())],
            'limitations': ['human_labels_are_supplied_not_independently_verified',
                           'scores_are_supplied_not_generated_or_calibrated_here',
                           'metrics_are_conditional_on_supplied_labelled_events',
                           'generated_events_only_cannot_measure_missing_event_detection_recall',
                           'sample_metrics_do_not_establish_deployment_readiness',
                           'no_minimum_sample_size_or_acceptance_policy_selected',
                           'no_database_review_or_automatic_activation_written']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='UTF-8 JSONL with one labelled event per line')
    parser.add_argument('--threshold', required=True, type=float, help='Explicit experimental threshold in [0,1]')
    args = parser.parse_args(argv)
    try:
        with args.dataset.open(encoding='utf-8') as source:
            result = evaluate(read_jsonl(source), threshold=args.threshold)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Evaluation failed: {exc}\n')
    json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False, indent=2)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
