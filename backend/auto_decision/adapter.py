"""Diagnostic-only boundary; detector scores never constitute approval proof.

No calibrated scorer or model/label-map attestation is installed. This adapter
cannot approve an event, even when all structural preview checks pass.
"""
from collections.abc import Mapping
import math


class DiagnosticOnlyAdapter:
    BLOCKERS = (
        'calibrated_violation_probability_unavailable',
        'model_artifact_attestation_unavailable',
        'whole_event_classifier_evidence_unavailable',
    )

    def policy_blockers(self, policy):
        # User-supplied model IDs and validation references are scope, not proof.
        return list(self.BLOCKERS)

    def diagnose(self, diagnostic):
        diagnostic = diagnostic if isinstance(diagnostic, Mapping) else {}
        score = diagnostic.get('observed_confidence')
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not 0 <= score <= 1 or not math.isfinite(score)):
            score = None
        whole = diagnostic.get('whole_event_evidence')
        cameras = whole.get('cameras') if isinstance(whole, Mapping) else None
        valid_cameras = (isinstance(cameras, list) and bool(cameras)
                         and all(isinstance(camera, Mapping)
                                 and isinstance(camera.get('manifest_coverage'), Mapping)
                                 for camera in cameras))
        return {
            'mode': 'diagnostic_only',
            'observed_detector_confidence': score,
            'calibrated_violation_probability': None,
            'camera_manifest_structure_valid': bool(valid_cameras),
            'activation_blockers': list(self.BLOCKERS),
            'automatic_confirmation_allowed': False,
        }

    def validate(self, conn, event_id, policy, diagnostic):
        # Do not query/write, deserialize proof, or infer it from a confidence.
        return None
