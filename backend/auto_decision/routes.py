"""Read-only threshold diagnostics; never an automatic review writer."""
from datetime import datetime, timezone
import hashlib
import json
import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.auth.service import require_admin
from backend.auto_decision.evidence import inventory
from backend.db.connection import run_stats_transaction
from backend.media.paths import owned_path
from backend.media.retention_access import expired
from backend.processing.lifecycle import require_finalizable
from backend.zones.policy import ALLOWED_PPE

router = APIRouter(prefix='/api/auto-decisions', tags=['auto-decision preview'],
                   dependencies=[Depends(require_admin)])


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ids: list[str] = Field(min_length=1, max_length=20)
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    model_versions: list[str] = Field(min_length=1, max_length=50)
    ppe_types: list[str] = Field(min_length=1, max_length=12)
    policy_label: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_lists(self):
        for values in (self.event_ids, self.model_versions, self.ppe_types):
            if len(values) != len(set(values)) or any(not v.strip() or v != v.strip() or len(v) > 200 for v in values):
                raise ValueError('Lists must contain distinct nonblank exact identifiers')
        if set(self.ppe_types) - set(ALLOWED_PPE):
            raise ValueError('Unknown PPE type')
        return self


def inspect(conn, event_id, policy):
    row = conn.execute('SELECT * FROM candidate_event WHERE event_id=?', (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'event_not_found')
    event = dict(row)
    mode = conn.execute('SELECT review_only FROM processing_review_only_event WHERE event_id=?', (event_id,)).fetchone()
    lifecycle = conn.execute('SELECT phase,revision,evidence_revision FROM processing_event_lifecycle WHERE event_id=?', (event_id,)).fetchone()
    reviewed = conn.execute('SELECT 1 FROM event_review WHERE event_id=?', (event_id,)).fetchone() is not None
    merged = conn.execute('SELECT 1 FROM event_merge WHERE source_event_id=? OR target_event_id=?', (event_id,event_id)).fetchone() is not None
    media = [dict(r) for r in conn.execute('SELECT * FROM event_media WHERE event_id=? ORDER BY media_id', (event_id,))]
    requests = [dict(r) for r in conn.execute('SELECT request_id,status FROM event_clip_request WHERE event_id=? ORDER BY request_id', (event_id,))]
    reasons = []
    if event['event_status'] != 'pending' or reviewed: reasons.append('already_reviewed_or_not_pending')
    if mode is None: reasons.append('submission_provenance_missing')
    elif mode['review_only']: reasons.append('review_only')
    confidence = event['ai_confidence']
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        reasons.append('verified_confidence_missing'); confidence = None
    if mode is None or mode['review_only']: confidence = None
    if event['model_version'] not in policy.model_versions: reasons.append('model_out_of_scope')
    if event['ppe_type'] not in policy.ppe_types: reasons.append('ppe_out_of_scope')
    if merged: reasons.append('merged_event_requires_review')
    retention = conn.execute('SELECT decision,retain_until FROM event_retention WHERE event_id=?', (event_id,)).fetchone()
    if expired(conn,event_id) or (retention and retention['decision']=='delete'): reasons.append('media_deleted_or_expired')
    try:
        start = datetime.fromisoformat(event['timestamp_start'].replace('Z','+00:00'))
        end = datetime.fromisoformat(event['timestamp_end'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end < start or end > datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError,TypeError,AttributeError): reasons.append('observation_interval_unverified')
    if lifecycle:
        try: require_finalizable(conn,event_id)
        except ValueError as exc: reasons.append(str(exc))
    def usable(item):
        try:
            return item['status']=='ready' and item['redaction_status']=='complete' and owned_path(item['storage_path']).is_file()
        except (ValueError,OSError,TypeError): return False
    usability = {m['media_id']: usable(m) for m in media}
    if not any(m['kind']=='video' and m['role']=='clip' and usability[m['media_id']] for m in media):
        reasons.append('primary_redacted_video_missing')
    if any(not usability[m['media_id']] for m in media) or any(r['status']!='received' for r in requests):
        reasons.append('evidence_incomplete')
    if any(not any(m.get('request_id')==r['request_id'] and usability[m['media_id']] for m in media) for r in requests):
        reasons.append('requested_evidence_missing')
    whole_event = inventory(conn, event)
    reasons = sorted(set(reasons))
    # The fingerprint identifies this diagnostic input, not a persisted revision or authorization token.
    evidence = {'event':event,'mode':dict(mode) if mode else None,'lifecycle':dict(lifecycle) if lifecycle else None,
                'reviewed':reviewed,'merged':merged,'media':media,'requests':requests,'retention':dict(retention) if retention else None,
                'whole_event_evidence':whole_event,'file_usable':usability,'blocking_reasons':reasons,'policy':policy.model_dump(exclude={'event_ids'})}
    digest = hashlib.sha256(json.dumps(evidence,sort_keys=True,default=str).encode()).hexdigest()
    return {'event_id':event_id,'model_version':event['model_version'],'ppe_type':event['ppe_type'],
            'observed_confidence':confidence,'event_status':event['event_status'],
            'evaluated_at':datetime.now(timezone.utc).isoformat(),
            'collection':dict(lifecycle) if lifecycle else None,'whole_event_evidence':whole_event,
            'structural_eligible':not reasons,'threshold_candidate':not reasons and confidence is not None and confidence >= policy.threshold,
            'blocking_reasons':reasons,'evidence_fingerprint':digest,
            'automatic_confirmation_allowed':False}


@router.post('/preview')
def preview(body: PreviewRequest):
    items = run_stats_transaction(lambda conn: [inspect(conn,ident,body) for ident in body.event_ids])
    return {'mode':'read_only_preview','automatic_confirmation_enabled':False,'persisted':False,
            'policy':body.model_dump(exclude={'event_ids'}),'items':items,
            'limitations':['not_a_calibrated_probability','model_allowlist_is_request_scope_not_validation',
                           'special_case_screening_not_complete','no_shadow_history_or_automatic_review_written']}
