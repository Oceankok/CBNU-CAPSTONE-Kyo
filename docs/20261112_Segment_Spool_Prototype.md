# Private segment spool prototype

This opt-in component is **not connected to run_server, the API, or production**.
No detector, model, frontend, or running deployment is changed.

`TimestampSegmentWriter` consumes already-observed BGR frames and capture times.
It uses timestamp-based sample-and-hold at an explicitly selected output rate;
this may select/repeat observed frames but never changes detector input. An input
gap beyond the caller's explicit budget fails closed, rather than inventing a
continuous image. A final exclusive end boundary must align to the output frame
grid: the caller must choose a boundary covered by observed evidence, never round
past it silently. Frame dimensions are fixed, even, and explicitly pixel-capped.

The maximum complete file is explicitly configured, no greater than the existing
60-second processing cap. Pre/post-roll count inside that complete-file limit.
The writer emits only finalized segments with a stable segment ID, monotonic
sequence, exclusive capture start/end, FPS, frame count, byte count and creation
time. It does not create events, infer continuity, make review decisions, assign
scores, or associate two people. Event/segment ownership belongs to the future
API integration. Multiple events sharing one camera need a separate mapping and
reference-count policy; duplicating one writer per event is not recommended.

## Bounded resources and private sources

`SegmentSpool` requires explicit byte, segment-count, source-age and attempt
limits. It reserves enough space for both atomic journal copies and the lock;
the encoder writes its stdout through a byte-capped sink. There is no uncapped
encoder output file. Retained application state is one previous frame, bounded
pipes/chunks and bounded journal entries. Temporary conversion copies and ffmpeg's
internal memory also exist; this is not a measured OS-process memory guarantee.

Use a dedicated private directory, never a public media/storage URL. New files
are created with mode 0600 and a new directory with 0700 where supported. Existing
directory/ancestor permissions are the deployer's responsibility. Windows ACLs,
Windows ffmpeg behaviour and the deployed service identity are **not yet tested**.

One OS lock owns a spool. On restart, `.part` and unjournalled MP4 files in a
recognized spool are discarded, never submitted as valid evidence. Recognized
completed pending files can retry within their original age and attempt budgets.
Unknown directories, unexpected files, symlinks and invalid journals fail closed.
A corrupted journal is not automatically erased; operator recovery is required.

`mark_attempt(id)` persists the attempt before returning the private upload path.
The uploader must use an idempotent server key and bounded timeout, then call
`acknowledge(id)` only after verified receipt; that deletes the raw file.
`mark_failed(id)` releases the attempt and prunes exhausted/expired sources.
An in-flight attempt is not scheduled twice. Age expiration still overrides it.
The caller must invoke `maintain()` periodically even when capture/upload stalls;
there is no hidden background janitor. A hard kill may leave private files until
the next owner startup. Existing supervisor/system cleanup must cover a machine
that never restarts this component. These are integration obligations, not solved
by merely importing this module.

## Verification

Synthetic MP4 tests cover five seconds split 2+2+1, exactly 60+1 seconds, mixed
15/10-FPS input keeping seven seconds at 15-FPS output, decoded frame counts,
media+metadata byte cap, segment cap, source expiry, partial/orphan recovery,
last-attempt handling, duplicate acknowledgements, OS lock exclusion, source gap
failure, frame pixel bound and rejection of an unaligned final boundary.

No actual camera, GPU/redaction throughput, API upload or Windows execution was
performed. Production connection remains blocked on finalized lifecycle/API
contracts, periodic cleanup ownership and real two-camera load tests.

## Unfinished multi-event boundary

This prototype owns one camera stream and one active encoder. It does not trim
camera-wide segments to each overlapping event's exact permitted pre/post-roll
interval. Attaching an entire camera-wide 60-second file to a short event could
expose unnecessary footage and is **not authorized by this implementation**.
Per-event interval intersection, redacted-only delivery, segment ownership,
reference counting and a system-wide quota across several spool instances remain
integration tasks. The present byte cap is per spool, not global.

Encoder stdin writes use an explicit deadline with process termination to release
blocked pipes. Output is read in bounded chunks and never bypasses the sink's byte
limit. A synthetic child that refuses stdin verifies timeout and cleanup. The
caller must still run periodic maintenance and use bounded network uploads.

## Durable unavailable/discard receipts

`discard_receipts()` exposes segment IDs, known segment sequence, reason and
source size/time for expired/exhausted sources, restart partial/orphan cleanup,
missing/corrupt-size files, lowered quotas and failed active encoding. These are
**unusable-source/cleanup dispositions**, not proof that every physical unlink
succeeded. Normal pruning persists disposition and cleanup intent before unlink;
failed deletion stays cleanup-only and cannot be re-uploaded. Confirmed successful
uploads also persist cleanup-only intent before unlink, but do not emit loss
receipts. On restart, cleanup-only files are deleted without upload retries.

Receipts are bounded and durable. If the cap is exceeded, `overflow_count` and
`reconcile_required` remain set; identities evicted from this small receipt log
must be recovered by comparing the upper durable event↔segment manifest with the
current spool/receipt state. The upper event mapping is mandatory, especially for
an active segment or a machine failure before a file is journalled. A receipt is
acknowledged only after the caller durably records/reports incomplete evidence.
An overflow must never be treated as all clips delivered. No privacy-expired raw
file is retained indefinitely merely to keep an unlimited receipt history.

State/journal operations are serialized by an internal reentrant lock so a
capture thread and uploader cannot collide on `journal.tmp` or prune the final
allowed upload attempt between its persistence and in-flight registration. The
encoder sink thread uses no state lock; blocked stdin and sink completion have
bounded deadlines. There is still only one capture producer per writer.
