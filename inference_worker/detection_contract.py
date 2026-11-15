"""The current scored xyxy detector contract. No score-free adaptation."""
from collections.abc import Mapping
from math import isfinite


class DetectionContractError(ValueError):
    pass


def unit_score(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and isfinite(value) and 0 <= value <= 1)


def valid_box(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(not isinstance(v, bool) and isinstance(v, (int, float))
                    and isfinite(v) for v in value)
            and value[0] < value[2] and value[1] < value[3])


def validate_detections(rows, *, class_map=None):
    if not isinstance(rows, (list, tuple)) or len(rows) > 4096:
        raise DetectionContractError("detector_rows")
    for row in rows:
        if (not isinstance(row, Mapping) or not isinstance(row.get("class_name"), str)
                or not row["class_name"] or not unit_score(row.get("confidence"))
                or not valid_box(row.get("bbox"))):
            raise DetectionContractError("detector_row_requires_scored_xyxy")
        if class_map is not None:
            ident = row.get("class_id")
            if (type(ident) is not int or ident not in class_map
                    or class_map[ident] != row["class_name"]):
                raise DetectionContractError("detector_class_binding")
    return rows


def unknown_people(rows):
    """Retain only real scored person measurements, masking all PPE evidence.

    Unscored people cannot be fed to ByteTrack; other valid people in the failed
    frame may maintain identity, but none establish absence or positive PPE.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    people = []
    for row in rows:
        if (isinstance(row, Mapping) and row.get("class_name") == "person"
                and unit_score(row.get("confidence")) and valid_box(row.get("bbox"))):
            people.append({"person_bbox": row["bbox"], "person_confidence": row["confidence"],
                           "status": "unknown", "verified_ppe_confidence": None,
                           "ppe": {p: {"status": "unknown", "verified_ppe_confidence": None,
                                       "reason": "invalid_detector_observation"}
                                   for p in ("helmet", "vest")}})
    return people
