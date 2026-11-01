"""Per-person rule resolution for the ROI API; does not change the current runner."""
import math

from backend.work_areas.geometry import contains, validate_points
from backend.zones.policy import combine_required_ppe


def resolve_person_policy(policy, bbox, frame_width, frame_height):
    """Return rules and area membership. Reset a person's timer when membership/version changes.

    bbox uses original-frame pixel xyxy coordinates, before any display resizing.
    Invalid or unresolved configuration must stop evaluation, never fall back to
    the camera-wide union while work-area mode is enabled.
    """
    if not isinstance(policy, dict) or policy.get('configured') is not True:
        raise ValueError('work_area_policy_not_configured')
    if policy.get('scope') == 'camera':
        return {**policy, 'area_ids': []}
    if policy.get('scope') != 'work_area' or policy.get('configuration_issues') or not policy.get('policy_version'):
        raise ValueError('invalid_work_area_policy')
    values = [*bbox, frame_width, frame_height]
    if len(bbox) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('invalid_person_position')
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= frame_width and 0 <= y1 < y2 <= frame_height):
        raise ValueError('invalid_person_position')
    point = ((x1+x2)/2/frame_width, y2/frame_height)
    matches = [area for area in policy['areas'] if contains(validate_points(area['points']), point)]
    required = combine_required_ppe(policy['zone_required_ppe'], [area['equipment_required_ppe'] for area in matches])
    return {**policy, 'scope': 'person', 'area_ids': sorted(area['area_id'] for area in matches),
            'required_ppe': required, 'detectable_ppe': policy['event_supported_ppe']}
