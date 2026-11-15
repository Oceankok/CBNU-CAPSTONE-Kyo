# Zone rule revision contract (schema 35)

Admin `GET /api/zones` and successful zone saves return an opaque `revision`.
Worker projections do not expose it. The revision covers PPE, rules, risk, update
metadata, and a persisted random `revision_nonce`.

| Operation | Required precondition | Result on changed/missing row |
| --- | --- | --- |
| Create with PUT | JSON `expected_revision: null` | 409 if the name already exists |
| Update with PUT | JSON `expected_revision` from the admin read | 409; no update or recreation |
| Delete | Query `expected_revision` from the admin read | 409; no deletion |

Missing preconditions return **428 `zone_rule_precondition_required`**. A stale
precondition returns **409 `zone_rule_changed`**. Invalid revision strings return
422. Old clients are deliberately prevented from making unguarded saves/deletes;
refresh the frontend and adapt API clients to read and send the precondition.
Never fetch a revision and automatically retry a rejected user mutation.

PUT validates input, enters the existing write transaction, checks the revision,
then performs a conditional SQL update matching the actual stored snapshot.
Delete checks the same revision, existing member/camera/equipment refusals, and
uses a conditional SQL delete in that transaction. The SQL predicates close the
read/write gap even for PostgreSQL writers bypassing the advisory lock. Create
uses insert-if-absent. Competing same-snapshot operations have one winner; a
failed operation rolls back completely. Membership, account-zone changes and
equipment API writes already share this write lock. Rule saves do not alter
membership, events, recommendation caches, or automatic-confirmation policy.

Omitted `risk_level` still preserves the current value. Explicit null remains
invalid. A stale rules-only update is rejected even when it omits risk. Existing
recommendations change only through the existing explicit regeneration flow.

Every API save/create chooses a fresh random nonce, including a no-op save.
Consequently, API change-away-and-back and delete/recreate sequences invalidate
old revisions even with identical second-level timestamps. The field fingerprint
also detects current changes from writers that do not update the nonce. It is
**not an audit log**: a direct writer restoring every stored field and retaining
the nonce leaves no detectable history. Such writers must participate in the
revision contract if historical-write detection is required.

## Migration and compatibility

Migration **35 here means zone rule revisions**, not an earlier, unlocated score
prototype. SQLite and PostgreSQL add `zone_rule.revision_nonce` without dropping
or resetting data. Existing rows start with an empty nonce but receive valid
fingerprinted read revisions; their first API save assigns a random nonce.
Initialization at v35 without this column fails with an explicit lineage error
before seeding or relabeling the database. The existing schema33 lineage guard
remains in place. Unknown prototype databases require explicit reconciliation.

## Frontend recovery

On 409/428, the zone editor retains local PPE, rules and risk edits and awaits a
fresh admin read. Only the conflicted zone receives the latest-state panel;
other-zone drafts and revision snapshots stay intact. Save/delete remain blocked
until an explicit choice:

- Load latest server settings, discarding this zone's local edits.
- Keep local edits against the displayed latest revision, then press Save as a
  separate action. An unedited risk adopts the latest displayed value and remains
  omitted from the next request.
- If the zone was deleted, discard its local card or explicitly prepare a new
  create-only save. A stale save never silently recreates it.

Failed refreshes preserve drafts and block mutations until successful explicit
retry. PR #259's per-zone request locks and serialized member refreshes remain.

## Verification

Before the fix, authenticated TestClient sessions for two different admins read
the same zone snapshot at candidate `a33c6845685241da32eae8ac4c861a2140856d57`.
Admin A saved PPE/rules/risk 5; stale admin B saved a different PPE/rules/risk 1.
Both returned 200 and B's stale values replaced A's. A's stale delete also
returned 200. Local evidence is `.private_media/zone-cas-baseline.json`.

Backend checks run against temporary SQLite DBs or UUID schemas in a dedicated
`kyo_test*` PostgreSQL database, never an operating database:

```powershell
python -m unittest backend.tests.test_zone_rule_revisions backend.tests.test_education_upgrade -v
# Repeat with PPE_TEST_POSTGRES_URL pointing to the dedicated test database.
```

Coverage includes authenticated two-admin writes, same-snapshot concurrency,
competing creates, edit/delete races, same-second API ABA, direct-writer changes,
PostgreSQL writes between read and mutation, risk omission and populated v34→35
repeat-init preservation of zones/memberships/events/translations/settings.

Actual React/Edge acceptance with intercepted API responses uses port 5191:

```powershell
cd frontend
npm ci
$env:PLAYWRIGHT_CHANNEL='msedge'
node node_modules/@playwright/test/cli.js test --config playwright.zone-cas.config.ts
node node_modules/@playwright/test/cli.js test --config playwright.zone-rules.config.ts
```

The first suite covers independently mounted contexts and explicit 409 recovery,
other-zone drafts, failed refresh, omitted risk, deleted-zone recreation and stale
delete. The second retains the eight earlier request-lock/member checks. Browser
API responses are intercepted; authenticated backend concurrency is tested
separately. Real browser-to-API validation is a separate acceptance check. These
tests do not run inference, use real cameras, or enable automatic confirmation.
