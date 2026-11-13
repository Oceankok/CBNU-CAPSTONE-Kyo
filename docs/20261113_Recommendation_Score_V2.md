# Education recommendation score v2

Approved formula for each frozen event zone + PPE group:

`confirmed_count × (1 + 0.2 × occurrence_week_ratio) × risk_multiplier`

Risk levels 1, 2, 3, 4, 5 map to 1, 1.25, 1.5, 1.75, 2. An unset
risk remains 1. No default level 3 or inferred historical zone risk is introduced.
The zone's current saved risk is read in the same generation transaction as the
event evidence. Its multiplier is persisted with that recommendation. Editing
risk alone never changes a cached recommendation; an explicit regeneration does.

The existing API accepts a quarter, not an arbitrary date range. Its entire
[start of quarter, start of next quarter) interval is the denominator, including
future weeks when generating an ongoing quarter. Each KST Monday-start calendar
week intersecting that interval counts once, even a partial week. Numerator is
the distinct same Monday-date keys containing confirmed event starts within the
interval. Week keys include the calendar year (e.g. 2025-12-29), avoiding week 0
and year-boundary collisions. End is exclusive. Existing local_time_sql retains
its legacy naive wall-clock handling and UTC-offset to KST conversion; no new
source timezone is guessed. No event duration expansion is performed.

The old `zone_concentration` field remains the within-zone PPE count fraction for
compatibility and explanation, but contributes **nothing** to v2 scoring.
`process_risk_weight` now persists the multiplier, rather than legacy constant 1.
Ranking uses unrounded scores, with zone name then PPE as deterministic tie keys.
Priority scores remain rounded to 2 decimals for storage and API display; equal
displayed numbers can legitimately have different ranks. These are ranking
scores, not probabilities, accuracy, percentages or calibrated risk estimates.
The existing top-three recommendation selection policy is unchanged.

Migration v30 adds nullable `education_recommendation.score_version`, without
recomputing any cache. Existing rows return `score_version: legacy_unversioned`;
newly generated rows return `score_version: zone_ppe_v2`. Only v2 breakdown adds
`period_weeks`, `occurrence_week_ratio`, and `zone_concentration_used_in_score:false`.
The multiplier already stored in `process_risk_weight` is the scoring snapshot;
level 1 and unset both legitimately map to 1. No historical risk level is inferred.
Admin zone responses now report `risk_scoring_enabled:true` for new v2 generation.
This supersedes the storage-only inactive behavior in the W01 preparation note.

PPE-wide and zone-wide quarterly statistics KEEP THEIR LEGACY SCORE FORMULA and
ordering. Their `priority_score` must not be compared numerically against v2
education scores. Changing them was not authorized. A possible future consistent
aggregation would sum unrounded zone/PPE v2 scores over ALL groups (not top 3),
then round once for display; this is only a design option, not enabled behavior.
Example: two zones with count10, one active week in14, risk1 and5 produce
10.142857 + 20.285714 = 30.428571 -> 30.43 PPE total; neither averaging risk nor
summing already-rounded scores is equivalent in general.

Frontend handoff: keep both version/scale distinctions visible; show the actual
formula for v2, week fraction and risk multiplier. Existing labels 'zone
concentration' do not mean a v2 input. A one-decimal multiplier display rounds
1.25/1.75, so display two decimal places if exact explanation is needed.
No frontend edits, model changes, automatic backfill, or production deployment
are part of this change. Recommendation-ID safety is a separate integration gate.
