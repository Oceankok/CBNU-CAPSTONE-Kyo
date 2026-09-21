"""Person-PPE association and per-worker temporal violation tracking.

The detector produces independent person and PPE boxes.  This module turns
those boxes into worker-level observations without assuming that the total
number of helmets belongs to the total number of people in a frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence


Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: Box
    track_id: int | None = None


@dataclass(frozen=True)
class WorkerObservation:
    track_id: int
    person: Detection
    helmet: Detection | None
    status: str
    confidence: float

    @property
    def is_violation(self) -> bool:
        return self.status in {"no_helmet", "no_helmet_inferred"}


@dataclass(frozen=True)
class ViolationEvent:
    track_id: int
    confidence: float
    duration_sec: float
    sample_count: int
    box: Box
    status: str


def box_iou(box_a: Box, box_b: Box) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    intersection = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0.0 else 0.0


def _association_score(person: Detection, ppe: Detection) -> float | None:
    """Score a PPE box against the expected head area of one person.

    The PPE centre must be horizontally inside the person and vertically near
    the upper body.  A small margin allows partially clipped helmets while
    preventing a helmet from satisfying a neighbouring worker.
    """

    px1, py1, px2, py2 = person.box
    hx1, hy1, hx2, hy2 = ppe.box
    person_w = max(1.0, px2 - px1)
    person_h = max(1.0, py2 - py1)
    centre_x = (hx1 + hx2) / 2.0
    centre_y = (hy1 + hy2) / 2.0

    margin_x = person_w * 0.12
    top = py1 - person_h * 0.08
    head_bottom = py1 + person_h * 0.42
    if not (px1 - margin_x <= centre_x <= px2 + margin_x):
        return None
    if not (top <= centre_y <= head_bottom):
        return None

    expected_x = (px1 + px2) / 2.0
    expected_y = py1 + person_h * 0.12
    distance_x = abs(centre_x - expected_x) / person_w
    distance_y = abs(centre_y - expected_y) / person_h
    confidence_bonus = ppe.confidence * 0.25
    return 1.0 - (0.65 * distance_x + 0.35 * distance_y) + confidence_bonus


class PersonPPEAssociator:
    """Assign at most one helmet/no-helmet detection to each person."""

    def __init__(self, infer_missing_helmet: bool = True) -> None:
        self.infer_missing_helmet = infer_missing_helmet

    def associate(
        self,
        detections: Iterable[Detection],
        fallback_track_ids: Mapping[int, int] | None = None,
    ) -> list[WorkerObservation]:
        items = list(detections)
        people = [item for item in items if item.label == "person"]
        ppe_items = [
            item for item in items if item.label in {"helmet", "no_helmet"}
        ]

        candidates: list[tuple[float, int, int]] = []
        for person_index, person in enumerate(people):
            for ppe_index, ppe in enumerate(ppe_items):
                score = _association_score(person, ppe)
                if score is not None:
                    candidates.append((score, person_index, ppe_index))

        matched_people: dict[int, int] = {}
        matched_ppe: set[int] = set()
        for _, person_index, ppe_index in sorted(candidates, reverse=True):
            if person_index in matched_people or ppe_index in matched_ppe:
                continue
            matched_people[person_index] = ppe_index
            matched_ppe.add(ppe_index)

        observations: list[WorkerObservation] = []
        for person_index, person in enumerate(people):
            ppe = (
                ppe_items[matched_people[person_index]]
                if person_index in matched_people
                else None
            )
            track_id = person.track_id
            if track_id is None and fallback_track_ids is not None:
                track_id = fallback_track_ids.get(person_index)
            if track_id is None:
                # The caller should normally supply tracker IDs.  A stable
                # negative ID still keeps untracked detections distinct.
                track_id = -(person_index + 1)

            if ppe is not None and ppe.label == "helmet":
                status = "helmet"
                confidence = min(person.confidence, ppe.confidence)
            elif ppe is not None and ppe.label == "no_helmet":
                status = "no_helmet"
                confidence = min(person.confidence, ppe.confidence)
            elif self.infer_missing_helmet:
                status = "no_helmet_inferred"
                # Missing-object evidence is weaker than an explicit class.
                confidence = person.confidence * 0.5
            else:
                status = "unknown"
                confidence = person.confidence

            observations.append(
                WorkerObservation(
                    track_id=track_id,
                    person=person,
                    helmet=ppe,
                    status=status,
                    confidence=confidence,
                )
            )
        return observations


@dataclass
class _TrackHistory:
    observations: deque[tuple[float, bool, float, str]] = field(
        default_factory=deque
    )
    last_seen: float = 0.0
    last_event: float = float("-inf")
    box: Box = (0.0, 0.0, 0.0, 0.0)


class ViolationTracker:
    """Confirm violations independently for each tracked worker."""

    def __init__(
        self,
        duration_sec: float = 2.0,
        cooldown_sec: float = 10.0,
        min_samples: int = 3,
        min_violation_ratio: float = 0.6,
        track_ttl_sec: float = 3.0,
    ) -> None:
        if duration_sec < 0 or cooldown_sec < 0 or track_ttl_sec <= 0:
            raise ValueError("time thresholds must be non-negative")
        if min_samples < 1:
            raise ValueError("min_samples must be at least 1")
        if not 0.0 < min_violation_ratio <= 1.0:
            raise ValueError("min_violation_ratio must be in (0, 1]")
        self.duration_sec = duration_sec
        self.cooldown_sec = cooldown_sec
        self.min_samples = min_samples
        self.min_violation_ratio = min_violation_ratio
        self.track_ttl_sec = track_ttl_sec
        self._tracks: dict[int, _TrackHistory] = {}

    def update(
        self, observations: Sequence[WorkerObservation], now: float
    ) -> list[ViolationEvent]:
        events: list[ViolationEvent] = []
        retention_sec = max(self.duration_sec * 1.5, self.duration_sec + 0.5)

        for observation in observations:
            history = self._tracks.get(observation.track_id)
            if history is None or now - history.last_seen > self.track_ttl_sec:
                history = _TrackHistory()
                self._tracks[observation.track_id] = history
            history.last_seen = now
            history.box = observation.person.box
            history.observations.append(
                (
                    now,
                    observation.is_violation,
                    observation.confidence,
                    observation.status,
                )
            )
            while (
                history.observations
                and now - history.observations[0][0] > retention_sec
            ):
                history.observations.popleft()

            recent = list(history.observations)
            violations = [item for item in recent if item[1]]
            if not observation.is_violation:
                continue
            if len(recent) < self.min_samples or not violations:
                continue
            evidence_duration = now - violations[0][0]
            violation_ratio = len(violations) / len(recent)
            if evidence_duration < self.duration_sec:
                continue
            if violation_ratio < self.min_violation_ratio:
                continue
            if now - history.last_event < self.cooldown_sec:
                continue

            history.last_event = now
            events.append(
                ViolationEvent(
                    track_id=observation.track_id,
                    confidence=sum(item[2] for item in violations) / len(violations),
                    duration_sec=evidence_duration,
                    sample_count=len(recent),
                    box=history.box,
                    status=violations[-1][3],
                )
            )

        expired = [
            track_id
            for track_id, history in self._tracks.items()
            if now - history.last_seen > self.track_ttl_sec
        ]
        for track_id in expired:
            del self._tracks[track_id]
        return events


class IoUTrackFallback:
    """Small fallback tracker used until Ultralytics emits track IDs."""

    def __init__(self, iou_threshold: float = 0.3, max_missed: int = 5) -> None:
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self._next_id = 1_000_000
        self._tracks: dict[int, tuple[Box, int]] = {}

    def update(self, person_boxes: Sequence[Box]) -> dict[int, int]:
        matches: dict[int, int] = {}
        available_tracks = set(self._tracks)
        scored: list[tuple[float, int, int]] = []
        for person_index, box in enumerate(person_boxes):
            for track_id in available_tracks:
                score = box_iou(box, self._tracks[track_id][0])
                if score >= self.iou_threshold:
                    scored.append((score, person_index, track_id))

        used_people: set[int] = set()
        used_tracks: set[int] = set()
        for _, person_index, track_id in sorted(scored, reverse=True):
            if person_index in used_people or track_id in used_tracks:
                continue
            matches[person_index] = track_id
            used_people.add(person_index)
            used_tracks.add(track_id)

        for person_index, box in enumerate(person_boxes):
            if person_index not in matches:
                matches[person_index] = self._next_id
                self._next_id += 1

        updated: dict[int, tuple[Box, int]] = {}
        for person_index, track_id in matches.items():
            updated[track_id] = (person_boxes[person_index], 0)
        for track_id, (box, missed) in self._tracks.items():
            if track_id not in updated and missed + 1 <= self.max_missed:
                updated[track_id] = (box, missed + 1)
        self._tracks = updated
        return matches
