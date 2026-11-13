# W17 explicit current-evidence scan

`POST /api/events/{event_id}/suspected-duplicates/reconcile-current?limit=20&offset=0`

Administrator-only action. No request body; the action explicitly asks the server to match and save **current** metadata evidence. The client should label it '현재 근거로 의심 관계 찾기', not '중복 사건 확정'. It is not invoked on reads, ingestion, a schedule, or worker startup.

The matching rule is unchanged: same registration group/version and PPE, different camera, positive timezone-aware observation overlap, no merged source. Missing registration context is ineligible; the server does not infer historical group membership.

For each bounded page (1–100 peers, offset 0–10000):
- A pair with **any** saved evidence record is preserved without mutation. This applies to saved, stale, and released records, regardless of their current evidence fingerprint.
- A never-recorded pair matching current evidence is saved with the requesting administrator as `saved_by`, under the shared write lock.
- All writes in a page are atomic. Repeating or scanning from the opposite event does not create a second pair record. Concurrent calls share the same lock; PostgreSQL reads current evidence after acquiring it under READ COMMITTED.
- The result includes created/preserved outcomes and pagination metadata. Page offsets are not a durable dataset snapshot; changes between requests may shift matches. A new explicit scan can be repeated safely. The endpoint does not promise complete unattended discovery.

No changes to candidate event status, review, media, statistics, or event merges. No model/front-end/operating deployment change. No schema migration.

## Release and re-review

Existing release only releases a saved evidence record. The separate computed metadata tag may still be visible and is explicitly labelled as such. This endpoint never recreates a released pair, including when the evidence changes. A changed record is reported `stale` by the existing listing.

An administrator can explicitly inspect current pair evidence and use the existing fingerprint-guarded pair save endpoint to record a **changed evidence version**. This is explicit re-review, not automatic re-proposal. Re-saving an identical released fingerprint returns the released record and does not reactivate it. No new 'undo release' or hidden-candidate policy is invented.

Unresolved choices for full unattended W17: whether dismissed computed candidates should be hidden, what evidence change permits resurfacing, and durable automatic scheduling/ingest integration with load limits. This candidate supplies automatic matching **within an explicit administrator action**, not unattended persistent automatic tagging.

## Checks

Focused tests: auth/bounds, missing context, opposite-direction retry, released+changed evidence, stale preservation, page retry, transaction rollback, and concurrent opposite scans. SQLite synthetic tests only in this task; actual PostgreSQL concurrency and workload validation remain separate.
