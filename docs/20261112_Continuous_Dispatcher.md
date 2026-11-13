# Opt-in dispatcher integration (not live enabled)

`ContinuousDispatcher` requires explicit `enabled=True` and an already exclusively
owned CaptureOutbox. It is not imported by the runner and starts no background
thread or scheduler. All calls must be serialized under the outbox lifetime lock.

Metadata is selected from the immutable outbox head per event. Exact stored JSON
bytes, not reconstructed payloads, are sent to fixed endpoint routes. Event and
session are checked before send; errors never acknowledge a request. Global
outbox sequence is distinct from API update/segment/camera-report sequence.
Camera reports bind the route via immutable `camera_id:unique_suffix` idempotency
keys and the event camera snapshot. No arbitrary URL is accepted.

Upload plans are bound to the event/session/camera/segment and persisted in a
second bounded SQLite ledger. Each pass checks for a historical content-bound
receipt before attempting an upload. A first receipt 404 on a queued record may
start one upload; `attempted` is committed before network transmission. After an
ambiguous failure or restart, receipt 404 remains unknown and cannot trigger a
second upload. A 403, mismatch or outage blocks progress without discarding the
source or inventing another request. Unavailable local evidence is held manual.

Accepted receipts are persisted before a private-delivery cleanup obligation.
The cleanup supervisor must receive `dispatcher.cleanup_receipt_verified` and
verify the same persisted binding again. Metadata/private-delivery acknowledgments
never mean redaction complete or publicly accessible. Both API acceptance and
privacy-expiry disposal preserve their separate meanings.

## Limits and live gates

Configure outbox/delivery DB byte and record ceilings, rollback-journal allowance,
source TTL, camera/global spool reservations, a snapshot allowance per upload,
HTTP per-I/O/total deadlines and maintenance cadence explicitly. Existing ledgers
larger than a reduced byte/record budget fail closed. No production numbers are
chosen by the unit tests. SQLite exhaustion still needs operator recovery.

Remaining integration gates: atomic transition-state plus outbox creation in the
actual runner; authoritative camera snapshot reconciliation before reference
encoding; clock epoch/calibration/reconnect policy; periodic maintenance and hard
worker cancellation; source/receipt/orphan recovery ordering; actual GPU Windows
2-camera throughput and redaction quality. This module does not silently resolve
blocked terminal requests or recovery-capacity aggregates. They require explicit
remote-state/manual reconciliation. No frontend or detection model changes.

Tests exercise two camera uploads, repeated ticks, lost-response/restart recovery,
unknown receipt no-resend, TTL failure, exact metadata retry and reduced disk
budget rejection. An isolated real API run also verifies start/manifest/close,
two source MP4 uploads and digest-bound receipts, duplicate-pass suppression and
no public media URLs while processing. Real face redaction is not run by these
integration checks.

Metadata acknowledgments additionally require an exact `accepted_command` match
(kind, stored session, API sequence, nullable camera ID). A 200 with a different
command or missing correlation field leaves the durable request unacknowledged.
This is an additive paired-backend contract; old servers fail closed rather than
silently accepting unbound acknowledgments. The outbox body is retained, not
removed, after successful acknowledgment.

The isolated API check also sends both camera capture-complete reports after the
lifecycle close. The outbox permits camera reports in closed state without
reopening the incident; manifests and progress remain forbidden there. The
dispatcher refuses complete capture reports if its local event is no longer
closed, and an `incomplete` command can only carry `complete:false`.

Completed local source files are deleted only by the separately invoked cleanup
supervisor after the durable receipt callback validates their binding. The
integration test verifies both source files disappear while the backend media
remain private/processing. TTL disposal tests separately verify incomplete audit
state, so successful private custody is never inferred from source deletion.

Uploads wait for a durably acknowledged terminal close command: either closed /
complete true, or incomplete / complete false. Both require the same event and
session binding plus an immutable final-range plan. The latter preserves only
verified completed source fragments, not a successful overall incident. An
active event cannot upload merely because `final_range_verified=True` was set.
This conservative implementation buffers until final closure; long-event spool
pressure remains a measured operating gate, not permission to bypass retention.
