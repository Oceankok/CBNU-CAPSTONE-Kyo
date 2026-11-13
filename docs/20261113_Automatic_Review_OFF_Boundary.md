# W16: internal OFF preflight, not an automatic review implementation

`backend.auto_decision.readiness.evaluate_readiness` is an internal read-only
preflight with no route, DB schema, scheduler, enable switch, actor, or writer.
It recomputes current preview diagnostics in one read snapshot and reports why
automatic confirmation is unavailable. Every result is disabled, including a
structurally complete closed event with confidence above the requested threshold.

Required but unavailable policy/implementation boundaries:
1. Validated model/PPE scope, calibration, threshold and validation-set acceptance
   criteria. Caller-supplied experiment scope and low threshold do not establish
   any of these. Persisted shadow history also does not establish authority.
2. A defined aggregation of all completed event evidence, including missing or
   failed segments. Existing single-event confidence and metadata are not proof
   of a calibrated final whole-event decision.
3. An actual atomic writer with commit-time freshness, system actor provenance,
   audit, existing human-decision protection and administrator override, and
   review/aggregate transaction consistency. It must not alter independent
   consent or retention policy. None of this writer is implemented here.

The preflight additionally requires closed lifecycle provenance. Legacy events
without it, open/interrupted events, review_only submissions, human-reviewed
rows, deleted/expired/missing evidence inherit blocking reasons. Unknown IDs
raise the existing safe `404 event_not_found`; a batch does not partially persist.
No raw media paths are returned. The internal caller must remain within an
already authorized administrative context; this is not an unauthenticated API.

Optional expected diagnostic fingerprints can detect a changed input snapshot.
Matching them is NOT authority, calibration, byte integrity, complete observation
validation, a lock, or a commit-time concurrency guard. Files may change outside
the DB snapshot. No future automatic writer may reuse this check as its atomic
stale-evidence protection without a separate design.

Environment strings, request enabled/validated flags, a fabricated high-score
preview, or a saved shadow run cannot enable writes. The PreviewRequest contract
is revalidated and extra flags are rejected. This patch does not make a decision
policy or automated adjudication available. It only prepares and tests the OFF
validation boundary while those choices and implementation remain pending.

No frontend/model/production/PC/UAC changes are included.
