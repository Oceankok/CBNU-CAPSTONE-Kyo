# Opt-in sender backpressure and soft spool protection

Disabled by default. No GUI option, saved configuration, system setting or
operational deployment is changed by this candidate. Legacy sender behavior is
unchanged; the new controls apply to context_sender and its controlled capture.

## Explicit opt-in requirements

All of these must be supplied intentionally before launching:

- VIDEO_BACKPRESSURE_ENABLED=true
- VIDEO_SPOOL_THRESHOLD_BYTES: planned per-session spool budget
- VIDEO_SPOOL_RESERVE_BYTES: reserved headroom below that budget
- VIDEO_SPOOL_MIN_FREE_BYTES: free-space floor, separate from spool usage

Missing/noninteger budgets fail before camera launch. Require
0 < reserve < threshold and min_free >= reserve. Suggested values to discuss,
NOT defaults or automatic settings: threshold 1 GiB, reserve 256 MiB, min_free
2 GiB. This means capture requests a protective stop at 768 MiB current-session
usage or disk free <= 2.25 GiB. Choose based on bitrate, disk capacity, camera
count and recovery needs; these numbers are not validated hardware guarantees.

## Scope and limits

Usage sums files directly in the current session directory, including .part,
finalized raw MP4, timestamps, markers, journal and completion evidence. Other
session directories and prior failed outboxes are NOT added to this per-session
sum. Their disk use is reflected only by the shared filesystem free-space check.
No previous session, unacknowledged video or unfinished fragment is auto-deleted.

This is a **soft stop threshold with reserved headroom, not a hard byte cap**.
Both sender (including retry waits) and capture frame loop check it. The capture
loop is independent of blocked HTTP requests. FFmpeg's in-flight buffer and
final trailer can still grow the spool after the stop request; a blocked camera
read, stdin write or hung FFmpeg can delay or prevent graceful completion. No
forced kill, timeout-based stop acknowledgement or OS disk quota is introduced.
A mathematically enforced hard disk cap needs a separately configured OS quota.

On protective stop, capture uses the normal close/publish/completion-proof path.
Sender drains finalized chunks and only retires context after exact server ACKs.
Sender records a non-secret protective_stop_reason when its guard observes the
budget condition. Capture also logs its own protective reason. No timer alone
means capture has stopped. Failure preserves the raw outbox and active context;
recovery requires operator attention, not silent loss or forced unlock.

## HTTP retry behavior

Only chunk-upload HTTP 429 is retried. All other statuses, connection failures,
malformed Retry-After values and permission errors are surfaced without retry.
Each attempt reopens exactly the same file and sends the same sequence/time.
Unacknowledged content is not cleaned up. Existing acknowledgement-before-cleanup
ordering remains in effect.

Maximum five HTTP attempts per upload, with a 60-second monotonic retry
admission budget. Retry-After accepts nonnegative integer seconds or HTTP-date;
missing values use one second. A delay reaching/exceeding remaining budget fails
instead of retrying early. Individual requests receive a timeout capped by the
remaining budget. **Requests timeouts are not an absolute wall-clock deadline**;
OS/socket scheduling and read semantics can exceed this envelope. No hard
60-second process-termination promise is made.

Waits check spool and explicit stop markers every <= 0.2 seconds. A stop request
does not abandon captured evidence: waiting/draining can continue until ACK or
bounded failure. Exhaustion is explicit; server assignment remains locked until
valid stop proof and final sequence manifest are acknowledged.

## Privacy and verification

No raw retention TTL is increased by this sender patch. No automatic disposal
policy is invented. Failed raw outboxes may persist until authorized recovery or
cleanup; the operator must handle that privacy obligation. The capacity settings
are not permission to keep identifiable footage indefinitely.

Tests include default-off compatibility, explicit config rejection, Retry-After,
five-attempt bound, elapsed request budgeting, 429-only retry, original-file
reopening, .part accounting/free-space stop, pre-capture protection, and real
TestClient quota429 -> same-sequenceACK plus exhausted429 original/context
preservation. Synthetic media and isolated DB only. Camera, Windows/FFmpeg hang
behavior, physical disk pressure and PostgreSQL deployment remain unverified.

## Protective stop display
The capture completion record carries only whitelisted spool stop reasons.
The sender preserves that reason after validating completion evidence. GUI polling
refreshes child exit state before displaying confirmed ACK; a reason alone never
proves completed transmission. Unconfirmed exits continue to prohibit reassignment.
