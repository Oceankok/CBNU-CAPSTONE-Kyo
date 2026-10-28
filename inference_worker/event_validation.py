
"""Validate local PPE candidates before server event submission."""


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

    # The current model has no dedicated 'no_helmet' class.
    # Person detection confidence must not be submitted as
    # verified PPE violation confidence.
    if candidate.get("verified_ppe_confidence") is None:
        return False, "ppe_confidence_not_verified"

    return True, "valid"
