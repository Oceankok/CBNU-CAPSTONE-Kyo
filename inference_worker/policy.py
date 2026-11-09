
"""Validate camera PPE policies before evaluating violations."""


def ppe_policy_status(policy, ppe_type):
    """Return (enabled, reason) for a supported PPE requirement."""

    if ppe_type not in {"helmet", "vest"}:
        return False, "unsupported_ppe"

    if not isinstance(policy, dict):
        return False, "invalid_policy"

    if policy.get("configured") is not True:
        return False, "policy_not_configured"

    if policy.get("configuration_issues"):
        return False, "configuration_issues"

    required = policy.get("required_ppe")
    detectable = policy.get("detectable_ppe")

    if not isinstance(required, list) or not isinstance(detectable, list):
        return False, "invalid_ppe_lists"

    if ppe_type not in required:
        return False, f"{ppe_type}_not_required"

    if ppe_type not in detectable:
        return False, f"{ppe_type}_not_detectable"

    return True, f"{ppe_type}_required"


def helmet_policy_status(policy):
    return ppe_policy_status(policy, "helmet")
