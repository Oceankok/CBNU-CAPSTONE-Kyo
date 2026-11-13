"""Shared sequence rules for one capture process and its sender directory."""
import re


def next_sequence(directory):
    # Timestamp-only leftovers also reserve their number. Never overwrite a
    # prior session's capture time while its MP4 may still be awaiting delivery.
    numbers = (
        int(match.group(1))
        for path in directory.iterdir()
        if path.is_file()
        and (match := re.fullmatch(r"chunk_(\d+)\.(?:mp4(?:\.part)?|txt|cleanup)", path.name))
    )
    return max(numbers, default=-1) + 1


def completed_chunks(directory):
    chunks = sorted(
        (path for path in directory.glob("chunk_*.mp4")
         if path.is_file() and re.fullmatch(r"chunk_\d+\.mp4", path.name)),
        key=lambda path: int(path.stem.removeprefix("chunk_")),
    )
    # Preserve the legacy sender's tail exclusion for folders written by older
    # capture versions. New capture publishes finalized .part files separately.
    return [path for index, path in enumerate(chunks)
            if index < len(chunks) - 1 or path.with_suffix(".ready").is_file()]


def publish_completed_segments(directory, manifest):
    """Expose only segments whose trailer FFmpeg has finished writing.

    The bounded flat list may be replaced while we read it. Only complete,
    strictly named lines are used; a transient sharing violation is retried on
    the next capture iteration, after FFmpeg closes its file handle.
    """
    try:
        entries = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    except (FileNotFoundError, PermissionError):
        return
    for entry in entries:
        if not entry.endswith('\n'):
            continue
        name = entry.rstrip('\r\n')
        if not re.fullmatch(r"chunk_\d+\.mp4\.part", name):
            continue
        pending = directory / name
        ready = pending.with_suffix('')
        if ready.exists() or not pending.exists():
            continue
        try:
            # Publish proof before exposing the MP4, so even the final segment
            # is eligible. A legacy writer without this marker keeps tail safety.
            ready.with_suffix(".ready").touch()
            pending.rename(ready)
        except (FileNotFoundError, PermissionError):
            continue
