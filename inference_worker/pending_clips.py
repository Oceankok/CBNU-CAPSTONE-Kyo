
"""Manage pending local clips until post-event footage arrives."""

from datetime import datetime, timezone

from inference_worker.clip_manager import calculate_clip_window


class PendingClips:
    def __init__(self):
        self.pending = []

    def add(self, candidate, camera_id):
        start = datetime.fromtimestamp(
            candidate["start_time"], tz=timezone.utc
        )
        end = datetime.fromtimestamp(
            candidate["end_time"], tz=timezone.utc
        )

        clip_start, clip_end = calculate_clip_window(start, end)

        job = {
            "camera_id": camera_id,
            "tracking_id": candidate["tracking_id"],
            "clip_start": clip_start,
            "clip_end": clip_end,
        }

        self.pending.append(job)
        return job

    def pop_ready(self, latest_timestamp):
        """Return jobs whose post-event interval has elapsed."""
        ready = [
            job for job in self.pending
            if latest_timestamp >= job["clip_end"]
        ]

        self.pending = [
            job for job in self.pending
            if latest_timestamp < job["clip_end"]
        ]

        return ready
