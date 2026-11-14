"""Per-camera ByteTrack adapter. Only measured boxes produce assessments.

Low-score boxes may retain an existing ID but cannot establish PPE absence.
Track expiry uses source time, not the inference machine's processing speed.
"""
from copy import deepcopy
from itertools import count
from math import isfinite
from types import SimpleNamespace

import numpy as np


def calculate_iou(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(
        0, min(a[3], b[3])-max(a[1], b[1]))
    union = ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)
    return intersection / union if union > 0 else 0.0


class _Boxes:
    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32).reshape(-1, 6)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return _Boxes(self.data[index])

    @property
    def conf(self):
        return self.data[:, 4]

    @property
    def cls(self):
        return self.data[:, 5]

    @property
    def xywh(self):
        result = self.data[:, :4].copy()
        result[:, :2] = (self.data[:, :2] + self.data[:, 2:4]) / 2
        result[:, 2:4] -= self.data[:, :2]
        return result


class PersonTracker:
    def __init__(self, *, max_missing_seconds=1.0, high_threshold=0.5,
                 low_threshold=0.1, match_threshold=0.8):
        if (not isfinite(max_missing_seconds) or max_missing_seconds <= 0
                or not 0 <= low_threshold < high_threshold <= 1
                or not 0 < match_threshold <= 1):
            raise ValueError("tracking parameters")
        # Importing Ultralytics without lap otherwise attempts a pip install.
        import importlib.util
        if importlib.util.find_spec("lap") is None:
            raise RuntimeError("Install requirements.txt (lap is required for ByteTrack)")
        from ultralytics.trackers.byte_tracker import BYTETracker, STrack
        ids = count(1)

        class CameraTrack(STrack):
            @staticmethod
            def next_id():
                return next(ids)

        self.engine = BYTETracker(SimpleNamespace(
            track_high_thresh=high_threshold, track_low_thresh=low_threshold,
            new_track_thresh=high_threshold, track_buffer=1_000_000,
            match_thresh=match_threshold, fuse_score=True))
        self.engine.track_class = CameraTrack
        self.max_missing_seconds = max_missing_seconds
        self.high_threshold = high_threshold
        self._seen = {}
        self._timestamp = None
        self._last_results = []

    def update(self, assessments, timestamp=None):
        # Compatibility for callers without a source clock; runners supply one.
        if timestamp is None:
            timestamp = 0 if self._timestamp is None else self._timestamp + 1/30
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (float, int))
                or not isfinite(timestamp)):
            raise ValueError("tracking timestamp")
        if self._timestamp is not None:
            if timestamp < self._timestamp:
                raise ValueError("tracking timestamp order")
            if timestamp == self._timestamp:
                return deepcopy(self._last_results)
        rows = list(assessments)
        data = []
        for row in rows:
            box = row["person_bbox"]
            score = row["person_confidence"]
            if (len(box) != 4 or any(not isfinite(v) for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]
                    or isinstance(score, bool) or not 0 <= score <= 1):
                raise ValueError("person detection")
            data.append([*box, score, 0])
        # Remove expired IDs before matching, so the same location after a long
        # disappearance cannot revive the previous person's episode.
        for name in ("tracked_stracks", "lost_stracks"):
            retained = []
            for track in getattr(self.engine, name):
                if timestamp - self._seen.get(track.track_id, timestamp) > self.max_missing_seconds:
                    track.mark_removed()
                    self._seen.pop(track.track_id, None)
                else:
                    retained.append(track)
            setattr(self.engine, name, retained)
        tracked = self.engine.update(_Boxes(data))
        for track in self.engine.tracked_stracks:
            if track.frame_id == self.engine.frame_id:
                self._seen[track.track_id] = timestamp
        results = []
        for track in tracked:
            index = int(track[-1])
            row = deepcopy(rows[index])
            row["tracking_id"] = "track-" + str(int(track[4]))
            row["tracking_only"] = row["person_confidence"] < self.high_threshold
            ambiguous = any(index != other and candidate["person_confidence"] >= self.high_threshold
                            and calculate_iou(row["person_bbox"], candidate["person_bbox"]) >= .65
                            for other, candidate in enumerate(rows))
            row["identity_ambiguous"] = ambiguous
            if row["tracking_only"] or ambiguous:
                row["status"] = "unknown"
                row["verified_ppe_confidence"] = None
                for assessment in row.get("ppe", {}).values():
                    assessment.update(status="unknown", verified_ppe_confidence=None,
                                      reason="ambiguous_person_overlap" if ambiguous else "weak_person_tracking_only")
            results.append(row)
        active = {t.track_id for t in self.engine.tracked_stracks + self.engine.lost_stracks}
        self._seen = {key: value for key, value in self._seen.items() if key in active}
        self._timestamp = timestamp
        self._last_results = deepcopy(results)
        return results
