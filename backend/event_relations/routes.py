from fastapi import APIRouter, Depends, Query
from backend.auth.service import require_admin
from backend.db.connection import run_stats_transaction
from backend.event_relations.service import preview

router = APIRouter(prefix='/api/events', tags=['event relation previews'], dependencies=[Depends(require_admin)])


@router.get('/{event_id}/suspected-duplicates/preview')
def suspected_duplicates(event_id: str, limit: int = Query(default=20, ge=1, le=100),
                         offset: int = Query(default=0, ge=0, le=10000)):
    return run_stats_transaction(lambda conn: preview(conn, event_id, limit, offset))


# Saved relations never suppress the metadata preview above.
from pydantic import BaseModel, ConfigDict, Field
from backend.event_relations import persistent


class EvidenceGuard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/{event_id}/suspected-duplicates/evidence/{peer_id}')
def pair_evidence(event_id: str, peer_id: str):
    return run_stats_transaction(lambda conn: persistent.evidence(conn, event_id, peer_id))


@router.post('/{event_id}/saved-suspected-relations/{peer_id}')
def save_relation(event_id: str, peer_id: str, body: EvidenceGuard, user: dict = Depends(require_admin)):
    return persistent.run_write(lambda conn: persistent.save(conn, event_id, peer_id, body.expected_fingerprint, user['user_id']))


@router.get('/{event_id}/saved-suspected-relations')
def saved_relations(event_id: str, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000)):
    return run_stats_transaction(lambda conn: persistent.listing(conn, event_id, limit, offset))


# The event prefix keeps the existing router/auth boundary. This path is not an event ID.
@router.post('/saved-suspected-relations/{relation_id}/release')
def release_relation(relation_id: str, body: EvidenceGuard, user: dict = Depends(require_admin)):
    return persistent.run_write(lambda conn: persistent.release(conn, relation_id, body.expected_fingerprint, user['user_id']))


@router.post('/{event_id}/suspected-duplicates/reconcile-current')
def reconcile_current(event_id: str, limit: int = Query(default=20, ge=1, le=100),
                      offset: int = Query(default=0, ge=0, le=10000), user: dict = Depends(require_admin)):
    """Explicit admin action, not invoked on GET or automatically on ingestion."""
    return persistent.run_write(lambda conn: persistent.reconcile(conn, event_id, user['user_id'], limit, offset))
