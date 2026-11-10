
"""Conservative helmet assessment from YOLO detections.

This is a candidate-screening heuristic, not definitive PPE compliance.
"""

from inference_worker.event_validation import valid_ppe_confidence


def center_of(bbox):
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2, (y1 + y2) / 2


def helmet_in_head_region(person_bbox, helmet_bbox):
    px1, py1, px2, py2 = person_bbox
    hx, hy = center_of(helmet_bbox)

    person_height = py2 - py1
    if person_height <= 0 or px2 <= px1:
        return False

    # Approximate top 30% of a person's detected region.
    return (
        px1 <= hx <= px2
        and py1 <= hy <= py1 + person_height * 0.30
    )


def assess_helmets(detections):
    people = [
        d for d in detections if d["class_name"] == "person"
    ]
    helmets = [
        d for d in detections if d["class_name"] == "helmet"
    ]
    # An absent helmet box alone is never verified negative-class evidence.
    negatives = [d for d in detections if d["class_name"] == "no_helmet"
                 and valid_ppe_confidence(d["confidence"])]

    assessments = []

    for person in people:
        x1, y1, x2, y2 = person["bbox"]
        height = y2 - y1

        # Cropped or unusually short detections are uncertain.
        confidence = None
        if height <= 0 or x2 <= x1:
            status = "unknown"
        else:
            matched = any(
                helmet_in_head_region(person["bbox"], h["bbox"])
                for h in helmets
            )
            matching = [d for d in negatives
                        if helmet_in_head_region(person["bbox"], d["bbox"])]
            # Do not attach one negative prediction to overlapping people.
            unique = [d for d in matching if sum(
                helmet_in_head_region(other["bbox"], d["bbox"])
                for other in people) == 1]
            if matched and matching:
                status = "unknown"  # Contradictory labels reset the observation.
            else:
                status = "helmet" if matched else "suspected_no_helmet"
                if unique:
                    confidence = max(d["confidence"] for d in unique)

        assessments.append({
            "person_bbox": person["bbox"],
            "person_confidence": person["confidence"],
            "status": status,
            "verified_ppe_confidence": confidence,
        })

    return assessments


def assess_ppe(detections, frame_shape, supported_ppe=("helmet", "vest")):
    """Associate positive PPE boxes with people; absence is review-only evidence.

    Geometric regions are tunable heuristics, not pose/occlusion estimates.
    Small/cropped people, overlapping regions, and shared PPE boxes are unknown.
    One row per person preserves identity when tracking both PPE types.
    """
    from math import isfinite

    def valid_box(box):
        return (isinstance(box, (list, tuple)) and len(box) == 4
                and all(isinstance(v, (int, float)) and isfinite(v) for v in box)
                and box[0] < box[2] and box[1] < box[3])

    def overlap(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
            0, min(a[3], b[3]) - max(a[1], b[1]))

    def area(box):
        return (box[2] - box[0]) * (box[3] - box[1])

    height, width = frame_shape[:2]
    people = [d for d in detections if d.get("class_name") == "person"
              and valid_box(d.get("bbox"))]
    rows = [{"person_bbox": p["bbox"], "person_confidence": p["confidence"],
             "ppe": {}} for p in people]
    for ppe_type in ("helmet", "vest"):
        regions = []
        for person in people:
            x1, y1, x2, y2 = person["bbox"]
            h = y2 - y1
            regions.append([x1, y1, x2, y1 + .30*h] if ppe_type == "helmet"
                           else [x1, y1 + .15*h, x2, y1 + .70*h])
        matches = [[] for _ in people]
        ambiguous = set()
        for detection in detections:
            box = detection.get("bbox")
            if detection.get("class_name") != ppe_type or not valid_box(box):
                continue
            cx, cy = center_of(box)
            owners = [i for i, region in enumerate(regions)
                      if region[0] <= cx <= region[2] and region[1] <= cy <= region[3]
                      and overlap(people[i]["bbox"], box) / area(box) >= .8]
            if len(owners) == 1:
                matches[owners[0]].append(detection)
            elif owners:
                ambiguous.update(owners)
        for i, person in enumerate(people):
            x1, y1, x2, y2 = person["bbox"]
            # Conservative full-person gate: cropped frames cannot establish absence.
            observable = (ppe_type in supported_ppe and x2-x1 >= 12 and y2-y1 >= 40
                          and 1 <= x1 < x2 <= width-1 and 1 <= y1 < y2 <= height-1
                          and valid_ppe_confidence(person.get("confidence")))
            occluded = any(i != j and overlap(regions[i], other["bbox"]) / area(regions[i]) > .3
                           for j, other in enumerate(people))
            if not observable or i in ambiguous:
                status = "unknown"
            elif matches[i]:
                status = ppe_type
            elif occluded:
                status = "unknown"
            else:
                status = f"suspected_no_{ppe_type}"
            rows[i]["ppe"][ppe_type] = {"status": status, "verified_ppe_confidence": None}
    return rows
