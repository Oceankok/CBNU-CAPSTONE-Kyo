"""Allowlisted failure metadata only; never exception messages or face data."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.media.redaction import MediaError


class FailureDiagnostic(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    model: Literal['retinaface-resnet50'] = 'retinaface-resnet50'
    status: Literal['failed'] = 'failed'
    reason: Literal['residual_detection_limit', 'invalid_repair_box', 'frame_count_exceeded',
                    'detector_runtime_error', 'detector_output_invalid',
                    'detector_probability_invalid', 'detector_box_decode_failed',
                    'detector_postprocess_error']
    stage: Literal['detector', 'primary_detection', 'encoded_verification'] = 'detector'
    passes: int | None = Field(default=None, ge=1, le=3)
    frames_checked: int | None = Field(default=None, ge=0, le=100000)
    expected_frames: int | None = Field(default=None, ge=0, le=100000)


class VerificationFailure(MediaError):
    def __init__(self, reason):
        super().__init__('face_verification_failed')
        self.verification_failure = FailureDiagnostic(reason=reason).model_dump(exclude_none=True)

    def annotate(self, **values):
        # Diagnostics must never replace the original fail-closed error.
        for name in ('frames_checked', 'expected_frames'):
            if type(values.get(name)) is int:
                values[name] = max(0, min(100000, values[name]))
        try:
            self.verification_failure = FailureDiagnostic.model_validate(
                {**self.verification_failure, **values}).model_dump(exclude_none=True)
        except ValueError:
            pass
        return self
