"""Internal read-only preflight. No automatic writer or enable mechanism exists."""
import re
from backend.auto_decision.routes import PreviewRequest, inspect
from backend.db.connection import run_stats_transaction

_UNIMPLEMENTED_GATES = (
    'automatic_confirmation_disabled', 'validated_decision_policy_unavailable',
    'full_evidence_decision_validation_unavailable', 'automatic_review_writer_unavailable',
)


def evaluate_readiness(policy: PreviewRequest, *, expected_fingerprints: dict[str, str] | None = None):
    """Reinspect in one read snapshot; always blocked, never a write precondition.

    A matching fingerprint is NOT a lock, calibration, whole-event validation,
    byte-integrity check, or authority. Actual writer freshness needs a separate
    atomic transaction design. Experimental request scope is not validated policy.
    """
    policy = PreviewRequest.model_validate(policy.model_dump())
    expected = {} if expected_fingerprints is None else dict(expected_fingerprints)
    if set(expected) - set(policy.event_ids):
        raise ValueError('Expected fingerprints must refer only to requested events')
    if any(not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value) for value in expected.values()):
        raise ValueError('Expected fingerprints must be lowercase SHA256 identifiers')

    def read(conn):
        results=[]
        for event_id in policy.event_ids:
            diagnostic=inspect(conn,event_id,policy)
            reasons=set(diagnostic['blocking_reasons']) | set(_UNIMPLEMENTED_GATES)
            lifecycle=diagnostic['collection']
            if lifecycle is None:reasons.add('closed_lifecycle_provenance_required')
            elif lifecycle['phase'] != 'closed':reasons.add('continuous_event_not_closed')
            if event_id in expected and expected[event_id] != diagnostic['evidence_fingerprint']:
                reasons.add('diagnostic_evidence_changed')
            results.append({'event_id':event_id,'evaluated_at':diagnostic['evaluated_at'],
                'evidence_fingerprint':diagnostic['evidence_fingerprint'],
                'automatic_confirmation_allowed':False,'blocking_reasons':sorted(reasons)})
        return results

    return {'mode':'disabled_read_only_preflight','persisted':False,
        'automatic_confirmation_enabled':False,'items':run_stats_transaction(read),
        'limitations':['experimental_request_scope_is_not_validated_policy',
            'whole_event_evidence_decision_not_implemented','snapshot_not_commit_time_state',
            'fingerprint_is_not_authorization_or_content_integrity',
            'no_review_consent_retention_or_statistics_written']}
