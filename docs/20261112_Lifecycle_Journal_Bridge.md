# Opt-in transactional lifecycle bridge

`lifecycle_journal.py` computes one frame's transitions on a deep copy of
EventLifecycle. `CaptureOutbox.apply_lifecycle_batch` commits all resulting starts,
progress commands and pending-close intentions in one SQLite transaction. Only
successful persistence replaces the live lifecycle reference and API sequence map.
Failure leaves the original tracker unchanged, so the same frame may be retried.
There are no detector, frontend, live-runner, remote, or OS changes here.

All grace, threshold, sample-gap, camera snapshot, and model-version inputs are
explicit. A threshold of three seconds preserves the existing initial start/
broadcast wire payload; no broadcast is performed by this bridge. Its exact start
body is immutable in the outbox before delivery. Confidence retains the model
score meaning; no new score or automatic confirmation is introduced.

Camera snapshot is local expected ownership metadata. Start API body remains the
existing schema and MUST NOT include an extra cameras field. The server chooses
its camera group and returns cameras in its start response. The parent runner /
dispatcher must compare that response exactly to expected configuration before
using it. A mismatch must stop/incomplete the event, not expand camera collection.
Before start ACK, reference prebuffer collection is limited to explicitly
configured cameras. This bridge does not implement that network-side check.

Input timestamps must already be validated common UTC seconds. Decoder/source PTS
must not be passed as UTC merely because it is numeric; clock validation and
conversion are parent-runner responsibilities.

## Independent sequence spaces

A lifecycle head stores API update sequence separately from the global outbox
command sequence. Manifests and camera reports may occupy global command slots
without advancing lifecycle API update sequence. A pending close stores its final
transition, producer session, and next API sequence, but produces NO HTTP close.
Post-roll completion, durable manifests and explicit completeness/count precede
parent finalization. The finalizer must atomically consume this intent together
with its final close command; the outbox finalizer consumes the intent as described below without guessing
completeness or publishing raw media.

On process restart, old active events and pending-close intents become interrupted.
Exact pending start/progress command bytes remain available for protocol recovery.
The new bridge always starts with an empty tracker and fresh producer session;
track IDs cannot silently reconnect to old events. Pending-close transition bytes
remain available for recovery/incomplete reporting, not automatic continued capture.

## Transaction ownership

Outbox public mutations now use a nested-aware transaction context. Nested create /
enqueue calls share an outer BEGIN IMMEDIATE transaction instead of committing it
mid-frame. All calls remain single-thread / single-owner serialized. Network I/O
must not occur inside this transaction. SQLite FULL synchronous and the existing
exclusive process lock retain the original outbox durability model.

Prequalification state and last decoder frame are intentionally not replayed after
restart. A frame without transitions still validates persistence availability before
replacing its in-memory copy. Capacity/admission errors fail closed; no event in a
multi-person frame is published while another event from that frame fails to save.

Tests cover atomic multi-event admission failure, mixed close/progress rollback,
reference replacement only after commit, exact start retry after restart, pending
close durability, no immediate HTTP close, independent sequence spaces, and reset.
Operational process-kill/actual-camera tests and parent finalizer integration remain
required. This module imposes no new blanket restriction on already-redacted media
visibility; public output policy belongs to the validated server/media adapter.


## Finalization and bounded progress cadence

`progress_emit_interval_seconds` is explicit and required (zero preserves every
frame); no operational cadence is chosen here. Tracker observations update every
frame while only emitted cumulative progress snapshots advance API sequence and
consume command records. Start and close are immediate. If close's last-positive
exceeds the last emitted observation, that final progress is written first in the
same transaction, followed by the pending-close intent with the next API sequence.
The runner must ACK this metadata before posting a segment whose end extends the
server's prior required range. Even a locally incomplete incident can persist this
final observation without promoting its evidence/completeness state.

`finalize_pending_close(event, complete, final_segment_count)` first reconciles
sources, then validates same-session pending ownership and all registered manifests
as acknowledged, same-session sequence prefix 0..N-1. It uses the stored API sequence,
enqueues at the current global command sequence and marks the intent finalized in
one transaction. A complete=false wire close uses outbox kind incomplete. Identical
retries return saved bytes; altered final parameters fail. Old interrupted intents
require remote-state recovery rather than automatic finalization. Existing finalized
bytes remain retryable after restart but only pending() authorizes dispatch.

`mark_incomplete` persists zero-segment failures too. Incomplete/interrupted events
may append only camera reports explicitly marked state=incomplete, never a new
successful report. Complete finalization rejects a locally incomplete event.
The method still relies on the parent for validated capture completion/post-roll;
manifest ACK alone is not proof that all required camera evidence was captured.
