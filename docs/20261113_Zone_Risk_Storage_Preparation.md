> Historical preparation only. Active v2 scoring now reports risk_scoring_enabled=true. See 20261113_Recommendation_Score_V2.md for the current multiplier/formula and cache rules. Do not implement the inactive behavior below.

# W01: zone risk storage preparation only

Migration v29 adds nullable `zone_rule.risk_level` (integer 1 through 5).
Existing zones and newly created zones without an explicit value remain NULL.
There is no inferred default 3, legacy backfill, score mapping or automatic cache rebuild.
Existing recommendations do not require retroactive regeneration.

Admin `PUT /api/zones/{zone_name}` retains required `required_ppe` and `rules`.
Optional `risk_level` accepts JSON integers 1–5 only. Explicit null, booleans,
floats (including 1.0), strings and out-of-range values return 422 without mutation.
Omission preserves existing storage; omission on creation leaves it unset.
Reset to null is not supported. Deleting and recreating an otherwise deletable
zone does not inherit its former risk value. Unknown fields retain existing
Pydantic ignore behavior; this change does not introduce a general request policy.

Admin PUT and GET `/api/zones` responses add:
- `risk_level`: stored integer or null
- `risk_scoring_enabled`: always false in this release

Worker zone responses retain their original shape and do not expose these fields.
Only admins may write this setting. The SQL upsert excludes the risk UPDATE
assignment for omission, preventing legacy editors from clearing a concurrent
explicit update. Two explicit updates remain last-writer-wins; `updated_at` is
not a revision/CAS token. Other zone fields retain their existing behavior.

Storage is preparation, not an active recommendation factor. Recommendation
`score_breakdown.process_risk_weight` remains **1.0**, including on explicit
regeneration. No event snapshot or old cache is rewritten when risk changes.
The 1–5 to calculation mapping and future risk-change semantics remain undecided.
No frontend, detector/model, production DB or device changes are part of this patch.

The additive SQL is shared in meaning between SQLite and PostgreSQL, with schema
version gates for replay. Focused tests run on either isolated backend via the
existing PPE_TEST_POSTGRES_URL harness. A SQLite pass is not a PostgreSQL run.
