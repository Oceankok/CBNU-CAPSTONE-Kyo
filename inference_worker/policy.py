
"""Validate camera PPE policies before evaluating violations."""


def helmet_policy_status(policy):
    """Return (enabled, reason) for helmet violation evaluation."""

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

    if "helmet" not in required:
        return False, "helmet_not_required"

    if "helmet" not in detectable:
        return False, "helmet_not_detectable"

    return True, "helmet_required"
