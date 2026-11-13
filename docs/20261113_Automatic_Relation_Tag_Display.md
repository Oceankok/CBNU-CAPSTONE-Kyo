# Automatic metadata tag display (W17 partial)

This candidate connects the existing conservative metadata matcher to administrator event reads. It does not identify a person, prove the same incident, or change counting.

- `GET /api/events?include_suspected_duplicates=true` adds `suspected_duplicate_tag` to each page item (maximum 100).
- `GET /api/events/{id}?include_suspected_duplicates=true` adds the same object to `event`.
- Omit the flag for the existing response shape and query cost.
- The event page/detail and tag are read in one DB snapshot. Page count/ordering/filtering remain unchanged. Candidate counts describe all matching peer events, **not only peers on the selected page or matching its UI filters**.

Fields:
- `tag`: `suspected_duplicate` when the metadata rule finds at least one peer; otherwise null.
- `eligible`, `reason`: distinguish unavailable legacy/group/time context from a valid no-match result.
- `candidate_count`: same registered group version + PPE, different camera, strictly positive observation overlap. Merged source events are excluded. Historical groups are never inferred.
- `saved_record_count` / `released_record_count`: all saved evidence records attached to this event, including stale records; these are **not counts of current matching peers**.
- `release_suppresses_computed_tag=false`: existing release only marks a saved evidence record released. It does not hide the computed metadata indication. Reads never recreate or re-save any released record.
- `persisted=false`, `identity_confirmed=false`, `automatic_merge=false`, `statistics_changed=false` are invariant.

UI guidance: show a neutral '동일 사건 의심' indication, never a duplicate-confirmed badge. Open the existing paginated preview to inspect peers. Use saved-relation APIs to inspect exact saved/stale/released evidence. Do not interpret record counts as active tags or current candidate counts. Missing context means unknown, not proof of uniqueness.

Deletion, merges, changes to observation evidence are reflected on the next read. Existing registration-time group snapshots remain immutable when a current camera group changes.

Still unresolved: durable automatic tag creation, hiding a dismissed candidate, whether changed evidence should re-propose it, and frontend adoption. No schema migration, worker integration, model change, operating deployment or actual PostgreSQL run is part of this candidate. This is automatic **computed display preparation**, not completed persistent automatic tagging. The current rule can miss same-incident matches across group revisions and cannot prove identity from metadata.

Verification: focused API tests cover optional compatibility, no mutation, symmetric counts, filters, release semantics, merge/deletion refresh, missing context and auth. Full regression and independent review results are reported separately.
