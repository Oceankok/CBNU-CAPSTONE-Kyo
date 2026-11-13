# Inference process crash: continuity limits

The supervisor can restart a process; it cannot establish that the same person
is still present. `PersonTracker` and lifecycle state live in memory. A fresh
`CaptureOutbox` allocates a fresh session UUID and marks old active records
interrupted. Old request bodies remain immutable for idempotent delivery, while
new lifecycle observations accumulate a fresh three-second threshold and create
new event UUIDs. Recycled numeric/string tracking IDs are not identity evidence.

This deliberately prevents identity reuse and fabricated gap coverage. It can
produce two distinct incidents for one real-world episode across a crash; that
is an explicit incomplete/duplicate-review case, not seamless restoration. The
legacy runner's latest-chunk start boundary also avoids replaying historical
chunks as new incidents, but may skip footage during the outage.

Tests added here simulate journal interruption while active and below threshold:
no observation duration is borrowed, old payload bytes stay unchanged, a fresh
session and event are created, and an old incomplete incident is not silently
marked complete. These are synthetic process-lifetime boundaries, not a real GPU
crash test.

Safe future recovery requires a specified and validated source cursor/time gap
contract, persistent tracker/identity evidence (or explicit split/link policy),
and remote-session recovery that preserves acknowledged request sequencing and
missing media. None can be inferred from a reused track ID. Therefore this patch
does not implement automatic event merging or uninterrupted tracker recovery.
