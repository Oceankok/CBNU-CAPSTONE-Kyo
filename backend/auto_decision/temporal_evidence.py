"""Experimental full-episode replay of verified PPE observations.

This is not an operating policy. It never loads a detector, infers absent scores,
writes review decisions, or turns on automatic confirmation. The existing
verified-confidence code path accepts helmet scores only when a source model
actually provides a negative class; repository three-class checkpoints do not. Unknown/positive/gap observations
are retained as exclusion evidence, not silently discarded.
"""
from datetime import datetime, timezone
from math import isfinite

def valid_ppe_confidence(value):
    # Mirror the existing producer score domain without importing inference_worker:
    # API-only images intentionally contain backend only. This is NOT an operating
    # automatic-decision threshold; it validates the supplied source-score domain.
    return (isinstance(value,(int,float)) and not isinstance(value,bool)
            and isfinite(value) and .5 <= value <= 1)


def _time(value):
    if not isinstance(value,str):
        raise ValueError('timestamp must be a timezone-aware ISO string')
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result.astimezone(timezone.utc)
    except ValueError:
        raise ValueError('timestamp must be a timezone-aware ISO string') from None


def summarize_episode(*, event_id, model_version, ppe_type, start_at, end_at,
                      samples, max_sample_gap_seconds):
    """Replay one explicitly bounded event; gap tolerance must be supplied.

    Each sample is {timestamp, status, verified_ppe_confidence}, where status is
    violation/present/unknown. Confidence is accepted only for violation; all
    other statuses must use null. Times must be strictly increasing and within
    the event. The experimental score is the producer-compatible minimum only
    when the whole event is temporally covered by verified violation samples.
    It is not calibrated, and the gap setting is not a validation approval.
    """
    for name,value in (('event_id',event_id),('model_version',model_version),('ppe_type',ppe_type)):
        if not isinstance(value,str) or not value.strip() or value != value.strip():
            raise ValueError(name+' must be a nonblank exact string')
    try:
        gap_valid = (not isinstance(max_sample_gap_seconds,bool) and isinstance(max_sample_gap_seconds,(int,float))
                     and isfinite(max_sample_gap_seconds) and max_sample_gap_seconds>0)
    except (OverflowError, TypeError):
        gap_valid = False
    if not gap_valid:
        raise ValueError('explicit positive finite max_sample_gap_seconds required')
    start,end=_time(start_at),_time(end_at)
    if end<=start:raise ValueError('event duration must be positive')
    if not isinstance(samples,list) or not 1<=len(samples)<=100000:
        raise ValueError('samples must be a nonempty list of at most 100000 observations')
    rows=[];previous=None
    counts={'violation':0,'present':0,'unknown':0,'unverified_violation':0}
    scores=[]
    for index,row in enumerate(samples):
        if not isinstance(row,dict) or set(row)!={'timestamp','status','verified_ppe_confidence'}:
            raise ValueError(f'sample {index}: unexpected fields')
        instant=_time(row['timestamp'])
        if instant<start or instant>end or (previous is not None and instant<=previous):
            raise ValueError(f'sample {index}: time must be ordered, unique and within event')
        previous=instant
        status=row['status'];score=row['verified_ppe_confidence']
        if status not in ('violation','present','unknown'):
            raise ValueError(f'sample {index}: unknown status')
        if status!='violation' and score is not None:
            raise ValueError(f'sample {index}: non-violation confidence must be null')
        try:
            score_valid = score is None or valid_ppe_confidence(score)
        except (OverflowError, TypeError):
            score_valid = False
        if not score_valid:
            raise ValueError(f'sample {index}: invalid verified PPE confidence')
        counts[status]+=1
        verified=status=='violation' and score is not None
        if status=='violation' and not verified:counts['unverified_violation']+=1
        if verified:scores.append(float(score))
        rows.append((instant,verified))
    covered=0.0;gaps=[]
    if rows[0][0]>start:gaps.append({'start_at':start.isoformat(),'end_at':rows[0][0].isoformat(),'reason':'leading_unsampled'})
    for (left,left_valid),(right,right_valid) in zip(rows,rows[1:]):
        seconds=(right-left).total_seconds()
        if seconds<=max_sample_gap_seconds and left_valid and right_valid:
            covered+=seconds
        else:
            gaps.append({'start_at':left.isoformat(),'end_at':right.isoformat(),
                         'reason':'sample_gap' if seconds>max_sample_gap_seconds else 'non_verified_observation'})
    if rows[-1][0]<end:gaps.append({'start_at':rows[-1][0].isoformat(),'end_at':end.isoformat(),'reason':'trailing_unsampled'})
    reasons=[]
    if (end-start).total_seconds()<3:
        reasons.append('below_existing_three_second_event_gate')
    if ppe_type!='helmet':reasons.append('producer_verified_ppe_not_supported')
    if counts['present']:reasons.append('ppe_present_observation')
    if counts['unknown']:reasons.append('unknown_observation')
    if counts['unverified_violation']:reasons.append('unverified_violation_observation')
    if gaps or len(rows)<2:reasons.append('temporal_evidence_incomplete')
    if not scores:reasons.append('verified_confidence_missing')
    return {'version':1,'event_id':event_id,'model_version':model_version,'ppe_type':ppe_type,
            'mode':'experimental_episode_replay','automatic_confirmation_allowed':False,
            'score_kind':'minimum_verified_ppe_confidence',
            'experimental_score':min(scores) if scores and not reasons else None,
            'observed_verified_min':min(scores) if scores else None,
            'observed_verified_mean':sum(scores)/len(scores) if scores else None,
            'sample_count':len(rows),'counts':counts,'span_seconds':(end-start).total_seconds(),
            'verified_pair_coverage_seconds':covered,'excluded_ranges':gaps,
            'max_sample_gap_seconds':max_sample_gap_seconds,'blocking_reasons':reasons,
            'limitations':['not_a_calibrated_whole_event_probability',
                'temporal_interpolation_is_explicit_experimental_assumption',
                'source_samples_and_labels_not_independently_verified',
                'no_cross_camera_identity_or_reference_evidence_fusion',
                'not_connected_to_operational_review_or_live_collector']}
