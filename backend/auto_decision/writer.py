"""Dormant automatic-review transaction, not an activation API.

A future trusted in-process runtime must supply a validated whole-event adapter.
No request body, environment variable or shadow result can install one here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Callable
from uuid import uuid4

from backend.auto_decision.routes import PreviewRequest, inspect


@dataclass(frozen=True)
class ValidationProof:
    """Produced by a trusted validator, NEVER deserialized from a client request.

    The adapter must validate calibrated policy/model/whole-event evidence and
    integrity, not merely echo the diagnostic fingerprint. No adapter ships yet.
    """
    evidence_fingerprint: str
    validation_reference: str


@dataclass(frozen=True)
class Runtime:
    policy: PreviewRequest
    validator: Callable
    enabled: bool = False


def on_event_ready(conn, event_id, *, runtime=None):
    """Caller owns write lock+transaction. Runtime errors roll back, never approve.

    Three completion hooks call this with no runtime, so current operation stays
    OFF without DB reads or writes. Only a future reviewed integration can supply
    Runtime; the public preview/shadow APIs have no path to it.
    """
    if runtime is None or runtime.enabled is not True:
        return {'applied':False,'reason':'automatic_confirmation_disabled'}
    if not callable(runtime.validator):
        return {'applied':False,'reason':'validated_evidence_adapter_unavailable'}
    policy=PreviewRequest.model_validate(runtime.policy.model_dump())
    if event_id not in policy.event_ids:
        return {'applied':False,'reason':'event_out_of_scope'}
    # Read AFTER acquiring shared write lock; human review is never overwritten.
    diagnostic=inspect(conn,event_id,policy)
    if not diagnostic['threshold_candidate']:
        return {'applied':False,'reason':'diagnostic_not_eligible'}
    lifecycle=diagnostic['collection']
    whole=diagnostic['whole_event_evidence']
    if (not lifecycle or lifecycle['phase']!='closed' or not whole.get('available')
        or not whole.get('observation_metadata_valid') or whole.get('gap_seconds') != 0
        or whole.get('unaccounted_seconds') != 0 or not whole.get('cameras')
        or any(not c['manifest_coverage'].get('complete') for c in whole['cameras'])):
        return {'applied':False,'reason':'whole_event_evidence_incomplete'}
    # Adapter is a local, side-effect-free verifier under this transaction.
    proof=runtime.validator(conn,event_id,policy,diagnostic)
    if (type(proof) is not ValidationProof
        or proof.evidence_fingerprint != diagnostic['evidence_fingerprint']
        or not isinstance(proof.validation_reference,str)
        or not proof.validation_reference.strip() or len(proof.validation_reference)>200):
        return {'applied':False,'reason':'validated_whole_event_proof_unavailable'}
    from backend.media.service import validate_review
    review={'review_id':'AUTO_'+uuid4().hex,'event_id':event_id,'reviewer_id':None,
        'review_result':'confirmed','review_reason_code':'automatic_policy_confirmed',
        'review_time':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'review_comment':json.dumps({'source':'automatic_decision_v1',
            'policy':policy.model_dump(exclude={'event_ids'}),
            'validation_reference':proof.validation_reference,
            'evidence_fingerprint':proof.evidence_fingerprint,
            'observed_confidence':diagnostic['observed_confidence']},sort_keys=True),
        'confirmed_violation':1,'second_review_needed':1}
    # Same finalization, media, expiration and merge safety as manual review.
    # Consent/retention are not created, extended, approved or bypassed.
    validate_review(conn,review)
    if conn.execute('SELECT 1 FROM event_review WHERE event_id=?',(event_id,)).fetchone():
        return {'applied':False,'reason':'already_reviewed'}
    changed=conn.execute("UPDATE candidate_event SET event_status='confirmed' WHERE event_id=? AND event_status='pending'",(event_id,))
    if changed.rowcount != 1:
        return {'applied':False,'reason':'not_pending'}
    columns=tuple(review)
    conn.execute('INSERT INTO event_review ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',tuple(review.values()))
    return {'applied':True,'review_id':review['review_id'],'administrator_review_available':True}


def attempt(event_id, *, runtime=None):
    if runtime is None or runtime.enabled is not True:
        return {'applied':False,'reason':'automatic_confirmation_disabled'}
    from backend.db.connection import connect, begin_write
    with connect() as conn:
        if getattr(conn,'dialect','sqlite')=='postgresql':
            conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        begin_write(conn)
        return on_event_ready(conn,event_id,runtime=runtime)
