# W07: ranking-independent recommendation IDs

## Scope

Only regenerated recommendations use new IDs. Existing stored recommendation rows,
material references/content/publication, worker membership and scores are not migrated
or rewritten at startup. No frontend or detector changes, no schema migration.
The standalone recommendation generation and explicit statistics generation (which
rebuilds recommendations in its transaction) both use this identity function.

`EDU_S1_` plus full SHA256 of canonical JSON `[quarter, zone_name, ppe_type, education_topic]`.
Exact strings are used: no trimming, slugging, casefolding or ambiguous delimiter
concatenation. Topic is included so a changed educational objective is not the same
recommendation target. Rank, score and generation timestamp are not identity.
The prefix versions the encoding. SHA256 collision resistance is relied upon; this
is not an authorization token or a formal mathematical uniqueness guarantee.

Same semantic scope keeps its ID across rank/score changes, disappearance from the
top three, and later reappearance. A changed quarter, zone, PPE or topic gets a
new ID. It identifies the semantic target, not its numeric statistics revision.

## Legacy transition / compatibility

- Reads do not convert old `EDU_<quarter>_<rank>` rows.
- On the next explicit regeneration, all selected recommendations use semantic IDs.
  No new rank-based IDs are generated and no old ID is aliased to whichever target
  happens to occupy that rank now.
- A cached retired legacy URL fails404; create/generate supplying the old
  expected_scope use their existing409 protection. The frontend must refresh its
  recommendation list. This deliberate safe failure is preferable to silently
  saving educational content against a different zone/PPE.
- An old cache holding a semantic ID still targets the same scope after rank changes,
  even if the older client omits expected_scope. If that target disappeared it fails
  rather than falling through to a new rank occupant.
- Existing material `recommendation_id` fields are retained as historical references.
  Material GET/edit uses material_id and remains available. The existing current
  recommendation material listing finds by quarter/zone/PPE, so it also finds legacy
  materials for that semantic area. It can list old-topic drafts as before, but
  publish checks quarter/zone/PPE/topic and blocks a no-longer-current topic.
- Published worker content uses exact semantic scope, not source recommendation ID;
  unchanged targets stay visible. A removed or changed topic remains hidden by the
  existing publication policy. No automatic retarget or republication occurs.

## Remaining limits

- expected_scope is still optional for old clients. Stable IDs prevent rank reuse,
  not all stale content edits or score-version disagreements. FE should retain scope
  guards, handle404/409 and protect unsaved edits on refresh.
- A topic changed away and back restores its semantic ID. Numeric statistics and
  regenerated timestamps do not create new identity. If education later depends on
  an exact statistics/content revision, add an explicit revision guard separately.
- Unsupported direct SQL/manual mutation of a recommendation's scope while retaining
  its ID is outside this generator contract. Fresh-database seeds/manual fixtures may
  contain legacy IDs; no blanket SQL migration is performed.
- Zone renaming is an identity change under current string-based zone semantics.
  Stable zone IDs and aliasing are not introduced here.
- No actual PostgreSQL or frontend UI run was performed in this isolated task.

## Verification

Focused verification includes semantic identity, restart seed guards and publication refresh: rank swap with old
unguarded client, legacy retirement and materials preserved/publishable, removal and
reappearance, top-three eviction, topic replacement, mocked generation race, exact
component encoding, and existing worker publication snapshot/rollback regressions.
Full backend regression result is recorded in the handoff report.

Treat recommendation IDs as opaque strings. Do not parse rank from an ID. Existing
README/seed examples may still show the accepted legacy shape; list responses are
the authoritative IDs, and clients must refresh them after regeneration.


## Recommendation seed replay guard

Recommendation fixture INSERT is separated from general seed files:
- SQLite `education_seed.sql` only when education_recommendation did not exist
  before applying schema.sql. Then general seed.sql, initial recommendation seed,
  and migrations execute in that initialization order.
- PostgreSQL `postgres_education_seed.sql` only with seed=True and no existing
  app_schema_version row (the existing unversioned-schema safety check still applies).
  General postgres_seed.sql still runs under existing seed policy.

Fresh DB fixture IDs remain legacy for compatibility. On existing databases,
initialization never seeds recommendations again, even when their cache is empty.
This prevents retired legacy rank IDs returning after restart, and avoids adding
extra seed recommendations alongside new semantic IDs or legacy stored rows.
Other seed behavior is unchanged. Do not manually replay recommendation seed files
against initialized databases. An interrupted SQLite first initialization that left
the recommendation table present can omit demo recommendations; explicit generation
is safe recovery, not blindly replaying retired fixture IDs.
