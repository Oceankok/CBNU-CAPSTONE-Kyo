# Automatic rule management UI

Base: `33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a`. This frontend change uses the frozen existing settings/preview/shadow contracts documented in root `reports/20261115_auto-rules-wire-contract.md`. It adds no backend route and performs no model substitution or score conversion.

The settings form preserves `decision_method` and `rule_criteria` in PUT and settings preview. Rule selection requires explicit duration/sample criteria for shadow, with server-equivalent bounds. The threshold field remains round-tripped for compatibility and is hidden on the rule path. Empty rule criteria can still be saved in OFF. Scope and validation-reference semantics remain unchanged.

Saved enforce is displayed, but this phase never offers an enforce selection, PUT, or preview. Every save/preview helper rejects enforce even when saved activation blockers are empty. The saved activation-blocker response is displayed separately from current unsaved preview; neither is presented as activation approval. Preview is read-only and is invalidated when policy/IDs change. There is no POST to the older model preview or shadow writer.

Settings operations use an immediate request lock and disable editing while pending. Component identity invalidates late initial/preview/save responses after unmount. Version409 preserves all inputs and expected version, retrieves latest settings, and requires explicit adoption of either the latest server policy or just its version before a separate retry. Failed latest reads can be retried. Non-version save failures preserve input without imposing a false version-conflict loop.

Persisted shadow history is an explicit per-event GET with pagination. It displays historical settings version, method, policy, evidence fingerprint, observations, exclusions, and limitations independently of current preview and configuration-change history. Older rows without method/version are labeled as older model/manual diagnostics; no version is invented. Rule scores are null and are described as no probability score. Rule eligibility is diagnostic condition satisfaction, not a newly written review.

Review detail renders only recognized `automatic_continuous_rules_v1` audit objects with checked shapes and stored system-review context (reviewer null, automatic reason) as structured rule explanations. Manual, older and malformed comments remain React-escaped plain text, even if manual text resembles audit JSON. Original raw audit text remains expandable. Review types now represent the existing null system reviewer and automatic reason. The existing re-review action remains available; system-only reason resets to a fresh manual choice, manual correction uses the existing PUT, and previous system audit remains readable in review history.

## Verification

Actual React/DOM/API clients run in headless Microsoft Edge with all API responses mocked. The Vite fixture has no production config, environment files or backend proxy; external origins are blocked. Port5192 is owned only during the tests. No operating setting, database, inference service or paid provider is used.

```powershell
cd frontend
npm ci
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm run test:auto-rules:browser
npm run lint
npm run build
node --experimental-strip-types scripts/autoDecisionSettings.check.ts
```

Final browser run: **11 passed**, 0 failed/skipped/flaky, approximately5.4seconds. Scenarios cover method/criteria round-trip and busy locks; same-tick duplicate-save prevention; explicit409 adoption and failed latest fetch; current preview and saved blockers; enforce display-only; missing/fractional criteria; rule/old shadow history; safe recognized/manual/malformed audit rendering; real review-detail correction PUT and retained history; late preview after unmount; ordinary save failure recovery. Lint and TypeScript/Vite build passed. The settings helper contract check passed. Machine-readable compact evidence and source hashes are in `docs/auto-rules-ui-20261115/results.json`; full local Playwright output is `frontend/test-results/auto-rules.json` and traces are isolated under `test-results/auto-rules`.

Linux Chromium can run the same script after `npx playwright install --with-deps chromium`, with no PLAYWRIGHT_CHANNEL. Existing suites were not broadly rerun: translation/zone/inference behavior is outside this change. Workflow files were not changed; publication/CI dependency is for root to choose. There is no push or PR from this worktree.

Actual authenticated API/PG/continuous transport and producer OFF/shadow acceptance remain backend/root integration work. Real footage/manual labels, model identity/capability, clock/resource measurements and any operating activation are separate validation gates. The UI does not claim those have passed.
