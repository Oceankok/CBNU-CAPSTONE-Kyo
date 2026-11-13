# Continuous-event prototype (not enabled in the live runner)

This branch isolates the approved direction from unresolved operational choices.
It does not change detector weights, inference inputs, the UI, or the live runner.

## Current behavior and reproduced boundary

`ViolationTracker` reports once for an uninterrupted same-track/PPE streak.
A single missing person assessment deletes that streak, even if PersonTracker
keeps the ID, allowing a fresh event after another three seconds. PendingClips
uses the first candidate interval plus two seconds on either side; it does not
extend to the eventual end of the violation.

## Opt-in lifecycle component

`EventLifecycle` is a pure state machine. Its explicit grace parameter has no
production default. The qualification threshold remains three continuously
observed seconds; a missing/unknown sample before qualification resets that
qualification. After qualification a short same-ID gap can preserve the event.
The gap count/duration is reported separately, not claimed as observed violation.
A different track ID is never linked automatically. Explicit departure, positive
PPE evidence, scope change, evidence-mode change or a caller reset closes the
old event. The last positively observed timestamp is the end; a timeout does not
invent positive observations during the missing interval.

The caller must provide policy scope consistently and distinguish actual ROI
exit from unknown observations. It may use `departed_tracking_ids` only when
actual departure is established, not merely on a single missing detection.
Duplicate capture timestamps are no-ops; reverse timestamps are rejected so the
caller must explicitly finalize/reset on stream discontinuity. Progress events
are local signals, not a requirement to perform an HTTP write on every frame.
No new rule-based score or automatic confirmation is added.

## Integration gates

- Missing grace is chosen after measured 0.5/1/5-second scenarios; these tests do
  not select one as a production default.
- The current five-missing-frame PersonTracker may issue a new ID before a long
  grace expires. No identity reconnection is implied by a long grace budget.
- Preserve one initial event registration and preventive broadcast at threshold.
- Keep legacy immutable submission contracts; continuous events need an opt-in
  owner/revision/idempotency-protected lifecycle API.
- Segment metadata needs independent capture intervals and stable sequence IDs.
  Total encoded file duration, including pre/post-roll, must not exceed the
  configured limit (initial validation candidate: 60 seconds).
- Existing UI can select multiple video items, but does not provide a continuous
  playlist, ongoing-state display or guaranteed refresh for new segments.
- The user selected automatic judgment after event closure using the whole
  interval, with later administrator correction. Preventive broadcast remains
  at the three-second threshold. Automatic judgment remains OFF until accuracy
  validation; this component does not implement or activate it. Ongoing events
  must not become final judgments from an early partial clip. Merge and missing
  segment handling still require explicit backend contract tests.
- Raw spool quota, age and cleanup/restart behavior must be verified. Never
  expose unredacted source clips. Memory expansion alone is not a solution.

No live server, database, camera or GPU test has been performed for this prototype.

## Pure protocol payloads and synthetic integration

`continuous_payloads.py` maps transitions into the opt-in backend Start/Update/
Segment bodies. It performs no HTTP calls. Start keeps the event ID, policy scope
is represented by a deterministic hash, update sequence is caller-supplied, and
close requires an explicit completeness/segment manifest. Verified confidence
is a cumulative model-score minimum, not a rule score or probability of a
violation. Review-only candidates continue to carry null confidence.

A local synthetic integration test feeds 64.9 seconds of positive observations
with one missing detection and continuous source video. It observes one start
and one close, encodes the exact [-2,66.9) evidence interval into 60-second and
8.9-second MP4 files, and verifies successful-ack raw-file deletion. It does not
exercise HTTP, real camera identity stability, GPU detection, face redaction,
Windows, or the UI.

Before enabling any live runner path, a durable producer journal must atomically
preserve lifecycle state and pending start/progress/close bodies. A failed HTTP
call must not lose a start or close transition. Same-sequence replay must use the
same body, and a producer restart must not reuse old track identities. Segment
mapping must be durable so a missing raw file, TTL expiry or quota refusal can
be reported as incomplete evidence instead of being silently ignored.

The current spool is a single-stream primitive. An event-specific writer with
exact pre/post-roll is the first integration candidate; concurrent events need
an explicit process/camera-wide byte budget and configurable admission bound.
Do not attach a whole camera-wide segment to a shorter event: doing so would
include unrelated source footage outside the requested evidence interval.
