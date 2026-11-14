from typing import Annotated
from pydantic import Field, BaseModel, ConfigDict
from backend.education.models import MaterialContent, MaterialUpdate

# BCP-47 language tag subset. Korean is the immutable original, not a translation.
Language = Annotated[str, Field(pattern=r'^[a-z]{2,3}(?:-[A-Z]{2})?$', max_length=6)]

class TranslationCreate(MaterialContent):
    language: Language
    expected_source_version: int = Field(ge=0)

class TranslationGenerate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Language
    expected_source_version: int = Field(ge=0)
    # Explicit per-request boundary: configuration alone never sends source text.
    allow_external_processing: bool = False

class TranslationUpdate(MaterialUpdate):
    pass
