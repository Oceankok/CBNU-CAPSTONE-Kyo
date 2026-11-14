
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
    """One-to-one PPE association with explicit ambiguity/visibility reasons.

    Positive boxes remain review-only observations, never a violation probability.
    Head visibility is independent of cropped feet. Missing PPE on a cropped
    torso is unknown; a well-localized visible positive can still establish wear.
    """
    from math import isfinite
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    def valid_box(box):
        return (isinstance(box, (list, tuple)) and len(box) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        and isfinite(v) for v in box)
                and box[0] < box[2] and box[1] < box[3])

    def overlap(a, b):
        return max(0, min(a[2], b[2])-max(a[0], b[0])) * max(
            0, min(a[3], b[3])-max(a[1], b[1]))

    def area(box):
        return (box[2]-box[0])*(box[3]-box[1])

    height, width = frame_shape[:2]
    people = [d for d in detections if d.get("class_name") == "person"
              and valid_box(d.get("bbox"))]
    rows = [{"person_bbox": p["bbox"], "person_confidence": p["confidence"],
             "ppe": {}} for p in people]
    for ppe_type in ("helmet", "vest"):
        regions = []
        visible = []
        for person in people:
            x1, y1, x2, y2 = person["bbox"]
            h, w = y2-y1, x2-x1
            region = ([x1-.05*w, y1-.08*h, x2+.05*w, y1+.30*h]
                      if ppe_type == "helmet"
                      else [x1, y1+.15*h, x2, y1+.70*h])
            regions.append(region)
            visible.append(w >= 12 and h >= 40 and 1 <= x1 < x2 <= width-1
                           and y1 >= 1 and region[3] <= height-1
                           and (ppe_type == "helmet" or y2 <= height-1))
        ppes = [d for d in detections if d.get("class_name") == ppe_type
                and valid_box(d.get("bbox")) and valid_ppe_confidence(d.get("confidence"))]
        scores = np.zeros((len(people), len(ppes)), dtype=float)
        uncertain = set()
        for j, detection in enumerate(ppes):
            box = detection["bbox"]
            cx, cy = center_of(box)
            for i, person in enumerate(people):
                # Weak person boxes are tracking support, never PPE owners.
                if not valid_ppe_confidence(person.get("confidence")):
                    continue
                region = regions[i]
                px1, py1, px2, py2 = person["bbox"]
                h, w = py2-py1, px2-px1
                coverage = overlap(region, box) / area(box)
                eligible = (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]
                            and coverage >= .5 and area(box) <= 2*area(region))
                if eligible:
                    expected_y = py1 + h*(.12 if ppe_type == "helmet" else .425)
                    distance = abs(cx-(px1+px2)/2)/w + abs(cy-expected_y)/h
                    scores[i, j] = 2 + coverage - distance
                elif coverage > .1:
                    uncertain.add(i)  # Nearby rejected box is not proof of absence.
            owners = sorted(((scores[i, j], i) for i in range(len(people))
                             if scores[i, j] > 0), reverse=True)
            if len(owners) > 1 and owners[0][0]-owners[1][0] < .15:
                uncertain.update(i for _, i in owners)
                scores[:, j] = 0
        matches = {}
        if scores.size:
            person_indices, ppe_indices = linear_sum_assignment(scores, maximize=True)
            for i, j in zip(person_indices, ppe_indices):
                if scores[i, j] > 0:
                    matches[int(i)] = int(j)
        for i, person in enumerate(people):
            x1, y1, x2, y2 = person["bbox"]
            valid_person = (valid_ppe_confidence(person.get("confidence"))
                            and x2-x1 >= 12 and y2-y1 >= 40
                            and 1 <= x1 < x2 <= width-1 and y1 >= 1)
            occluded = any(i != j and valid_ppe_confidence(other.get("confidence"))
                           and overlap(regions[i], other["bbox"])/area(regions[i]) > .3
                           for j, other in enumerate(people))
            if ppe_type not in supported_ppe:
                status, reason = "unknown", "unsupported_ppe"
            elif not valid_person:
                status, reason = "unknown", "person_not_observable"
            elif i in uncertain:
                status, reason = "unknown", "ambiguous_ppe_association"
            elif i in matches:
                status, reason = ppe_type, "positive_ppe_association"
            elif not visible[i] or occluded:
                status, reason = "unknown", "ppe_region_not_observable"
            else:
                status, reason = "suspected_no_" + ppe_type, "observable_ppe_absence"
            rows[i]["ppe"][ppe_type] = {
                "status": status, "verified_ppe_confidence": None, "reason": reason}
    return rows
