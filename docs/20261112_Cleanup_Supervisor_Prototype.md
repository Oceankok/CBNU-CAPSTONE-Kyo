# Opt-in cleanup supervisor

`cleanup_supervisor.py` bridges a durable CaptureOutbox with already-owned
SegmentSpool instances. No live runner, scheduler, network, or public-media output
is connected. The caller serializes capture/upload/cleanup and stops an expired
in-flight upload, including any transport snapshot, before cleanup. This module
owns neither transport snapshots nor their disk budget.

## Two distinct outcomes

1. Verified private delivery: the delivery ledger callback must verify exact
   event/camera/segment/request/media/content binding and private custody. Only
   literal True permits successful-delivery acknowledgment and source removal.
   Server processing/private custody is NOT public-ready or complete evidence.
2. Explicit TTL/other source loss: outbox incomplete/disposition/cleanup intent is
   committed before source discard. No delivery receipt is fabricated. Even a
   claimed delivery row whose callback cannot confirm custody expires through the
   incomplete privacy-discard route when explicit TTL is reached.

Neither route publishes data. Reports contain segment IDs and outcome/reason,
not raw source paths. Retention numbers remain explicit in the existing outbox
and spool configuration, not new defaults.

Only known segment manifests with matching owned-spool path, byte count, source
creation time and interval can be removed. Unregistered files, mismatched bytes,
symlinks, orphan inventories, overflow and aggregate recovery obligations are
held for manual recovery. They are never guessed away to satisfy retention.
This may require operator intervention to meet privacy deadlines; a held result
must be surfaced, not treated as successful cleanup.

Spool discard receipts and outbox audit survive unlink failure. Cleanup completion
is acknowledged only after absence is verified. Repeating a pass is safe. Start
outbox recovery BEFORE opening interrupted spools; spool startup can remove its
own partial/orphan files, so the outbox must first retain their ownership record.
Supervisor intentionally does not open/restart spools itself. An unknown orphan
without a sufficient manifest remains manual recovery.

The delivery verifier callback belongs to the parent's durable delivery ledger.
It must not simply trust the string `verified_upload_receipt`; it must validate
persisted binding evidence, including unknown-response recovery. If the callback
fails or returns false before TTL, raw evidence is held. If audit persistence
fails, the pass raises before deleting any source. Existing standalone spool TTL
pruning also records dispositions; the integrated scheduler must reconcile those
into the outbox rather than assume a successful upload.

Tests cover two cameras, duplicate cleanup, restart, unlink retry, unknown custody,
TTL incomplete disposition, orphan/manual hold, changed source, missing source,
and audit failure preserving source. Real Windows cleanup/ACL/file-lock behavior,
crash injection, process supervision and operational timing remain live gates.

Unbound partial-encoder receipts are now joined to the precommitted capture owner.
The outbox stores exact receipt bytes plus event/camera/spool identity and marks
the event incomplete in one transaction before the supervisor acknowledges the
bounded spool receipt. Both .part and .mp4 must already be absent; this path does
not delete any source. Unknown ownership, receipt overflow, existing raw files or
full audit capacity remain held. Repeated receipt delivery is exact-idempotent,
including restart after ledger commit but before spool receipt acknowledgment.
This prevents an already-discarded partial file from permanently consuming a
spool reservation merely because no finalized segment mapping was ever created.
