"""Offline postprocessing evidence from existing positive-PPE assessments.

An absent detected box is NOT a model prediction of non-compliance. This module
measures its persistence only, without enabling automatic review or creating a
confidence value. It accepts one track/PPE episode after assess_ppe; identities
must never be merged across cameras or tracking resets.
"""
from math import isfinite


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value))
    except (OverflowError, TypeError):
        return False


def summarize_absence(*, camera_id, session_id, model_version, tracking_id, ppe_type, start_time, end_time,
                      observations, max_sample_gap_seconds):
    for field,value in (('camera_id',camera_id),('session_id',session_id),('model_version',model_version),('tracking_id',tracking_id)):
        if not isinstance(value,str) or not value.strip() or value != value.strip():
            raise ValueError(field)
    if ppe_type not in ('helmet', 'vest'):
        raise ValueError('ppe_type')
    if not _number(start_time) or not _number(end_time) or end_time <= start_time:
        raise ValueError('episode interval')
    if not _number(max_sample_gap_seconds) or max_sample_gap_seconds <= 0:
        raise ValueError('explicit sample gap required')
    if not isinstance(observations, list) or not 1 <= len(observations) <= 100000:
        raise ValueError('observations')
    statuses = {'present': 0, 'absent': 0, 'unknown': 0}
    previous = None
    gap_count = 0
    gap_seconds = 0.0
    absence_pair_seconds = 0.0
    expected = {ppe_type: 'present', 'suspected_no_'+ppe_type: 'absent', 'unknown': 'unknown'}
    for row in observations:
        if not isinstance(row, dict) or set(row) != {'camera_id','session_id','model_version','tracking_id','ppe_type','timestamp','status'}:
            raise ValueError('observation fields')
        if any(row[field] != value for field,value in (('camera_id',camera_id),('session_id',session_id),('model_version',model_version),('tracking_id',tracking_id),('ppe_type',ppe_type))):
            raise ValueError('mixed camera/session/model/track/PPE scope')
        timestamp = row['timestamp']
        if not _number(timestamp) or not start_time <= timestamp <= end_time:
            raise ValueError('observation timestamp')
        if not isinstance(row['status'], str) or row['status'] not in expected:
            raise ValueError('observation status')
        status = expected[row['status']]
        statuses[status] += 1
        if previous is not None:
            if timestamp <= previous[0]:
                raise ValueError('observation ordering')
            delta = timestamp-previous[0]
            if delta > max_sample_gap_seconds:
                gap_count += 1
                gap_seconds += delta
            elif previous[1] == status == 'absent':
                absence_pair_seconds += delta
        previous = (timestamp, status)
    leading = observations[0]['timestamp']-start_time
    trailing = end_time-observations[-1]['timestamp']
    reasons = []
    if end_time-start_time < 3: reasons.append('below_existing_three_second_gate')
    if leading or trailing: reasons.append('episode_edges_not_observed')
    if gap_count: reasons.append('sample_gaps')
    if statuses['unknown']: reasons.append('unknown_observations')
    if len(observations) < 2: reasons.append('insufficient_observations')
    observable = statuses['present']+statuses['absent']
    return {'version':1,'mode':'offline_positive_ppe_absence_metric',
            'camera_id':camera_id,'session_id':session_id,'model_version':model_version,
            'tracking_id':tracking_id,'ppe_type':ppe_type,'sample_count':len(observations),
            'counts':statuses,'span_seconds':end_time-start_time,
            'absence_sample_ratio':statuses['absent']/observable if observable else None,
            'unknown_sample_ratio':statuses['unknown']/len(observations),
            'absence_pair_seconds':absence_pair_seconds,'gap_count':gap_count,'gap_seconds':gap_seconds,
            'leading_unobserved_seconds':leading,'trailing_unobserved_seconds':trailing,
            'structurally_complete_for_calibration':not reasons,'blocking_reasons':reasons,
            'decision_metric':statuses['absent']/len(observations) if not reasons else None,
            'metric_kind':'observable_positive_box_absence_fraction',
            'verified_ppe_confidence':None,'automatic_confirmation_allowed':False,
            'limitations':['absence_is_not_a_negative_model_class',
                'assess_ppe_geometry_is_heuristic_not_pose_or_occlusion_verification',
                'source_status_and_identity_not_independently_verified',
                'sample_ratio_depends_on_sampling_and_is_not_violation_probability',
                'suspected_only_episode_selection_can_make_absence_ratio_tautological',
                'requires_labelled_ppe_specific_validation_and_explicit_policy',
                'no_live_runner_or_review_writer_connection']}
