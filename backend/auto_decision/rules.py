"""Explicit opt-in rules over continuous observations, never a PPE probability.

The producer's absence heuristic can be wrong. These rules check persistence and
evidence completeness; shadow comparison with labelled footage remains necessary.
"""
from pydantic import BaseModel, ConfigDict, Field


METHOD = 'continuous_rules_v1'


class Criteria(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # No operating defaults: the administrator must choose both thresholds.
    minimum_observed_seconds: float = Field(ge=3, le=3600, strict=True, allow_inf_nan=False)
    minimum_frame_samples: int = Field(ge=2, le=100000, strict=True)


def evaluate(conn, event_id, criteria, diagnostic):
    """Read within the same transaction as the saved policy and review writer."""
    event = conn.execute('SELECT * FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()
    # Only this explicitly selected rule path accepts absence-only observations.
    # Keep their original review_only provenance and null public confidence.
    reasons = set(diagnostic['blocking_reasons']) - {'review_only', 'verified_confidence_missing'}
    whole = diagnostic['whole_event_evidence']
    if criteria is None:
        reasons.add('explicit_rule_criteria_required')
    if event['ppe_type'] not in {'helmet', 'vest'}:
        reasons.add('rule_ppe_not_supported')
    if not event['tracking_id'] or event['person_detected'] != 1 or event['ppe_detected'] != 0:
        reasons.add('tracked_absence_observation_required')
    if not whole.get('available') or whole.get('phase') != 'closed':
        reasons.add('closed_continuous_event_required')
    if (not whole.get('observation_metadata_valid') or whole.get('gap_seconds') != 0
            or whole.get('gap_count') != 0 or whole.get('unaccounted_seconds') is None
            or whole['unaccounted_seconds'] > 1e-6):
        reasons.add('continuous_observation_incomplete')
    if (not whole.get('cameras') or any(not camera['manifest_coverage'].get('complete')
                                      for camera in whole.get('cameras', []))):
        reasons.add('whole_event_evidence_incomplete')
    if whole.get('close_reason') not in {'ppe_present', 'track_departed', 'primary_source_ended'}:
        reasons.add('abnormal_event_close_requires_review')
    if criteria is not None:
        if (whole.get('observed_seconds') is None
                or whole['observed_seconds'] < criteria.minimum_observed_seconds):
            reasons.add('minimum_observed_seconds_not_met')
        if event['frame_sample_count'] < criteria.minimum_frame_samples:
            reasons.add('minimum_frame_samples_not_met')
    from backend.event_relations.service import automatic_tag
    relation = automatic_tag(conn, event_id)
    if relation['candidate_count'] or relation['saved_record_count']:
        reasons.add('suspected_duplicate_requires_review')
    return {
        'method': METHOD, 'eligible': not reasons, 'blocking_reasons': sorted(reasons),
        'criteria': criteria.model_dump() if criteria else None,
        'observed_seconds': whole.get('observed_seconds'),
        'frame_sample_count': event['frame_sample_count'],
        'gap_count': whole.get('gap_count'), 'gap_seconds': whole.get('gap_seconds'),
        'close_reason': whole.get('close_reason'), 'score': None,
        'score_semantics': 'rule_conditions_not_a_probability',
        'limitations': ['producer_absence_heuristic_not_independent_ppe_verification',
                       'reference_video_completeness_not_cross_camera_classification'],
    }
