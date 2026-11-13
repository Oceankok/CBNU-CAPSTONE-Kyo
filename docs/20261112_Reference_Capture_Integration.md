# Whole-event primary and reference capture (not live enabled)

The owner selected full-interval reference footage, not an initial short reference
clip. Frontend and detector models remain unchanged. This extends the isolated
producer prototype; it is not a deployed fix.

## Start discovery and time

At the three-second qualification point the server must return the immutable
primary/reference camera snapshot immediately. Reference workers must not wait
for the first completed 60-second segment manifest. Capture begins at the same
required event start minus two-second pre-roll on every camera; since discovery
happens after qualification, approximately five seconds plus discovery latency
must already exist in bounded per-camera prebuffers. Insufficient history is an
explicit incomplete interval, never a successfully shortened clip.

Capture timestamps must map to a shared event clock using an explicit verified
clock offset and uncertainty bound. A source media PTS is not automatically UTC.
The permitted uncertainty is an explicit configuration pending measurement, not
a silently chosen production default. Camera reconnects, clock jumps and stale
clock calibration invalidate that mapping. Discovery polling uses bounded,
observable latency and also finds closed events awaiting post-roll; otherwise a
restart immediately after closure could miss their outstanding capture work.

## Evidence state and ordering

Each camera reports cumulative captured ranges, missing ranges with reasons,
clock metadata and monotonically sequenced idempotent report bodies. Captured
means encoded source coverage, not redacted/publicly usable evidence. The server
requires all expected manifests and redacted media ready in addition to complete
camera coverage before evidence can become finalizable. Judgment remains OFF.

The event is registered and broadcast at three seconds. Lifecycle closure fixes
the final positive timestamp, but evidence collection continues through its
post-roll. Manifests must precede a complete lifecycle close under the existing
API contract; therefore the integration must journal a pending-close intention,
collect post-roll, register all manifests, then deliver the durable close body.
No additional event or broadcast is created while waiting for evidence.

If the source has already advanced beyond the final requested interval when a
close arrives, the producer must trim to the exact boundary or explicitly reject
completion. It must not publish unrelated trailing frames. Camera ranges and
segment boundaries are independent facts even when the requested ranges match.

## Resource/privacy boundaries

Prebuffers have explicit per-camera and process-wide byte/time limits. Concurrent
events reserve bounded spool capacity before starting encoders; admission and
raw-file quotas are configurable and observable, not a fixed unmeasured promise
about RTX 5080 throughput. A reservation remains held while files or cleanup
obligations remain, not merely until logical event closure. Encoder memory and
GPU upload/redaction queues require separate measured limits.

Private source files are never public URLs. Raw retention age, maximum attempts,
disk quota and periodic maintenance are explicit. TTL/quota/source gap/stalled
encoder/late discovery/clock uncertainty/restart must create durable incomplete
records. Disposal receipts alone do not prove a file was deleted. Redaction
failure keeps publication blocked and does not silently substitute raw media.

## Outstanding live gates

The in-memory coordinator cannot survive restart by itself. An atomic producer
outbox must durably bind lifecycle transitions, event-camera memberships,
reservations, source-segment identities, exact API bodies, acknowledgments and
cleanup obligations. Startup marks interrupted capture incomplete rather than
reattaching old track identities. Recoverable upload retries reuse identical
sequence/body pairs. API session ownership must explicitly support reference
workers or constrain deployment to one owner coordinator.

Before live enablement: isolated API contract and idempotency tests; two-camera
synthetic full-range MP4 tests; missing prebuffer and clock-error tests; global
quota and release tests; post-roll/late-close tests; restart/outbox tests; redaction
and upload failures; then authorized Windows/two-camera/GPU measurements. No
operational grace, retention, total-event-duration or concurrency value is
selected merely by passing synthetic tests.

### Publication and clock-epoch safety

Marking an event incomplete does not authorize publishing out-of-range footage.
A delayed close may place the final end before an already encoded segment end.
The prototype performs no upload. A future uploader must either keep all media
private until the final range is verified or use a proven safe publication
watermark and exact trim/replacement before disclosure. A finalizable-only gate
is insufficient if a direct media URL still serves the earlier source range.

One capture uses an immutable camera-clock calibration snapshot. A new clock
epoch or reconnect cannot silently change offsets within a segment. Restart and
clock discontinuity require an explicit incomplete record and fresh mapping.

The pure `capture_payload` builder rejects out-of-range provisional segments,
false complete coverage and unverified clocks. The adapter must pass persisted
previous missing intervals; the function does not remember prior reports.
`shared_monotonic` is a time-basis label, not permission to serialize raw process
monotonic seconds as Unix UTC. The adapter must first apply its fixed, validated
shared-clock-to-epoch mapping, consistently for events, frames and reports.
An isolated TestClient check has exercised two camera complete reports, identical
retries, and discovery of closed but evidence-pending events; this does not test
network delivery, durable recovery or actual camera clocks.
