
"""Validate local PPE candidates before server event submission."""

from math import isfinite


def valid_ppe_confidence(value):
    """A real model score, at least the detector's existing 0.5 threshold."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isfinite(value) and 0.5 <= value <= 1)


def validate_candidate(candidate, policy):
    if not isinstance(candidate, dict):
        return False, "invalid_candidate"

    if not isinstance(policy, dict):
        return False, "invalid_policy"

    if policy.get("configured") is not True:
        return False, "policy_not_configured"

    if policy.get("configuration_issues"):
        return False, "policy_configuration_issues"

    required = policy.get("required_ppe")

    if not isinstance(required, list):
        return False, "invalid_required_ppe"

    if candidate.get("ppe_type") != "helmet":
        return False, "unsupported_ppe"

    if "helmet" not in required:
        return False, "helmet_not_required"

    if candidate.get("duration_seconds", 0) < 3.0:
        return False, "duration_too_short"

    confidence = candidate.get("verified_ppe_confidence")
    if confidence is None:
        return False, "ppe_confidence_not_verified"
    if not valid_ppe_confidence(confidence):
        return False, "invalid_ppe_confidence"

    return True, "valid"
