"""Shared PPE vocabulary and composition for zone and future equipment rules.

Equipment requirements must come from administrator-confirmed equipment assigned
to the relevant camera/work position. Recognition candidates are not policy.
This module does not resolve equipment assignments or gate current inference.
"""

from collections.abc import Iterable

ALLOWED_PPE = frozenset({
    "helmet", "vest", "goggles", "gloves", "safety_shoes",
    "hearing_protection", "mask", "harness",
})


def validate_required_ppe(values: Iterable[str]) -> list[str]:
    normalized = [value.strip() for value in values]
    if any(value not in ALLOWED_PPE for value in normalized):
        raise ValueError(f"required_ppe values must be from: {', '.join(sorted(ALLOWED_PPE))}")
    if len(set(normalized)) != len(normalized):
        raise ValueError("required_ppe must not contain duplicates")
    return normalized


def combine_required_ppe(
    zone_required_ppe: Iterable[str], equipment_required_ppe: Iterable[Iterable[str]],
) -> list[str]:
    """Zone baseline plus applicable equipment additions, preserving input order.

The caller must explicitly supply equipment rules; their absence must not be
inferred from an unresolved assignment. Equipment cannot remove zone baseline PPE.
"""
    combined = validate_required_ppe(zone_required_ppe)
    for requirements in equipment_required_ppe:
        for requirement in validate_required_ppe(requirements):
            if requirement not in combined:
                combined.append(requirement)
    return combined
