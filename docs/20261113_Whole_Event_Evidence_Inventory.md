# Whole-event evidence inventory (diagnostic preparation)

The existing administrator preview and stored shadow results now include
`whole_event_evidence` version 1. This is an additive read-only metadata inventory;
no automatic review writer, threshold default, schema migration or new activation
switch is added.

The inventory distinguishes:
- event lifespan, producer-reported observed time and observation gaps;
- each primary/reference camera's registered segment manifest;
- union coverage clipped to the event lifespan (overlapping or pre/post-roll
  intervals are not double counted), with uncovered ranges;
- the limits of that evidence. Full manifest coverage does not establish decoded
  visual evidence, PPE classification, calibrated confidence or clock correctness.

Existing finalizability checks still independently require delivered, redacted,
measured videos and complete camera capture reports. The summary intentionally
has no storage paths, raw start payload or node session credential. Legacy events
report `continuous_provenance_missing`, not fabricated zero gaps.

The summary is included in the diagnostic fingerprint and therefore stored by
existing explicit shadow requests. Changes in observation/gap metadata invalidate
that diagnostic fingerprint. Existing saved shadow results remain historical and
are not retroactively recalculated. A matching fingerprint is not authorization,
content-integrity proof, or a substitute for transaction-time checks.

Still required for actual automatic confirmation: validated whole-event decision
policy and per-model/PPE calibration, defined treatment of unknown/occluded
observations, independent full-evidence evaluator, atomic writer preserving human
review/consent/retention/statistics rules, and actual device/PostgreSQL validation.
No calibrated probability or automatic eligibility is inferred from coverage.

## Remaining evaluation inputs (not fabricated by this implementation)

1. A manually labelled set of complete event episodes, including negatives,
   ambiguous/occluded views, camera transitions and interrupted uploads.
2. Out-of-sample separation by recording/session, not arbitrary adjacent clips of
   the same episode. The old three-second fragmentation must not multiply samples.
3. An explicitly defined whole-event score producer. The current source's cumulative
   minimum verified PPE confidence is metadata, not a validated probability. Reference
   camera recording availability alone does not provide reference-camera inference.
4. A separately approved threshold and acceptance criteria per model/PPE, with
   sample counts and false-positive/false-negative consequences evaluated.

The offline evaluator consumes supplied labels and scores without producing them.
It does not authenticate labels, validate score provenance, choose a threshold,
activate review writing, or establish recall for episodes that never generated an
 event. Until these are available, moving metadata completeness directly into an
 automatic review writer would overclaim the underlying evidence.
