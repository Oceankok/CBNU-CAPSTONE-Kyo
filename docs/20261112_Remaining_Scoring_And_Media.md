# Remaining scoring decisions and media deadline guard

## W01/W02: deliberately unchanged

The documented score is count×0.5 + repeated weeks×0.2 + zone concentration×0.2 + process risk×0.1 (`20260407_PPE.md`, section 4-4). Neither this document nor the algorithm design DOCX defines the exact concentration denominator or risk conversion table.

Current concentration is the group count divided by all confirmed events in the quarter. The groups are respectively PPE, zone, and zone×PPE. For PPE-only stats this does not distinguish spatial concentration from dispersion. Risk is always 1.0 in all three paths, so changing a camera's process type has no scoring effect; this was reproduced with an isolated synthetic database.

Before implementation, settle risk scale/defaults, aggregation across zones, historical versus current risk attribution, and the concentration definition for each aggregation. More importantly, ranking changes must wait for W07 identity/editor protection: legacy recommendation IDs reuse rank, and a pending manual draft can be saved to a different target. No scoring formula, risk field, migration or frontend was changed here.

## Existing retention deadline enforcement

The existing cleanup policy treats `decision='retain' AND retain_until<=current epoch` as expired. The storage route previously checked only deletion state and ready/redacted media status, so expired media remained downloadable until cleanup ran.

GET and HEAD (including Range requests) now reject an expired retained media record with the existing generic 404 response. The check uses the same UTC epoch and inclusive deadline as cleanup. It performs no deletion or retention-state mutation. A valid extension is read fresh on the next request; pending or absent retention records and legacy unbounded retention remain available under existing rules. Existing `delete` decisions still block access. Structured consent revocation already changes linked retained events to delete; that workflow is unchanged.

The authorization decision applies at request time. It does not terminate a response already streaming when the deadline or revocation occurs. Physical cleanup is still performed by the existing maintenance workflow. This patch does not change media listings, source TTL, consent terms, redaction, retention duration or model behavior.

## Verification scope

Isolated synthetic databases and files only. No user PC, production DB, remote or commit changes. Actual PostgreSQL execution remains unverified; the added predicate uses the existing numeric timestamp column and ordinary SQL comparisons supported by both adapters.
