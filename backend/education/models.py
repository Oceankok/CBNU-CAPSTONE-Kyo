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


class MaterialApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    approved: bool
