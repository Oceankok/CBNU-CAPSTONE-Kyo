
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
