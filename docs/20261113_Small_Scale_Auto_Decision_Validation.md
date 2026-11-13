# Small-scale demonstration validation (proposal, not measured accuracy)

## Scope
Use one fixed camera setup and one exact model version. The repository's inspected
best.pt metadata contains helmet/vest/person only, with no no_helmet/no_vest class.
Therefore neither PPE currently obtains explicit negative-class confidence from
that artifact. A code path exists for verified helmet-negative confidence only if
an independently identified compatible model is supplied. Do not substitute
person-box confidence or 1-positive confidence for missing negative evidence.
The actual deployed model was not inspected in this source-only investigation.

Keep automatic review OFF. The following is a practical demonstration screen,
not a statistically reliable deployment approval or estimate of field accuracy.
For an independently verified negative-score source only, a proposed initial shadow threshold is 0.90. It is an experimental starting value,
not a probability guarantee, and is NOT an application default in this candidate.

## Twelve independent episodes
Record each separately, closing the previous event before beginning the next:
- Four clear helmet violations: stationary, walking, different position and a
  longer continuous episode. At least three seconds of clearly visible evidence.
- Four correct helmet-use episodes in comparable positions/motion.
- Four ambiguous/interrupted episodes: partial occlusion, brief exit/reentry,
  boundary crossing and temporary camera/observation interruption.

Mark the intended condition before examining the score. Review actual footage to
correct mistaken labels. One event is one sample: never count its three-second
chunks as independent examples. Retain ambiguous cases separately; do not relabel
them as clear negative merely to fit the binary evaluator. If a normal/ambiguous
recording generates no event, count that separately in the manual scenario ledger,
not as a fabricated event_id or score in the event-only dataset.

## Conservative demonstration criteria
- No automatically confirmable candidate in the four normal or four ambiguous
  episodes. Any such case blocks the demonstration auto mode until investigated.
- Record how many of the four clear violations become candidates; low recall
  should lead to human review, not a lower threshold chosen just to pass.
- Every candidate must refer to a closed event, complete evidence, fixed supported
  model/PPE, no unknown/present/unverified observations or uncovered temporal span,
  no prior human review, no expired/deleted evidence and no unresolved integrity
  or clock problems. These are safeguards, not proof of classification accuracy.
- Add four new episodes (two normal/two violation) if possible after choosing the
  provisional configuration. Do not tune on these held-out attempts.
- Exercise administrator correction of a shadow candidate separately. No real
  automatic writer exists in this candidate; this test alone cannot enable it.

## What the new replay module can do now
`backend.auto_decision.temporal_evidence.summarize_episode` accepts explicitly
provided per-sample times, status and verified PPE confidence plus an explicit
maximum sample interval. It computes the producer-compatible minimum only when
all observations are verified helmet violations and cover the declared episode.
That does not mean the repository's default weights supply such observations;
synthetic replay tests establish code behavior only.
A weak late observation changes the score; unknown/present/gap records are not
silently ignored. Observations and time interpolation are not independently
validated. It is an offline module, not connected to live collection or review.

For binary labelled events with actual scores, use the offline evaluation tool
with an explicit threshold. Zero false positives in eight tests is not evidence
of zero field error, and event-only datasets cannot measure missed detections
that never produced an event. Describe the result as “tested these scenarios on
this setup”, not “validated system accuracy”.

Remaining for automatic confirmation: complete score provenance and online
collection, accepted policy and manual fallback, transaction-safe writer preserving
review/consent/retention/statistics, independent validation and controlled rollout.

Excluded episodes (`experimental_score: null`) must remain in the scenario ledger
with their exclusion reasons and counts. Do not report metrics only for clear
scored candidates as if those were the entire test set; doing so selects out the
hard cases. The 12+4 exercise checks behavior and shadow plumbing, not field
accuracy or authorization to activate automatic decisions.
