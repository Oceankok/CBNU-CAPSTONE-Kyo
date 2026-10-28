
"""Bounded timestamped frame buffer for local event clip assembly."""

from collections import deque
from datetime import datetime, timedelta


class ClipBuffer:
    def __init__(self, retention_seconds=30.0):
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")

        self.retention = timedelta(seconds=retention_seconds)
        self.frames = deque()

    def add(self, timestamp: datetime, frame):
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Timezone-aware timestamp required")

        if self.frames and timestamp < self.frames[-1][0]:
            raise ValueError("Frames must arrive in timestamp order")

        self.frames.append((timestamp, frame.copy()))

        cutoff = timestamp - self.retention

        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def select(self, start: datetime, end: datetime):
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Timezone-aware timestamps required")

        if end < start:
            raise ValueError("End must not precede start")

        return [
            frame.copy()
            for timestamp, frame in self.frames
            if start <= timestamp <= end
        ]
