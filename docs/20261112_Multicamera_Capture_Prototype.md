# Full-incident multi-camera capture prototype

`inference_worker/multicamera_capture.py` is opt-in and **not connected to runner,
backend, UI, or any live camera**. It is a serial-owner, in-memory coordinator,
not a restart-safe event ledger. No production defaults are selected.

## Contract

- Caller supplies a fixed camera snapshot when the incident is discovered. Each
  camera starts its own writer immediately by replaying the private prebuffer.
  Main and reference cameras receive the same requested interval.
- `CameraClock.offset` maps camera source timestamps into the common event clock;
  finite externally verified error bounds must meet explicit `max_clock_error`.
  This is not a clock-calibration implementation. Source PTS is not UTC.
- All retention, pre/post-roll, discovery delay, gap, output FPS, fragment length,
  byte budgets, frame counts, concurrency, and clock-error limits are explicit.
  For start−2/end+2 with discovery at start+3, configure at least five seconds of
  prebuffer. Byte capacity must also accommodate those five seconds on *all*
  cameras; duration alone never guarantees coverage.
- The latest source frame at or before the required start seeds the output grid,
  only within max_input_gap_seconds. A future frame is never shifted backward.
  Status seed metadata records actual common-clock `source_sample_at`, resampled
  `output_sample_at`, `hold_seconds`, and separate `clock_error_bound`. This is
  sample-and-hold output, not proof of an exposure exactly at the output timestamp.
  Original source-to-source gaps and source-to-end gaps remain checked, so seeding
  cannot disguise missing observations. Off-grid end boundaries fail closed.
  Allow prebuffer duration/bytes for the predecessor as well as pre-roll/discovery.
- `end()` establishes an exclusive capture boundary including post-roll. It does
  not stop capture immediately. A subsequent frame at/beyond that boundary lets
  the writer finalize exactly there. Parent must preserve pending-close intention,
  upload manifests, then close the server event. No HTTP operations occur here.
- If end arrives after the writer has consumed that boundary, evidence is marked
  incomplete and capture aborted; previously generated raw fragments must NOT
  be published as exact incident evidence. No implicit trim or broad attachment.
- Per-camera reasons cover missing prebuffer, late discovery, unverified clock,
  clock regression, source gaps, quota admission, encoder failure, TTL/discard,
  missing post-roll, and late end. A missing camera never silently becomes complete.
  `status.complete` describes capture only, NOT upload/redaction/review readiness.

## Bounds and ownership

One coordinator owns an initially empty dedicated private root, with all calls
serialized by its caller. Reservation is `snapshot camera count × spool.max_bytes`
per admitted event and is retained for completed/incomplete incidents until raw
files and discard receipts are drained. Each spool's reservation includes its
journal metadata. `release` removes its closed empty spool metadata before freeing
reservation. Multiple coordinators/processes are NOT globally coordinated.

Prebuffer time/bytes/number of frames are bounded, with eviction before copying.
Writers additionally retain at most one input frame per admitted event-camera;
this is bounded by max_events × configured camera count × max_frame_pixels × 3.
Encoder/OS memory is outside those application-frame byte caps. Number of completed
fragments is bounded by each spool's max_segments; reaching capacity fails closed.
No throughput, GPU, Windows, or two-physical-camera validation is claimed.

`maintain(now=common_clock)` must run periodically even with no incoming frames to
catch missing post-roll and TTL. All timestamps must be finite. Reconnect/clock
calibration changes require a new validated capture session; this prototype has
no reconnect method and must not reuse old camera offsets after a reset.

## Hard live blockers

Durable event↔camera↔segment mapping, monotonic/idempotent report revisions,
restart recovery/outbox, source identity/calibration expiry, coordinated process
ownership, redacted-only output, and integration with final manifest/close are
not implemented. Restart refuses a nonempty root rather than reconstructing
unproven event ownership. An upper durable recovery layer must reconcile/discard
raw sources and report incomplete evidence. `close()` stops active capture but
keeps completed private sources for spool recovery; it is not data delivery.

Synthetic tests use two time-offset cameras and real ffmpeg output. They cover
same full interval, missing prebuffer, clock error, overlapping admission, late
end, source gap/TTL, bounded buffer, off-grid end, and reservation release.

**Publication gate:** This prototype supports no external upload or publication
before the whole incident's final end and all camera ranges are validated. Early
complete 60-second fragments are PRIVATE provisional sources, not publishable
manifests. An `incomplete` report cannot revoke leaked footage. `status()` segment
records must not be forwarded directly to public media APIs. The future adapter
must enforce a separate public-video block, not just server `finalizable` state,
and must trim/reject any range outside the final request before redaction/upload.
The camera calibration is an immutable epoch for the entire capture; changing
`cameras`, offsets, or clock epoch externally is unsupported. Reservation failures
are only in-memory here and need durable incomplete outbox records before live use.

Independent review correction: periodic `maintain` also expires raw prebuffer
frames for idle cameras, and shutdown clears all prebuffers. These guarantees
still require the owner to call maintain on schedule. Camera frame-phase
misalignment now uses bounded predecessor sample-and-hold, explicitly distinguished
from capture time in seed metadata. Synthetic tests assert matching duration across
phase-offset cameras, refusal of future/stale seeds, and original-gap checking.
Real asynchronous cameras still require actual FPS/clock/throughput validation;
the two-camera tests are not a claim of production readiness.
