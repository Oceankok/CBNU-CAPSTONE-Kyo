from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.auth.service import require_admin
from backend.event_merge.service import merge

router = APIRouter(prefix='/api/events', tags=['event merge'])


class MergeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    source_event_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=20)
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


@router.post('/{event_id}/merge')
def merge_events(event_id: str, body: MergeRequest, user: dict = Depends(require_admin)):
    return merge(event_id, body.source_event_ids, body.expected_version, body.reason, user['user_id'])
