# Continuous processor capture-context binding

The opt-in continuous reader accepts server stream/chunk metadata `capture_context_id`, `capture_session_id`, `assignment_version`. All three must be present together with valid identifiers and a positive integer version. If `source.require_capture_context=true`, context-free streams are rejected before decoding. Default false retains legacy compatibility, but a context advertised by a stream must always match every chunk.

The first observed context tuple per camera is immutable for the reader. Changes to primary OR reference context, stream identity, missing context, or mismatched chunk metadata fail closed. The normal runner finally path interrupts/closes the existing local lifecycle and frees resources. A new runner creates a new tracker/outbox session; old durable event requests retain their original context and are never rebound to the new zone.

Before primary frames are processed, the runner binds the primary `capture_context_id` to the lifecycle journal. Start payloads persist it in the durable outbox. Once any observation has been processed, a journal cannot acquire a different attribution. A legacy None binding cannot be silently upgraded in-place.

This is metadata consistency, not cryptographic source verification, physical relocation proof, or camera-clock calibration. Server authorization, issued context registry, source/session ownership and event interval validation remain server responsibilities. Existing explicit camera calibration is still required. References retain independent context consistency but do not determine the primary event's zone.

Legacy run_server/run_chunks event builders are unchanged. With the server's capture-context enforcement enabled they cannot submit context-free events; use the matching opt-in continuous runner and compatible sender. Model, weights and tracker implementation are untouched. No operating deployment, real hardware or real-camera tests have been performed by this change.

## Stop acknowledgement tail drain

The stream-list contract may retain stopped streams while their final uploaded
chunks remain buffered. A bound reader selects its existing stream ID even when
new-context streams for the same camera coexist. It consumes only sequences up
to the acknowledged `final_sequence`, which must equal `last_sequence`; the
final boundary cannot change or revert to live. A fresh reader never chooses a
`capture_stopped: true` stream. The poll that consumes final chunks returns its
ready frames normally. The subsequent poll raises `session_stopped` before any
camera is decoded, so no new-context observations attach to the old event.

This is bounded tail draining, not offline replay. If unconsumed buffered old
data expires, the missing stream fails closed. A consumed, acknowledged source
is immutable EOS and no longer needs its stream-list row to remain present.

## EOS-aware multi-camera tail (follow-up)

Only exact final-sequence consumption authorizes a camera EOS. Timeouts, an empty
chunk response, and missing streams never authorize it. EOS cameras cannot push
frames again. The mux computes its watermark over remaining cameras; after all
EOS it releases the final ordered queue, then the next reader poll signals
`session_stopped`. Already-consumed sources are skipped rather than replaced by
new-context streams. Unfinished sources retain gap, buffer, stall, and raw TTL
limits. All limits and new-reader live boundaries are unchanged.

The runner tells capture about EOS only after every frame of that camera has
actually been emitted from the mux and processed from the returned batch. Active
unfinished capture and any subsequently discovered event explicitly record
`source_ended` for that camera, preserve sealed valid fragments, and never claim
complete reference evidence. A previously complete capture is not invalidated.
Primary detections continue after reference EOS: the warm-prebuffer condition
excludes explicitly ended references instead of converting primary violation
assessments to an empty/normal result. Reference-only frames do not invoke the
primary detector or update its PPE lifecycle. `session_stopped` is retained as
the journal shutdown reason; outbox pending work is not deleted on shutdown.

If primary stop is acknowledged while other configured sources remain live,
the reader freezes each live reference's advertised `last_sequence` from that
poll as a finite **reader-local drain cutoff**. This is NOT a sender stop, real
source EOS, or backend authorization to move that camera. Real stopped sources
still drain their true finals. No cutoff follows later uploads/new contexts.
The cutoff is reported to evidence as `reference_drain_cutoff`, never as a real
source stop. All bounded inputs drained ends the old runner with
`primary_source_ended`; a fresh runner must select the new primary context.

On emitting the final primary frame, the runtime immediately closes its PPE
lifecycle at the unchanged last-primary observation time, even if reference
frames remain. Reference-only frames never extend this event time or create a
new violation. A complete capture is not guaranteed when a source ends before
required post-roll, files expire, or unconsumed data is unavailable. These are
explicit incomplete evidence outcomes, not silently shortened success.
