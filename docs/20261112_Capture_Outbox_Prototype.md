# Private durable capture outbox prototype

`capture_outbox.py` is isolated from runner, network, frontend and model. It owns
a private SQLite journal and nonblocking process lock. Calls must be serialized
on its creating thread. Windows lock behavior still needs Windows validation.

Transactions preserve:
- Never-reused event IDs, new producer session per process, full fixed camera
  snapshot (IDs or dictionaries with camera_id plus role/calibration/range data).
- Start/progress/close/incomplete sequence, idempotency key, canonical JSON bytes,
  digest and verified-remote-receipt acknowledgment. Exact retries read persisted
  bytes, not a newly inferred payload. One pending head per event prevents reorder.
- Segment-to-event/camera ownership, exact private relative path, byte count,
  source creation time, capture range, disposition, and cleanup obligation.

Opening the journal turns old active events into durable interrupted records.
IDs cannot be reactivated; old track IDs must receive new event/session IDs.
Previously enqueued requests retain exact retry bodies. An adapter must replay
those in sequence and append an appropriate incomplete report, never regenerate
an altered request using the same idempotency key. Recovery interruption itself
is a durable ledger obligation, not an automatically synthesized API payload.
A process interrupted before start was enqueued can persist an incomplete record
at sequence zero; the adapter decides whether this is local-only or a server
report. This module deliberately does not impose an unconfirmed HTTP contract.

Reconciliation checks finalized mapped files for existence, regular-file type,
single link, size, explicit TTL and clock rollback. A mismatch atomically records
incomplete evidence plus cleanup obligation. Raw paths are constrained to
`<uuid spool directory>/<uuid segment>.mp4` under the configured private root;
symlinks/absolute/traversal paths are forbidden. Same-user malicious concurrent
filesystem mutation is not a supported threat model. Raw file contents are not
hashed here; size equality is not a cryptographic integrity claim.

Cleanup remains SegmentSpool's responsibility, using its receipts/retry-only
cleanup state. The outbox never unlinks arbitrary raw paths. Mark verified private
upload receipt before spool acknowledgment/deletion, then acknowledge cleanup only
when the source is absent. This avoids treating intentional source deletion as a
missing-evidence failure on restart. This is NOT a public-media approval gate.
Missing/expired evidence cannot be restored merely by a later upload receipt.

All body/record/SQLite page/source-age limits are explicit. SQLite max_page_count
bounds the database, not total process memory or the rollback journal: reserve up
to another database-size budget for rollback plus filesystem overhead outside
this component. Capacity failure raises and rolls back; callers must fail capture
closed rather than drop a report. No auto-purge means finite lifetime admission;
verified archival/compaction is required before operating indefinitely.

Live blockers: producer-to-ledger transaction boundaries, new API contract
adapter, bounded upload transport/retry, atomic capture discovery integration, durable periodic
maintenance scheduling, redaction/publication gate, API manifest acknowledgment
and final-close ordering, safe archival, actual machine validation. Before creating any raw files, caller must commit `begin_capture` to persist
the event/camera/spool-directory owner. Restart scans these interrupted ownership
records and persists cleanup obligations for partial/unbound MP4 sources. No
association can be recovered for files created before ownership was committed.
The adapter must open/reconcile this ledger BEFORE SegmentSpool startup cleanup
and join spool receipts; reversing that order loses individual orphan identifiers. Do not connect this component to live runner before those are addressed.


Source reconciliation runs before pending selection. If a queued complete close
becomes stale because the event is now incomplete, its exact bytes remain intact
but are withheld from pending and exposed by blocked_terminal_requests. The caller
must reconcile remote state/unknown receipt before resolving the sequence; it must
not mutate or blindly retry the close. This module intentionally does not invent a
server-supported cancellation/replacement protocol. Only pending() is a send queue;
enqueue() return bytes acknowledge local persistence, not permission to transmit.

If orphan inventory reaches the configured record cap, recovery updates existing
capture/event rows with an aggregate `reconciliation_required` obligation instead
of making the ledger inaccessible. `recovery_obligations()` identifies the exact
owned spool requiring full receipt/inventory reconciliation. The aggregate remains
until an external reviewed recovery/archival workflow resolves it; missing
individual rows must never be interpreted as complete evidence. SQLite/database
or actual filesystem exhaustion can still make writes fail and requires operator
recovery, not silent continuation.

Network contract: raw-upload response loss may yield HTTP 409 on retry. A caller
must query existing event media/request bindings and verify event/camera/request/
media identity before acknowledging private custody. Do not invent a new request
ID or retry blindly. A server `processing` response is private custody only, never
public/redacted-ready evidence. This module trusts the caller-provided verified
receipt and makes no HTTP requests. Transport upload snapshots require their own
additional disk reservation beyond source spools and SQLite rollback budget.
