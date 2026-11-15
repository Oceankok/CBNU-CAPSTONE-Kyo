# Terminal evidence display handoff — 2026-11-15 KST

Base: `be5ef7644dafb5ac4e029f35a4e8934d1530ce66`.
Branch: `feat/terminal-evidence-display-20261115`.
Worktree: `.private_media/worktrees/terminal-evidence-display-20261115`.
Local commit only; previous runtime branch and PR #258 preserved. Root selects local integration and targeted fresh acceptance.

## Reproduction and change

Read fresh verifier artifacts in `pipeline-final-verification-20261115/.private_media/final-verification`:
`browser-phase2.json`, `browser-final-failure.png`, `multi.database.json`, and the saved actual
`g3-terminal-incomplete-media.json` / `g3-terminal-incomplete-detail.json` responses.
The media response contained pending requests but omitted collection. The detail already contained an interrupted
collection and terminal incomplete camera reports: FV_EMPTY/FV_MISSING had no captured ranges and source_failed
covering their whole required interval. FV_A retained received early segments and declared missing later ranges;
FV_B retained all captured ranges/received media despite the overall event's incomplete status.

Only backend product change: existing authenticated GET `/api/events/{id}/media` now includes the same existing
`event.collection` projection (or null for legacy). No new endpoint/query/writer, status mutation or rule change.
Session IDs are already removed from public camera reports by the existing projection.

EventMediaPanel now surfaces authoritative per-camera terminal report reason and missing intervals, including
when primary video is available. A pending request becomes a **displayed** missing segment only when its camera
and segment interval overlap an explicit missing range in an incomplete report. Fully missing segments are shown
as unavailable; partial missing segments keep captured-but-pending work distinct. Captured/complete ranges alone,
collecting reports, and global incomplete reasons without missing overlap do not relabel valid pending uploads.
Received/ready media and processing/redaction status remain intact. Fully missing media-less requests do not poll
forever; open collection, captured pending work and processing continue polling. Manual refresh remains available.
Older responses without collection preserve prior behavior. Event/review/capture/clip-request records are not changed.

## Evidence

- Dedicated PostgreSQL `kyo_test_camera_recovery_20261115`, isolated_api UUID schemas:
  `python -W ignore -m unittest backend.tests.test_terminal_evidence_display -v` → **2 passed**.
  Media collection equals existing detail collection; terminal missing reason/segment mapping exposed;
  3 pending + 1 received and ready URL retained, can_review=false, unauthenticated GET401.
  Exact DB snapshots for clip requests/media/capture reports/lifecycle/reviews are unchanged across GETs.
  Legacy media has collection=null. Test schema count0 verified; resource returned to PG owner, no shared stop.
  Synthetic ready file bytes are fixture metadata, not validated video/redaction evidence.
- Real Edge/React DOM regression, one browser worker with a short-lived Vite fixture server:
  frontend: `$env:PLAYWRIGHT_CHANNEL='msedge'; node node_modules/playwright/cli.js test --config playwright.terminal-evidence.config.ts`
  → **5 passed**. Includes actual ReviewDetail+EventMediaPanel with saved authoritative API fixture, missing
  FV_EMPTY/FV_MISSING reason, preserved received primary/dialog and review gates, captured pending/redaction,
  partial overlap vs collecting, legacy response, terminal polling stop and active collection refresh.
  Only GETs are sent by the acceptance case. Storage is mocked without playable bytes; video decoding is not re-proven.
- `node node_modules/typescript/bin/tsc -p tsconfig.app.json --incremental false` → passed.
- Targeted ESLint for changed product/browser/config files → passed.
- `node node_modules/vite/bin/vite.js build` → passed.
- Existing `node scripts/mediaRecovery.check.cjs` → **10 passed** (hook/API doubles, distinct from browser tests).
- `git diff --check` → passed.

Browser screenshot visually checked:
`frontend/test-results/terminal-evidence/display-real-ReviewDetail--f2f90-ed-primary-and-review-gates/terminal-evidence-pass.png`.
Machine-readable run: `frontend/test-results/terminal-evidence.json`. These generated files are ignored, not committed.
Saved response fixtures committed under `frontend/scripts/terminal-evidence-browser` are synthetic acceptance data
from the actual saved API fields, with the new collection projection added from the authoritative detail response.
No credentials or private video bytes are included.

Initial test-harness issues (Node JSON import attributes, missing downloaded Chromium, overly broad /api route
matching Vite source modules) were corrected; final tests use existing Edge and all pass. No browser/package install.
Final short-lived Vite/worker PIDs150352/72048 were absent after the runner exited. Fresh verifier's services/data
were never modified or relied on. PG tests used TestClient, not an additional running API.

## Boundaries and next acceptance

No inference/lifecycle/recovery, automatic decision settings, DB status, review gates, operating config, GPU,
camera/broadcast, push/PR, main merge or deployment changes. Root should merge locally and relay the exact SHA to
the fresh verifier for its existing real API/browser/video targeted retest. Cross-chat to that verifier was rejected
by automatic approval review (only the three previously named chats were authorized), so no retry was attempted;
root relayed the sanitized fixtures and will relay the final result. No unresolved approval is needed for this local fix.
