"""Shared sequence rules for one capture process and its sender directory."""
import re


def next_sequence(directory):
    # Timestamp-only leftovers also reserve their number. Never overwrite a
    # prior session's capture time while its MP4 may still be awaiting delivery.
    numbers = (
        int(match.group(1))
        for path in directory.iterdir()
        if path.is_file()
        and (match := re.fullmatch(r"chunk_(\d+)\.(?:mp4|txt)", path.name))
    )
    return max(numbers, default=-1) + 1


def completed_chunks(directory):
    chunks = sorted(
        (path for path in directory.glob("chunk_*.mp4")
         if path.is_file() and re.fullmatch(r"chunk_\d+\.mp4", path.name)),
        key=lambda path: int(path.stem.removeprefix("chunk_")),
    )
    # FFmpeg closes the preceding segment before opening the next one. Capture
    # resumes above all existing numbers, so the greatest one is the active tail.
    return chunks[:-1]
