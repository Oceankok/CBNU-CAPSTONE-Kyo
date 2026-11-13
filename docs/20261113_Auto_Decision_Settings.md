# Automatic decision settings and completion-hook shadow

Candidate, not deployed. Based on #248 schema32; schema33 here is automatic-policy
storage, not deferred #246 translation schema33. Do not combine both migrations
under version33. A database from that divergent lineage is rejected before seed
changes; reconcile migrations explicitly before any later translation merge.

## Administrator API

GET /api/auto-decisions/settings returns version0 and mode off without inserting
anything when no policy exists. PUT requires expected_version and nonblank reason,
plus mode(off/shadow/enforce), finite threshold[0,1], model_versions, ppe_types,
camera_ids, zone_names and optional validation_reference. Only explicit model/PPE/
camera scope is accepted for non-OFF modes. Empty camera/model/PPE scope matches
nothing; empty zones adds no extra constraint. Zone restriction uses event's
historical snapshot, never mutable current camera zone. Unknown camera/zone is422.

An advisory write transaction serializes CAS and audit insertion. Version mismatch
is409 auto_decision_settings_changed. Failed writes do not append history. GET
/settings/history paginates by descending version with configured actor/time/reason
and whole policy. This is configuration audit, not review or model calibration.

Enforce is rejected409 automatic_confirmation_not_ready unless reviewed server
adapter and approved validation reference/scope checks pass. A text reference,
environment variable, saved shadow or client result cannot install the adapter.
No production adapter is included. The current 0.9 editing default is NOT a team
acceptance criterion, probability claim or model approval.

## Completion hooks and review

The four existing lifecycle/receipt/local-redaction/worker-result completion hooks
call the shared saved-policy dispatcher in their existing write transaction. OFF
returns without decision/shadow writes. In shadow, the existing diagnostic and
shadow tables are reused; key(policy version,event,evidence fingerprint) prevents
repeated unchanged hook calls from duplicating history. Policy actor means who
configured it, not a human reviewer. Reads do not trigger scans or backfill.

Enforce rechecks adapter policy gates at use time, rejects computed duplicate
suspicions and invokes the existing writer. Writer still requires complete closed
whole-event evidence, no human review, valid proof, usable media and matching
fingerprint, and writes system reviewer null with automatic_policy_confirmed and
second_review_needed=1. Existing admin PUT override remains available. No consent,
retention decision, footage publication or automatic statistics/recommendation
refresh is added. Exceptions roll back the caller transaction; no successful
confirmation is returned on error. The adapter must be pure, bounded local code.

## Model boundary still outstanding

trusted_adapter() intentionally returns None. A reviewed implementation must bind
actual model/adapter hashes, validated score semantics, approved held-out evaluation
report and policy scope to each proof, including unknown/occlusion/conflicting or
false-person evidence. It cannot just echo a diagnostic fingerprint. Current
review_only/null-confidence events remain pending even if a person detector gives
1.0. Model improvement alone does not validate a violation score. No scores are
manufactured, no weights are changed, and no hard face-presence gate is introduced.

The settings UI is independent of education/W17. It shows actual server activation,
blockers, configuration audit, and explicit read-only diagnostic preview. 409 keeps
inputs and requires explicit adoption of latest settings. It is not an accuracy
dashboard or a full automatic-vs-human review filter UI. Browser and combined
#249/#250 integration tests remain distinct from TypeScript/build checks.

## Test boundary

Synthetic temporary DB tests cover OFF, permission, scope/strict values, CAS/audit,
unknown validation refs, environment nonactivation, hook shadow idempotency and
rollback, review-only high-score decoys, revoked/missing adapter, repeated init,
32→33 preservation and divergent33 rejection. They do not establish real model
accuracy, live device E2E or PostgreSQL locking until those tests run separately.
