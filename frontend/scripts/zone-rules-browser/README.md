# Zone Rules browser acceptance

This harness renders the actual `ZoneRulesPage`, `ZoneMembers`, and `ConfirmButton`
with React DOM in Edge/Chromium. It uses the real frontend API client and intercepts
every API request in Playwright. Vite reads no environment files or production
config and has no backend proxy. Unexpected external requests are blocked. No
database, cameras, inference, or automatic confirmation settings are used.

## Run

Node 20+ is required. `@playwright/test` is declared in the frontend development
dependencies and locked to 1.65.1, matching PR #256. No changes from that PR's
education scripts or CI are required. The original browser verification reused
its installed dependencies through a local ignored `node_modules` junction.

From `frontend` in PowerShell:

```powershell
npm ci
$env:PLAYWRIGHT_CHANNEL='msedge'
node node_modules/@playwright/test/cli.js test --config playwright.zone-rules.config.ts
```

The command above uses an installed Microsoft Edge. For Playwright-managed
Chromium instead, run `node node_modules/@playwright/test/cli.js install chromium`,
remove `PLAYWRIGHT_CHANNEL` from the environment, and run the same test command.

Fresh setup was separately checked in an empty directory using these manifest and
lock files: `npm ci --ignore-scripts --offline --no-audit --no-fund` installed 204
packages from the existing npm cache. The fresh CLI reported 1.65.1 and discovered
all 11 cases with `test --config playwright.zone-rules.config.ts --list`. All files
in the three Playwright packages matched the previously tested installation.
This setup check did not repeat browser execution or test an uncached download.

The fixture owns loopback port **5189** and stops when the run finishes. Reports
and failure traces live under `frontend/.private_media/zone-rules-browser/`.

## Baseline evidence and resulting behavior

Baseline: `ac9d254774ae5c1d0ebdc9019f249b7ddceb68e8`.
Three diagnostic browser tests passed before the product change, confirming:

1. Submit A's rules as `Submitted`, hold the PUT response, then type
   `Later accepted input`, turn off helmet, and select risk 5. All three new edits
   appear in the DOM. Release the earlier response: text becomes `Submitted`,
   helmet returns to selected, risk 5 clears, and Save is disabled as if saved.
2. Hold A's save, save B, finish B: A's Save becomes enabled while A's request
   is still pending. A's membership controls also remain enabled during its save.
3. Return 409 for an A membership update and hold the resulting full worker GET.
   B can still add w2 successfully. Release the GET snapshot taken before B's
   update: w2 disappears from B's rendered membership.

To reproduce these diagnostics in a disposable checkout, copy this harness and
config there while keeping `ZoneRulesPage.tsx` at the baseline SHA, then run with
`$env:ZONE_RULES_BASELINE='1'`. The diagnostics intentionally assert the old broken
behavior and are skipped during fixed-code acceptance.

The fix locks text/PPE/risk/member/save/delete controls for each pending zone
operation, with independent zone locks so one response cannot release another.
Member writes are serialized across zones with initial, conflict, and retry reads
because those reads replace the entire worker list. Conflict/recovery messages
appear only after the awaited read completes. Failed reads keep member writes
disabled until a successful explicit retry; rule editing remains usable.

Acceptance covers independent A/B saves, all conflicting controls, failed-save
draft preservation, 409 and uncertain-response refreshes, failed refresh/retry,
initial worker-read failure, successful member updates preserving other zones,
and overlapping delete/save completion. Eight fixed-code browser cases passed
(including a targeted rerun after correcting a test fixture's empty DELETE
response to JSON). The production build, focused ESLint, and `git diff --check`
also passed. These are isolated frontend checks, not live backend/DB or field
validation. Cross-administrator revision recovery is covered separately by
`playwright.zone-cas.config.ts`; see `backend/zones/README.md` for the contract.
