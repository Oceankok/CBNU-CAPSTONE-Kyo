# Pre/post-roll wire endpoint precision

Local follow-up to START qualification (`cfa185a`) and the reviewed writer endpoint guard (`6d8202d`). This change does not alter observation durations, gaps, automatic decision criteria, stored timestamps, immutable request bodies, or source retention. It is not remotely merged or installed by this work.

## Preserved failure

Two native complete CLOSE requests remained uncommitted after HTTP409. Both ended at `2026-11-15T10:36:39.549667Z`; their six manifests covered from `10:36:30.216333Z` to `10:36:41.549666Z`. The required post-roll end was `10:36:41.549667Z`, exactly one microsecond later. The canonical 170-frame endpoint at 15 fps serializes to the manifest timestamp. Both event lifecycles were open at revision four; pending CLOSE sequence five, final count six, and all observation values matched committed progress.

Before editing, original SQLite bodies were read with `mode=ro`, SHA256 recorded, and matching PostgreSQL state was read in a read-only transaction into root report `reports/20261115_original-close-coverage-snapshot.json`. The two exact CLOSE bodies and their original hashes are regression fixtures in `backend/tests/fixtures/continuous_close_wire_precision.json`. Tests assert those hashes; only the route event/camera and preceding setup are isolated test fixtures. No original outbox is edited or replayed.

## Bounded comparison and consumer audit

`lifecycle.WIRE_TIME_EPSILON` is a one-microsecond timedelta. Complete CLOSE compares only the outer required pre/post-roll edges with that tolerance. Segment registration permits the same bounded outer deviation. Segment adjacency, sequence, ownership, observation accounting and sample monotonicity remain strict.

`capture._covers` defaults to exact coverage, preserving coverage-regression checks. Complete capture reports explicitly request the same outer endpoint tolerance. Internal gaps remain errors, including a one-microsecond gap. Range bounds and earliest-available checks use the same outer precision; missing ranges, clock uncertainty, non-closed state and incomplete history still block a complete report.

`auto_decision.evidence.coverage` unions manifest intervals over the actual event, rather than the padded pre/post-roll span. These manifests extend two seconds beyond the actual event, so this failure needs no change there. Its event coverage union remains exact; tests retain a one-microsecond actual-event gap as incomplete. `auto_decision.rules`, adapter and writer are unchanged. Actual positive observation gaps still prevent automatic eligibility; no measurements or timestamps are rounded to manufacture evidence. Manual review still needs all required capture reports and verified ready media.

## Verification scope

Actual PostgreSQL tests use the separate `kyo_test_observation_precision_20261115` database with disposable UUID schemas, never the persistent demo database. The exact two native CLOSE bodies accept idempotently, preserve stored segment ends and observation values, and produce no review. One-microsecond outer deviations accept; two-microsecond and one-frame deficits, missing manifests, missing capture ranges and internal one-microsecond gaps reject. Boundary overshoot and exact segment adjacency are checked separately.

Source TTL is independent. During investigation the preserved run's original 120-second source lifetime elapsed. A normal recovery may truthfully retire the original complete requests as incomplete with an audit record. This change does not extend TTL, force ACK, modify original bodies, restore expired media, or establish a successful native replay. Root coordinates any private demo upgrade and native owner recovery/repeat.

Final single-invocation result: **51 passed, no skips, 93.497 seconds** using `python -B -m unittest backend.tests.test_continuous_wire_coverage backend.tests.test_continuous_events backend.tests.test_auto_decision_rules backend.tests.test_capture_shadow_refresh -v` with `PPE_TEST_POSTGRES_URL` pointing to the separate test database. This includes six new regression methods and 45 existing continuous/rule/capture-hook methods. Raw output is `.private_media/observation-precision/wire-coverage-regression.log`. The earlier five-method invocation also passed before the sixth adjacency/overshoot case was added. `git diff --check` passes.
