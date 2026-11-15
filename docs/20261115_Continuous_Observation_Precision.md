# Continuous start qualification precision

Review candidate based on main `84022b7bc4baac182821886548532ed0f3daebd1`. This is not installed in the running team-demo API or merged remotely.

## Reproduced contract mismatch

The actual native vest start had `observed_seconds=2.9999992847442627`, `gap_seconds=0.8666677474975586`, span `3.866667` seconds and 52 samples. The producer's existing `EventLifecycle.update` qualifies with `observed_seconds + 1e-6 >= threshold`. Backend `Start.observation_span` required `observed_seconds >= 3` strictly. Thus a valid producer transition remained durably unacknowledged after HTTP422, blocking its later progress/manifests. The old outbox and failed event were preserved; this candidate does not rewrite them.

Two before-fix tests reproduced the rejection: the exact native values and a real EventLifecycle → start_payload → Start round trip whose epoch floating-point observation is less than three by approximately 0.715 microseconds.

## Bounded fix

`backend/processing/lifecycle.py` defines `OBSERVATION_EPSILON_SECONDS=1e-6`. Minimum start span and observed-time checks use that same producer tolerance; the existing observed+gap ≤ span tolerance uses the named constant. No stored timestamp/duration/observation is clamped or rounded to manufacture evidence. Values more than one microsecond below the minimum are still rejected, as are oversubscribed spans, nonfinite/negative observations, missing tracking ID and review-only confidence.

This is protocol qualification precision only. `backend/auto_decision/rules.py` is unchanged: actual gaps remain exclusions, minimum rule observation remains strict, incomplete/unknown media and lifecycle remain blocked. A newly accepted near-threshold start is not proof of a confirmed violation or permission to enable auto review.

## Tests and boundaries

```powershell
$env:PPE_TEST_POSTGRES_URL='postgresql://kyo_test@127.0.0.1:55437/kyo_test_observation_precision_20261115'
python -m unittest backend.tests.test_continuous_observation_precision backend.tests.test_continuous_events inference_worker.test_event_lifecycle inference_worker.test_continuous_payloads -v
```

The initial focused invocation ran 62 tests: all 56 existing tests and four new pure validation/producer cases passed; two new API cases had a test-fixture missing `ts` helper error. After correcting that fixture, all six new tests passed in a separate 2.686-second invocation. Therefore final evidence is **56 existing + 6 new distinct tests passed across the recorded invocations**, not a claim that one final full 62-test invocation was rerun. No tests are skipped in these recorded runs. Actual PostgreSQL API tests preserve the exact low observation values, idempotent one-event creation, review-only/null confidence, zero review writes and rule exclusions; genuinely short or inconsistent submissions create no event. Pure tests execute the real producer and wire serializer, without a detector/model/GPU.

Raw logs: `.private_media/observation-precision/precision-regression.log`, `precision-final.log`, and exact implementation SHA/hashes in the root handoff JSON. Focused compileall and git diff check pass. Demo services, main product source, existing data and policies were not changed for testing; tests use a distinct database and UUID schemas.

The separate encoder error `end boundary must align to output frame grid` is not fixed here. Its arithmetic belongs to inference's segment writer; exact raw origin/end/fps values are requested from that owner before a correction is proposed. No inference source or frame-grid eligibility check is modified in this candidate.
