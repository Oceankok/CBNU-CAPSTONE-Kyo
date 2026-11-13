"""Explicit administrator-requested shadow diagnostics, never review decisions."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field

from backend.auth.service import require_admin
from backend.auto_decision.routes import PreviewRequest, inspect
from backend.db.connection import run_stats_transaction

router = APIRouter(prefix='/api/auto-decisions/shadow', tags=['auto-decision shadow'],
                   dependencies=[Depends(require_admin)])


class ShadowRequest(PreviewRequest):
    request_key: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _run(conn, row):
    items = [json.loads(item['result_json']) for item in conn.execute(
        'SELECT result_json FROM auto_decision_shadow_item WHERE run_id=? ORDER BY ordinal', (row['run_id'],))]
    return {'run_id': row['run_id'], 'actor_id': row['actor_id'], 'request_key': row['request_key'],
            'created_at': row['created_at'], 'policy': json.loads(row['policy_json']),
            'mode': 'persisted_shadow_diagnostic', 'persisted': True, 'automatic_confirmation_enabled': False,
            'item_count': row['item_count'], 'removed_item_count': row['item_count'] - len(items),
            'items': items,
            'limitations': ['historical_diagnostic_not_current_state', 'transaction_snapshot_not_commit_time_state', 'not_a_calibrated_probability',
                           'model_allowlist_is_request_scope_not_validation', 'special_case_screening_not_complete',
                           'no_automatic_review_written']}


@router.post('', status_code=201)
def store_shadow(body: ShadowRequest, response: Response, actor: dict = Depends(require_admin)):
    # No client-provided preview result/fingerprint is trusted. Recompute in the write snapshot.
    policy = PreviewRequest.model_validate(body.model_dump(exclude={'request_key'}))
    request_digest = hashlib.sha256(_json(policy.model_dump()).encode()).hexdigest()
    policy_json = _json(policy.model_dump(exclude={'event_ids'}))
    run_id = str(uuid4())

    def operation(conn):
        inserted = conn.execute('''INSERT INTO auto_decision_shadow_run
            (run_id,actor_id,request_key,request_digest,policy_json,created_at,item_count)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(actor_id,request_key) DO NOTHING RETURNING run_id''',
            (run_id, actor['user_id'], body.request_key, request_digest, policy_json,
             datetime.now(timezone.utc).isoformat(), len(policy.event_ids))).fetchone()
        row = conn.execute('SELECT * FROM auto_decision_shadow_run WHERE actor_id=? AND request_key=?',
                           (actor['user_id'], body.request_key)).fetchone()
        if row is None:
            raise RuntimeError('shadow_request_gate_missing')
        if row['request_digest'] != request_digest:
            raise HTTPException(409, 'shadow_request_key_conflict')
        created = inserted is not None
        if created:
            # Any missing event or diagnostic failure rolls back the header and every item.
            for ordinal, event_id in enumerate(policy.event_ids):
                result = inspect(conn, event_id, policy)
                conn.execute('''INSERT INTO auto_decision_shadow_item(run_id,event_id,ordinal,result_json)
                    VALUES(?,?,?,?)''', (run_id, event_id, ordinal, _json(result)))
        return {**_run(conn, row), 'created': created}

    result = run_stats_transaction(operation, write=True)
    response.status_code = 201 if result['created'] else 200
    return result


@router.get('')
def list_shadow(event_id: str = Query(min_length=1, max_length=200),
                limit: int = Query(default=20, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=10000)):
    def operation(conn):
        if conn.execute('SELECT 1 FROM candidate_event WHERE event_id=?', (event_id,)).fetchone() is None:
            raise HTTPException(404, 'event_not_found')
        rows = conn.execute('''SELECT r.*,i.result_json FROM auto_decision_shadow_run r
            JOIN auto_decision_shadow_item i ON i.run_id=r.run_id WHERE i.event_id=?
            ORDER BY r.created_at DESC,r.run_id DESC LIMIT ? OFFSET ?''', (event_id, limit, offset)).fetchall()
        return {'event_id': event_id, 'limit': limit, 'offset': offset,
                'mode': 'persisted_shadow_diagnostic', 'persisted': True,
                'automatic_confirmation_enabled': False,
                'limitations': ['historical_diagnostic_not_current_state', 'transaction_snapshot_not_commit_time_state',
                                'no_automatic_review_written'], 'items': [
                    {'run_id': row['run_id'], 'actor_id': row['actor_id'], 'created_at': row['created_at'],
                     'policy': json.loads(row['policy_json']), 'result': json.loads(row['result_json'])}
                    for row in rows]}
    return run_stats_transaction(operation)


@router.get('/{run_id}')
def get_shadow(run_id: str):
    def operation(conn):
        row = conn.execute('SELECT * FROM auto_decision_shadow_run WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'shadow_run_not_found')
        return _run(conn, row)
    return run_stats_transaction(operation)


def record_policy_shadow(conn, event_id, version, actor_id, policy, diagnostic):
    """Completion-hook shadow in caller transaction, once per policy/evidence.

    actor_id is the administrator who configured this policy, not a reviewer.
    This never creates event_review and cannot install an enforce runtime.
    """
    fingerprint = diagnostic['evidence_fingerprint']
    request_key = 'policy_' + hashlib.sha256(f'{version}:{event_id}:{fingerprint}'.encode()).hexdigest()
    encoded_policy = _json(policy.model_dump(exclude={'event_ids'}))
    digest = hashlib.sha256(_json(policy.model_dump()).encode()).hexdigest()
    run_id = str(uuid4())
    inserted = conn.execute('''INSERT INTO auto_decision_shadow_run
        (run_id,actor_id,request_key,request_digest,policy_json,created_at,item_count)
        VALUES(?,?,?,?,?,?,1) ON CONFLICT(actor_id,request_key) DO NOTHING RETURNING run_id''',
        (run_id,actor_id,request_key,digest,encoded_policy,datetime.now(timezone.utc).isoformat())).fetchone()
    if inserted:
        result={**diagnostic,'source':'completion_hook_shadow','settings_version':version,
                'policy_configured_by':actor_id,'automatic_confirmation_allowed':False}
        conn.execute('INSERT INTO auto_decision_shadow_item(run_id,event_id,ordinal,result_json) VALUES(?,?,0,?)',
                     (run_id,event_id,_json(result)))
