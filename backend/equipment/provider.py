"""Provider-neutral boundary; adapters receive only already-redacted JPEG bytes."""

from typing import Literal, Protocol
import os

from pydantic import BaseModel, ConfigDict, Field


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    equipment_type: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(default="", max_length=500)


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[Candidate] = Field(max_length=5)


class ProviderError(Exception):
    def __init__(self, code: Literal["provider_timeout", "provider_unavailable",
                                  "provider_rate_limited", "provider_response_invalid",
                                  "provider_auth_required", "provider_usage_limit"]):
        self.code = code


class RecognitionProvider(Protocol):
    def analyze(self, redacted_jpeg: bytes, *, timeout_seconds: float) -> ProviderResult:
        """Adapters must enforce the timeout and must never log image bytes or secrets."""
        ...


def get_provider() -> RecognitionProvider | None:
    selected = os.environ.get("PPE_EQUIPMENT_PROVIDER", "").strip()
    if selected in {"codex_chatgpt", "chatgpt_oauth"}:
        from backend.equipment.subscription_provider import SubscriptionProvider
        return SubscriptionProvider(selected)
    if selected == 'openai':
        from backend.equipment.openai_provider import OpenAIProvider
        return OpenAIProvider()
    return None
