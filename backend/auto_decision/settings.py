"""Versioned administrator policy with separate model and continuous-rule paths."""
from datetime import datetime, timezone
import json
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.auth.service import require_admin
from backend.auto_decision.routes import PreviewRequest
from backend.auto_decision.adapter import DiagnosticOnlyAdapter
from backend.auto_decision.rules import Criteria, METHOD as RULE_METHOD
from backend.db.connection import run_stats_transaction
from backend.zones.policy import ALLOWED_PPE

router = APIRouter(prefix='/api/auto-decisions/settings', tags=['auto-decision settings'],
                   dependencies=[Depends(require_admin)])


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['off', 'shadow', 'enforce'] = 'off'
    decision_method: Literal['validated_model', 'continuous_rules_v1'] = 'validated_model'
    rule_criteria: Criteria | None = None
    threshold: float = Field(default=0.9, ge=0, le=1, allow_inf_nan=False, strict=True)
    model_versions: list[str] = Field(default_factory=list, max_length=50)
    ppe_types: list[str] = Field(default_factory=list, max_length=12)
    camera_ids: list[str] = Field(default_factory=list, max_length=100)
    zone_names: list[str] = Field(default_factory=list, max_length=50)
    validation_reference: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def validate_scope(self):
        for values in (self.model_versions, self.ppe_types, self.camera_ids, self.zone_names):
            if len(values) != len(set(values)) or any(not v.strip() or v != v.strip() or len(v)>200 for v in values):
                raise ValueError('Scope identifiers must be unique nonblank exact strings')
        if set(self.ppe_types)-set(ALLOWED_PPE):
            raise ValueError('Unknown PPE type')
        if self.validation_reference is not None and self.validation_reference != self.validation_reference.strip():
            raise ValueError('Validation reference must be an exact identifier')
        if self.mode != 'off' and not (self.model_versions and self.ppe_types and self.camera_ids):
            raise ValueError('Non-OFF mode requires explicit model, PPE and camera scope')
        if self.decision_method == RULE_METHOD:
            if self.mode != 'off' and self.rule_criteria is None:
                raise ValueError('Rule mode requires explicit observation criteria')
            if set(self.ppe_types) - {'helmet', 'vest'}:
                raise ValueError('Continuous rules support helmet and vest only')
        elif self.rule_criteria is not None:
            raise ValueError('Rule criteria require continuous_rules_v1')
        return self


class SettingsUpdate(Policy):
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def validate_reason(self):
        if not self.reason.strip():
            raise ValueError('A change reason is required')
        return self


def trusted_adapter():
    """Integration point for reviewed server code, not a plugin/import-name setting.

    Adapter requirements: policy_blockers(policy) returns current missing gates;
    validate(conn,event_id,preview_policy,diagnostic) returns writer.ValidationProof
    only after approved model/hash/score semantics/whole-event evidence checks.
    Validation reference text alone is never sufficient. The default adapter is
    diagnostic-only and always returns no validation proof.
    """
    return DiagnosticOnlyAdapter()


def activation_blockers(policy):
    reasons = []
    if not (policy.model_versions and policy.ppe_types and policy.camera_ids):
        reasons.append('explicit_scope_required')
    if not policy.validation_reference:
        reasons.append('approved_validation_reference_required')
    if policy.decision_method == RULE_METHOD:
        if policy.rule_criteria is None:
            reasons.append('explicit_rule_criteria_required')
        return sorted(set(reasons))
    adapter = trusted_adapter()
    if adapter is None:
        reasons.append('validated_model_adapter_unavailable')
    else:
        reasons.extend(adapter.policy_blockers(policy))
    return sorted(set(reasons))


def load(conn):
    row = conn.execute('SELECT * FROM auto_decision_settings WHERE singleton=1').fetchone()
    return (Policy.model_validate_json(row['policy_json']), row['version'], dict(row)) if row else (Policy(), 0, None)


def enabled_for_event(conn, event):
    """Configured activation/scope, not a claim that this event is eligible."""
    collection=conn.execute('SELECT start_payload FROM processing_event_lifecycle WHERE event_id=?',(event['event_id'],)).fetchone()
    if collection and json.loads(collection['start_payload']).get('evidence_mode')=='manual_review':
        return False
    policy, _, _ = load(conn)
    if policy.mode != 'enforce' or activation_blockers(policy):
        return False
    if (event['camera_id'] not in policy.camera_ids or event['model_version'] not in policy.model_versions
            or event['ppe_type'] not in policy.ppe_types):
        return False
    if policy.zone_names:
        zone=conn.execute('SELECT zone_name FROM event_zone_snapshot WHERE event_id=?',(event['event_id'],)).fetchone()
        if zone is None or zone['zone_name'] not in policy.zone_names:
            return False
    return True


def representation(policy, version, row=None):
    blockers = activation_blockers(policy)
    return {**policy.model_dump(), 'version':version,
            'automatic_confirmation_enabled':policy.mode=='enforce' and not blockers,
            'activation_blockers':blockers,
            'updated_by':row['updated_by'] if row else None,
            'updated_at':row['updated_at'] if row else None,
            'scope_semantics':{'camera_ids':'empty_matches_none', 'zone_names':'empty_adds_no_zone_restriction'},
            'score_semantics':('rule_conditions_not_a_probability' if policy.decision_method == RULE_METHOD
                               else 'validated_violation_score_required_not_person_detection_score')}


@router.get('')
def get_settings():
    return run_stats_transaction(lambda conn: representation(*load(conn)))


@router.put('')
def update_settings(body: SettingsUpdate, actor: dict = Depends(require_admin)):
    policy = Policy.model_validate(body.model_dump(exclude={'expected_version','reason'}))
    def operation(conn):
        previous, version, _ = load(conn)
        if version != body.expected_version:
            raise HTTPException(409, 'auto_decision_settings_changed')
        if (previous.decision_method == RULE_METHOD and policy.mode != 'off'
                and 'decision_method' not in body.model_fields_set):
            raise HTTPException(422, 'explicit_decision_method_required')
        for camera in policy.camera_ids:
            if not conn.execute('SELECT 1 FROM camera_info WHERE camera_id=?',(camera,)).fetchone():
                raise HTTPException(422,'unknown_camera_scope')
        for zone in policy.zone_names:
            if not conn.execute('SELECT 1 FROM zone_rule WHERE zone_name=?',(zone,)).fetchone():
                raise HTTPException(422,'unknown_zone_scope')
        if policy.mode == 'enforce':
            blockers = activation_blockers(policy)
            if blockers:
                raise HTTPException(409, {'code':'automatic_confirmation_not_ready','blocking_reasons':blockers})
        now=datetime.now(timezone.utc).isoformat()
        new_version=version+1
        encoded=policy.model_dump_json()
        conn.execute('''INSERT INTO auto_decision_settings(singleton,version,policy_json,updated_by,updated_at)
            VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET version=excluded.version,
            policy_json=excluded.policy_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (new_version,encoded,actor['user_id'],now))
        conn.execute('INSERT INTO auto_decision_settings_history(version,policy_json,changed_by,changed_at,reason) VALUES(?,?,?,?,?)',
            (new_version,encoded,actor['user_id'],now,body.reason.strip()))
        return representation(policy,new_version,{'updated_by':actor['user_id'],'updated_at':now})
    return run_stats_transaction(operation,write=True)


@router.get('/history')
def history(limit: int=Query(default=20,ge=1,le=100), offset: int=Query(default=0,ge=0,le=10000)):
    def read(conn):
        rows=conn.execute('SELECT * FROM auto_decision_settings_history ORDER BY version DESC LIMIT ? OFFSET ?',(limit,offset)).fetchall()
        return {'items':[{'version':row['version'],'changed_by':row['changed_by'],
                         'changed_at':row['changed_at'],'reason':row['reason'],
                         'policy':json.loads(row['policy_json'])} for row in rows], 'limit':limit,'offset':offset}
    return run_stats_transaction(read)


class SettingsPreview(Policy):
    event_ids: list[str] = Field(min_length=1, max_length=20)


def preview_policy(conn, event_id, policy, version_label):
    from backend.auto_decision.routes import inspect
    preview = PreviewRequest(event_ids=[event_id],threshold=policy.threshold,
        model_versions=policy.model_versions,ppe_types=policy.ppe_types,policy_label=version_label)
    diagnostic = inspect(conn,event_id,preview)
    event = conn.execute('SELECT camera_id FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
    reasons = list(diagnostic['blocking_reasons'])
    if event['camera_id'] not in policy.camera_ids:
        reasons.append('camera_out_of_scope')
    if policy.zone_names:
        zone=conn.execute('SELECT zone_name FROM event_zone_snapshot WHERE event_id=?',(event_id,)).fetchone()
        if zone is None or zone['zone_name'] not in policy.zone_names:
            reasons.append('zone_out_of_scope')
    if policy.decision_method == RULE_METHOD:
        from backend.auto_decision.rules import evaluate
        rule = evaluate(conn,event_id,policy.rule_criteria,{**diagnostic,'blocking_reasons':reasons})
        digest=hashlib.sha256(json.dumps({'diagnostic':diagnostic['evidence_fingerprint'],
            'policy':policy.model_dump(),'rule_evaluation':rule},sort_keys=True).encode()).hexdigest()
        return {**diagnostic,'blocking_reasons':rule['blocking_reasons'],
                'structural_eligible':rule['eligible'],'threshold_candidate':rule['eligible'],
                'evidence_fingerprint':digest,'scope_applied':True,'rule_evaluation':rule,
                'decision_method':RULE_METHOD,'score_semantics':rule['score_semantics']}
    # Current diagnostic-only model implementation, not a future trusted scorer.
    # A reviewed scorer must replace this diagnostic path together with
    # trusted_adapter(), so preview/shadow and enforce share score semantics.
    adapter_diagnostic=DiagnosticOnlyAdapter().diagnose(diagnostic)
    digest=hashlib.sha256(json.dumps({'diagnostic':diagnostic['evidence_fingerprint'],
        'camera_ids':policy.camera_ids,'zone_names':policy.zone_names,
        'trusted_score_adapter':adapter_diagnostic},sort_keys=True).encode()).hexdigest()
    return {**diagnostic,'blocking_reasons':sorted(set(reasons)),
            'structural_eligible':not reasons,
            'threshold_candidate':not reasons and diagnostic['threshold_candidate'],
            'evidence_fingerprint':digest,'scope_applied':True,
            'trusted_score_adapter':adapter_diagnostic}


@router.post('/preview')
def settings_preview(body: SettingsPreview):
    # The same explicit camera/model/PPE/historical-zone restrictions as hooks.
    # Body is an unsaved experiment, not a policy approval or activation request.
    policy=Policy.model_validate(body.model_dump(exclude={'event_ids'}))
    if len(body.event_ids)!=len(set(body.event_ids)):
        raise HTTPException(422,'duplicate_event_ids')
    if not policy.model_versions or not policy.ppe_types:
        raise HTTPException(422,'explicit_model_and_ppe_scope_required')
    return {'automatic_confirmation_enabled':False,'persisted':False,
            'mode':'unsaved_scoped_preview','scope_applied':True,
            'items':run_stats_transaction(lambda conn:[preview_policy(conn,event_id,policy,'administrator-scoped-preview')
                for event_id in body.event_ids])}


def process_ready(conn, event_id, *, capture_shadow_only=False):
    """Same caller-owned transaction and shared write lock as the review writer.

    No new polling worker, no automatic backfill. Hooks revisit an event when its
    lifecycle/media complete; future settings changes affect future hook calls.
    The post-commit capture hook also evaluates explicitly selected rule policies.
    """
    policy, version, row = load(conn)
    if policy.mode == 'off':
        return {'applied':False,'reason':'automatic_confirmation_disabled'}
    if capture_shadow_only and policy.mode != 'shadow' and policy.decision_method != RULE_METHOD:
        return {'applied':False,'reason':'capture_shadow_only'}
    event=conn.execute('SELECT * FROM candidate_event WHERE event_id=?',(event_id,)).fetchone()
    if event is None:
        return {'applied':False,'reason':'event_not_found'}
    if (event['camera_id'] not in policy.camera_ids or event['model_version'] not in policy.model_versions
        or event['ppe_type'] not in policy.ppe_types):
        return {'applied':False,'reason':'event_out_of_scope'}
    if policy.zone_names:
        # Historical event zone snapshot, never the camera's mutable current zone.
        zone=conn.execute('SELECT zone_name FROM event_zone_snapshot WHERE event_id=?',(event_id,)).fetchone()
        if zone is None or zone['zone_name'] not in policy.zone_names:
            return {'applied':False,'reason':'event_out_of_scope'}
    preview=PreviewRequest(event_ids=[event_id], threshold=policy.threshold,
        model_versions=policy.model_versions,ppe_types=policy.ppe_types,policy_label=f'settings-v{version}')
    if policy.mode == 'shadow':
        if capture_shadow_only:
            if event['event_status'] != 'pending':
                return {'applied':False,'reason':'event_not_pending'}
            # Rule shadow records exclusions too. A terminal incomplete report
            # changes evidence even though it can never authorize a review.
            # Keep the historical model-diagnostic finalizable-only behavior.
            if policy.decision_method != RULE_METHOD:
                from backend.processing.lifecycle import require_finalizable
                try:
                    require_finalizable(conn,event_id)
                except ValueError as exc:
                    if str(exc) not in {'continuous_event_not_closed', 'continuous_evidence_incomplete',
                                        'event_media_deleted_or_expired'}:
                        raise
                    return {'applied':False,'reason':str(exc)}
        from backend.auto_decision.shadow import record_policy_shadow
        diagnostic=preview_policy(conn,event_id,policy,f'settings-v{version}')
        shadow_policy=policy if policy.decision_method == RULE_METHOD else preview
        record_policy_shadow(conn,event_id,version,row['updated_by'],shadow_policy,diagnostic)
        return {'applied':False,'reason':'shadow_only','threshold_candidate':diagnostic['threshold_candidate']}
    blockers=activation_blockers(policy)
    if blockers:
        return {'applied':False,'reason':'automatic_confirmation_not_ready','blocking_reasons':blockers}
    if policy.decision_method == RULE_METHOD:
        diagnostic=preview_policy(conn,event_id,policy,f'settings-v{version}')
        if not diagnostic['rule_evaluation']['eligible']:
            return {'applied':False,'reason':'rule_conditions_not_met',
                    'blocking_reasons':diagnostic['blocking_reasons']}
        from backend.auto_decision.writer import confirm
        return confirm(conn,event_id,{
            'source':'automatic_continuous_rules_v1','settings_version':version,
            'policy_configured_by':row['updated_by'],'policy':policy.model_dump(),
            'model_version':event['model_version'],
            'evidence_fingerprint':diagnostic['evidence_fingerprint'],
            'rule_evaluation':diagnostic['rule_evaluation'],
        })
    from backend.event_relations.service import automatic_tag
    if automatic_tag(conn,event_id)['candidate_count']:
        return {'applied':False,'reason':'suspected_duplicate_requires_review'}
    from backend.auto_decision.writer import Runtime,on_event_ready
    return on_event_ready(conn,event_id,runtime=Runtime(preview,trusted_adapter().validate,True))
