from pydantic import BaseModel, ConfigDict, Field
from backend.education.models import MaterialContent


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recommendation_ids: list[str] = Field(min_length=2, max_length=20)


class SelectedSnapshot(Selection):
    expected_snapshot: str = Field(pattern=r'^[0-9a-f]{64}$')


class BundleCreate(MaterialContent, SelectedSnapshot):
    pass
