# Original-session operation receipt

Follow-up to backend completion commit `048466163246cdfdaea6b26927085588926da7f3`. This closes the ambiguity where a complete close committed but its response was lost before a producer crash/source loss. Existing schema and saved operation records are reused; score, rule eligibility and mutation behavior are unchanged.

## Read-only request

Send the **unchanged saved Update body** to `POST /api/processing/continuous-events/{event_id}/operation-receipt` with the original processor token. The existing Update model normalizes datetimes/defaults/numbers exactly as `/updates` does, before comparing `lifecycle.canonical(body)` to `processing_event_operation.payload`. This read-only POST avoids reproducing backend normalization in the producer. No new request key is introduced: identity is original event + owner node + session + sequence.

```json
{
  "session_id":"<original 32-hex session>", "sequence":2, "kind":"close",
  "timestamp_end":"<original UTC time>", "frame_sample_count":17,
  "confidence":null, "observed_seconds":3.2, "gap_seconds":0.0, "gap_count":0,
  "close_reason":"ppe_present", "complete":true, "final_segment_count":1
}
```

This is a shape example; placeholders are replaced by the saved original values, never regenerated timestamps. Request validation is identical to Update. The operation runs in a read-only repeatable snapshot and invokes no lifecycle mutation, broadcast, shadow, review or retention hook.

## Response / safe reconciliation

Response fields: `event_id`, `session_id`, `sequence`, `submitted_sha256`, `receipt_status`, `accepted_operation`, `lifecycle:{phase,revision,evidence_revision}`, `event_status`, `recovery_blockers`, `may_attempt_new_update`. All normal and rejected responses from this route have `Cache-Control: private, no-store`.

- `committed_exact`: stored normalized body matches the supplied saved body. `accepted_operation` contains `sha256` and `body` bounded to validated Update fields. Acknowledge only the original historical identity; never rewrite the immutable close into incomplete. Historical complete close does not certify current source custody or finalizable video. Use the existing incomplete capture-report mechanism for actual source loss where permitted.
- `committed_different`: same sequence holds another normalized body. Accepted body/hash are available for diagnosis. Preserve the local original, do not acknowledge it or overwrite either side, and remain fail-closed.
- `not_committed`: no accepted operation at the supplied sequence in the read snapshot; `accepted_operation=null`. `may_attempt_new_update` is advisory, true only for open lifecycle at exactly sequence-1 with no protections. It is not a transaction lease, authorization token or permission to alter a durable queued request. Any new safe disposition goes through existing `/updates` checks and can still conflict after the read.

The producer may durably retire an unaccepted request in its own audit and append a separate truthful incomplete request at that API sequence after safe reconciliation, preserving the original bytes. Existing server CAS decides races. If the original complete wins, the new incomplete gets 409 and its receipt is committed_different; the original remains committed_exact. If incomplete wins, a late original complete gets 409. Exactly one accepted body is retained. A truthful incomplete capture report after a historically accepted complete close still blocks evidence finalization. Local retirement atomicity and reconstruction belong to the inference owner's tests.

Protections are orthogonal: `recovery_blockers` contains any applicable `already_reviewed`, `event_merged`, `event_media_deleted`, `event_media_expired`. Even committed_exact can have these protections; do not turn receipt recovery into a new write, review overwrite, retention extension or source revival. Current phase/revision can be newer than the historical accepted operation.

Missing continuous event is 404; wrong owner node/original session is 403; disabled/revoked processor uses existing authorization errors; disabled continuous API is 503; invalid Update is 422. A stored operation with unexpected/invalid fields or mismatching stored session/sequence is 409 `continuous_operation_receipt_invalid`, without exposing its body. No proof object for automatic confirmation is produced.

No receipt endpoint for start, manifest or capture-report bodies is added: those existing mutation retry contracts remain unchanged. This package does not implement the producer's autonomous journal recovery. The event owner must consume this endpoint without changing the saved request and test reconstruction separately.

## Verification

```powershell
$env:PPE_TEST_POSTGRES_URL='postgresql://kyo_test@127.0.0.1:55437/kyo_test_auto_rules_completion_20261115'
python -m unittest backend.tests.test_continuous_operation_receipt backend.tests.test_openapi_contract -v
```

Actual ASGI API + PostgreSQL with synthetic producer/video fixtures verifies: close commit followed by dropped response and local source deletion; conflicting saved close; not-committed/future sequence; normalization; repeated query leaves operation/lifecycle/shadow/review/retention rows unchanged; historical receipt after real media completion, simulated server-source TTL expiry and administrator hold; merged/deleted/expired protections; original node/session auth and disabled/invalid/missing requests; malformed stored evidence fails closed; private no-store on success and errors; unique OpenAPI operation IDs. Protection marker/TTL mutation and invalid stored payload are explicit isolated fixture setup. The full producer process crash/restart and real detector/camera/clock/redaction accuracy remain separate tests. No operating enforce, migration, deployment, remote push or PR is part of this delta.

Final recorded runs: 9 receipt cases + 1 OpenAPI case passed in 19.873 seconds; 3 subsequently added server CAS ordering/concurrency cases passed in 8.124 seconds. These are 13 distinct tests across two invocations, all PostgreSQL, zero skips. Raw evidence is `.private_media/auto-rules-completion/operation-receipt-final.log` and `operation-receipt-cas.log`. Focused compileall and git diff check passed. No assertion is made that these are a full repository suite or that the event owner's new recovery code has already run against this endpoint.
