from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


ChecklistItem = Annotated[str, Field(min_length=1, max_length=500)]


class MaterialContent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=12000)
    checklist: list[ChecklistItem] = Field(min_length=1, max_length=20)


class MaterialUpdate(MaterialContent):
    expected_version: int = Field(ge=0)


class MaterialVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)


class MaterialApproval(MaterialVersion):
    approved: bool


class RecommendationScope(BaseModel):
    """The recommendation snapshot the editor actually selected, not its rank."""
    model_config = ConfigDict(extra='forbid')
    recommendation_id: str = Field(min_length=1,max_length=200)
    quarter: str = Field(min_length=1,max_length=100)
    zone_name: str = Field(max_length=200)
    ppe_type: str = Field(min_length=1,max_length=100)
    education_topic: str = Field(min_length=1,max_length=500)


class MaterialCreate(MaterialContent):
    # Optional solely for old UI compatibility. Omission does NOT protect a stale editor.
    expected_scope: RecommendationScope | None = None


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_scope: RecommendationScope | None = None
