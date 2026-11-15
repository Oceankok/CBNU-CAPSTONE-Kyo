# Rule pipeline completion and test boundaries

Base: `33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a`. Backend package only; inference and frontend owners retain their files. PR255's branch is preserved. Operating automatic confirmation stays OFF; no score is invented and no eligibility check is weakened.

## Demonstrated missing step

After both synthetic video segments had been uploaded and decoded through the real media APIs, terminal `state=incomplete` camera reports committed successfully but did not refresh rule shadow history. The previous history therefore described earlier evidence. The reproducer `test_incomplete_terminal_report_refreshes_rule_shadow` failed before the fix (`5 not greater than 5` snapshots).

`backend/processing/capture.py::report` now invokes its post-commit dispatcher for both complete and incomplete terminal reports. In `backend/auto_decision/settings.py::process_ready`, rule-shadow capture evaluation records exclusions without requiring finalizability first. Pending human-unreviewed events only are considered by this capture callback. Existing model-diagnostic behavior still requires finalizable evidence, OFF still makes no shadow/review writes, and enforce eligibility/writer checks are unchanged. An incomplete report never authorizes a review.

The report commits before secondary evaluation; a diagnostic failure rolls back only its own transaction. The same report retry refreshes the missing snapshot, and unchanged retries deduplicate by policy/evidence. Finalized human review and correction are preserved.

No new endpoint or producer wire field was required. Existing preview and shadow GET shapes remain compatible. Both successful and excluded rule results may now be recorded as terminal camera evidence arrives, so consumers must treat history as snapshots rather than assuming every row is eligible or that only a complete set of reports produces a row.

## Integration harness

`backend/tests/test_auto_rules_pipeline.py` has 13 focused cases. It uses the actual `EventLifecycle` and payload builders to produce start/progress/close, actual continuous API start/update/manifest/report/read/receipt routes, `PrivateClipTransport`, actual PostgreSQL test schemas, actual upload/source custody and processing claim/publication/cleanup routes. The server decodes an actual generated 36-frame MP4 and checks duration/generation against its segment manifest. No proof rows or received/ready statuses are seeded to bypass those paths.

One case runs uvicorn on a dynamically assigned loopback port and sends real HTTP with `httpx` and the unmodified private upload transport, performing OFF → shadow, upload → decoded completion → rule history. Its server thread/socket are closed in `finally`. Other cases use an explicit httpx adapter to TestClient ASGI. Fixture node/group creation uses actual APIs; the schema is isolated and deleted by existing test support.

Synthetic boundaries: frames are blank generated video; input person/PPE assessments and policy scope are explicit fixture observations; the test emits capture reports for the known generated segment interval and provides that synthetic video as the redaction worker's output. It does not execute the physical capture device, live detector, face redaction model, whole `run_continuous` source/mux/capture runtime, supervisor, measured clock calibration, or remote host/GPU. Manual incomplete-close replay tests verify consumer acceptance of the existing protocol, not the event owner's new autonomous restart reconciliation implementation. Cross-package runtime validation must combine that owner's final SHA with this backend package.

Cases cover both report/media arrival orders; OFF writes zero review/shadow rows; fresh and historical rule diagnostics with null confidence; exact metadata/publication/report retry and historical upload receipt recovery; pending/unavailable missing reference without deleting primary events; interrupted original-session close after remote GET; trailing unknown ending; post-commit shadow failure/retry; administrator hold/correction/history; primary-only/no-reference acceptance; and two independent primary event/session IDs on one processing node retaining the full camera snapshot. Each continuous process detects its primary only; reference video completeness does not mean reference-camera detection or shared identity inference.

A duplicate binary upload is not blindly retried as a new ingest after receipt: current API returns 409 for finished clip requests. The transport's existing `recover_receipt` checks the original session/camera/role/hash/bytes/media binding and recovers accepted custody. The harness uses that existing contract.

## Reproduction and evidence

Use a dedicated database whose name starts with `kyo_test`; each test creates a UUID schema through `isolated_api`.

```powershell
$env:PPE_TEST_POSTGRES_URL = 'postgresql://kyo_test@127.0.0.1:55437/kyo_test_auto_rules_completion_20261115'
python -m unittest backend.tests.test_auto_rules_pipeline -v
python -m unittest backend.tests.test_auto_decision_rules backend.tests.test_capture_shadow_refresh backend.tests.test_auto_decision_settings -v
```

Pipeline run: 13/13 passed in 45.097 seconds on PostgreSQL 17.12. Affected pre-existing rule/settings/capture-shadow regressions: 32/32 passed in 53.111 seconds. These are two separate final invocations, 45 distinct tests total, not a full repository suite. `git diff --check` and focused compileall also passed. Exact local implementation SHA is recorded in the package handoff JSON after completion. Raw logs are retained in `.private_media/auto-rules-completion/`, outside tracked product files. The migration regression intentionally prints its caught incompatible-schema diagnostic even on success. No claim of real-video accuracy or operating activation is made.

Remaining later gates are replacement artifact identity/capability validation, actual new video/manual observation comparison, calibrated source clock/resource configuration and integrated producer runtime validation. The user separately decides activation after OFF/shadow evidence. This package is locally committed only until root chooses the combined dependencies; it does not merge, push, deploy or close issues.
