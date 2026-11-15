# PPE model release: 2026-11-15

The repository's `best.pt` is the replacement checkpoint supplied for this release.
It is copied without retraining or modifying the artifact.

| Property | Value |
|---|---|
| SHA-256 | `3a0a001e20c6df50fa8653747ce08214ba98220599539da533a00f8c42203006` |
| Runtime model version | `ppe-3a0a001e20c6df50` |
| Classes | `0: helmet`, `1: vest`, `2: person` |
| Output contract | `scored_xyxy_v1`, original-frame coordinates |
| Confidence meaning | Detection score only; not a probability of non-wearing |
| Previous SHA-256 | `31037870bc9d677c679ad41ab24a65f7662f8d67bef54040b0b8cb37901c0d75` |

The previous checkpoint remains recoverable from the parent revision. Existing
installation copies and persistent event data must be retained during rollout.

## Verified scope

Both checkpoints were compared with the same code, thresholds and PPE policy on
the two supplied Camera01/Camera02 recordings. The checkpoint inference size was
960 for both. No camera-specific threshold, spatial exception or timestamp rule
was added. The new checkpoint produced two final candidates per camera: one
helmet and one vest candidate, with no observed final background false positive
or duplicate in these recordings. This is demonstration evidence, not a general
precision/recall estimate.

The isolated Windows continuous runner at `c944233` processed all 975 Camera01
frames and all 960 Camera02 frames. Four candidates and 44 ready redacted video
items were saved. Actual administrator review and same-quarter statistics refresh
passed for one candidate from each camera. This proof predates server deployment;
it does not assert that an operating installation already uses this checkpoint.

Known limits remain: Camera01's vest candidate qualified around 54.87 seconds
because observability gaps interrupted qualification. Camera02 had intermittent
accepted-vest detection loss while the vest was worn, but the existing three-second
qualification suppressed final false candidates during 26–38 seconds. Shared
postprocessing is unchanged. Field lighting, crossings, rewear and sustained
two-camera load require separate validation.

## Installation identity

Web/API containers do not contain the PPE checkpoint. The Windows inference host
must separately point to the exact artifact above and run code matching the API
release. Verify the SHA-256 and logged runtime model version before starting.
Use independent persistent state for this model and each camera; preserve older
state rather than clearing or relabeling it. The versioned continuous host wrapper
and per-camera profiles must be configured explicitly; replacing `best.pt` alone
does not switch a legacy launcher to continuous collection.

Rules-based automatic confirmation is a separate server policy. A detection score
does not authorize confirmation. Incomplete or gapped observation remains pending
under the existing eligibility checks, even when all review videos are available.
