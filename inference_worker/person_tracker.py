
"""Simple per-camera IoU tracker for local integration testing."""


def calculate_iou(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])

    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - intersection

    return intersection / union if union > 0 else 0.0


class PersonTracker:
    def __init__(self, iou_threshold=0.3, max_missing=5):
        self.iou_threshold = iou_threshold
        self.max_missing = max_missing
        self.tracks = {}
        self.next_id = 1

    def update(self, assessments):
        matched_ids = set()
        results = []

        for person in assessments:
            best_id = None
            best_iou = self.iou_threshold

            for track_id, track in self.tracks.items():
                if track_id in matched_ids:
                    continue

                score = calculate_iou(
                    person["person_bbox"], track["bbox"]
                )

                if score > best_iou:
                    best_iou = score
                    best_id = track_id

            if best_id is None:
                best_id = self.next_id
                self.next_id += 1

            self.tracks[best_id] = {
                "bbox": person["person_bbox"],
                "missing": 0,
            }
            matched_ids.add(best_id)

            results.append({
                **person,
                "tracking_id": f"track-{best_id}",
            })

        for track_id in list(self.tracks):
            if track_id not in matched_ids:
                self.tracks[track_id]["missing"] += 1

                if self.tracks[track_id]["missing"] > self.max_missing:
                    del self.tracks[track_id]

        return results
