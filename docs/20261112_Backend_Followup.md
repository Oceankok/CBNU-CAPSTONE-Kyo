# Backend follow-up: recommendations and quarterly trends

## Applied locally

- First recommendation generation for a quarter now generates its actual quarterly summary and ranking in one transaction if the FK parent is absent. It does not create a zero-count placeholder, delete other quarters, or delete education materials. Failure rolls everything back. Existing summaries retain the previous ranking-only refresh behavior.
- The quarterly trend follows the four-quarter contract in `20260411_Dashboard.md`: selected quarter plus three preceding calendar quarters, chronological, with zero-count quarters retained and future quarters excluded. Both new cache generation and reads of old cache rows normalize this shape. Existing cached counts are not silently recomputed during GET.
- No frontend or inference/model code changed. The frontend percentage convention (including a zero denominator) remains outside this patch. Backend supplies the adjacent calendar quarters correctly; it does not add a percentage rule.

## W07 deferred: stable recommendation identity

Current recommendation IDs encode quarter and rank. A rank reused for another target can misattribute a pending manual draft. Existing material snapshots protect already-created material, not a pending browser form.

Proposed coordinated backend/frontend change: stable identity from quarter, zone, PPE and topic; rank remains display-only; atomically retire legacy rank IDs without aliases; retain all material snapshots/history; verify the target opened by the editor at draft save time. Old editor saves should fail safely and ask for reload. The frontend must clear or deliberately preserve editor state when the target changes, rather than submit old text under a new target ID.

This patch intentionally does not change IDs, migrate the recommendation cache, require new request fields, or modify the editor. Those compatibility/interaction choices require coordinated work with the frontend owner. Regression plan: draft A while B overtakes rank 1, target removal, topic change, delayed AI generation, stale manual save, old IDs during migration, material/history preservation, and concurrent refresh/save. The migration design must also account for legacy rank IDs in both seed files, repeated seeding, and duplicate scope/topic cache rows without silently merging their identities.

## Verification limits

Tests use isolated synthetic SQLite databases. PostgreSQL SQL/transaction handling shares the existing repeatable-read/serialization-retry infrastructure; an actual dedicated PostgreSQL run is still required. No production DB, user PC, remote branch or commit was changed.
