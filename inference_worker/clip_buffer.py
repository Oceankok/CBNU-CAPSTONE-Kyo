
"""Bounded timestamped frame buffer for local event clip assembly."""

from collections import deque
from datetime import datetime, timedelta


class ClipBuffer:
    def __init__(self, retention_seconds=30.0, max_bytes=256*1024*1024):
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")

        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self.bytes_used = 0
        self.retention = timedelta(seconds=retention_seconds)
        self.frames = deque()

    def add(self, timestamp: datetime, frame):
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Timezone-aware timestamp required")

        if self.frames and timestamp < self.frames[-1][0]:
            raise ValueError("Frames must arrive in timestamp order")

        size = frame.nbytes
        if size > self.max_bytes:
            raise ValueError("Frame exceeds buffer byte limit")
        cutoff = timestamp - self.retention
        while self.frames and self.frames[0][0] < cutoff:
            self.bytes_used -= self.frames.popleft()[1].nbytes
        evicted = 0
        # Evict before copying so the new allocation cannot exceed this budget.
        while self.frames and self.bytes_used + size > self.max_bytes:
            self.bytes_used -= self.frames.popleft()[1].nbytes
            evicted += 1
        self.frames.append((timestamp, frame.copy()))
        self.bytes_used += size
        return evicted

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
