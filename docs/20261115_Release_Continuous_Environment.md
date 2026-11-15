# Release continuous protocol configuration

The release Compose stack now forwards three existing backend options from the deployment's `server.env` into API, maintenance and db-init environments:

| Option | Omitted value | Meaning |
|---|---|---|
| `PPE_CONTINUOUS_EVENTS_ENABLED` | `false` | Continuous lifecycle/segment/capture-report API opt-in |
| `PPE_CAPTURE_CONTEXT_ENABLED` | `false` | Authenticated source capture-session protocol; when on, event submissions require a real capture context |
| `PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS` | empty | No approved clock budget: complete capture reports fail closed |

Previously adding these options to server.env did not forward them to release containers. This change only forwards configuration. It does not enable either protocol by default, install an automatic review adapter, select cameras, approve model outputs, alter eligibility, or change stored automatic settings.

## Deployment and readiness

1. Back up and rehearse migrations before deploying matching published API/web images and the Windows inference host revision through the existing release procedure. Keep the persistent project, volumes and camera ownership. The release script copies this Compose file into the versioned release directory; editing a checkout alone does not change existing containers.
2. When approved, set `PPE_CONTINUOUS_EVENTS_ENABLED=true` and `PPE_CAPTURE_CONTEXT_ENABLED=true` in the deployment's server.env. Set `PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS` to the measured and approved nonnegative seconds budget for the actual source clock protocol. Zero is valid only with evidence supporting zero uncertainty; do not copy the isolated replay's clock claim onto real cameras.
3. Recreate the API/maintenance containers through the owned release procedure; confirm the three selected runtime values without printing the remaining environment or credentials. Verify authenticated runtime revision/schema match the Windows host, processing mode is remote, and worker/source node assignments are correct.
4. Start the versioned Windows inference entry with `runner=continuous`, explicit per-camera profiles, the approved model path and `mode=review`. Start real capture contexts before publishing source chunks. Retain actual clock/retention/source and upload limits. A context-free event must be rejected when the capture protocol is on.
5. Read the current automatic settings version, then preview exact fresh event IDs with explicit model/camera/PPE/zone scope. `continuous_rules_v1` is the installed rule path; `validated_model` remains diagnostic-only. Enabling the continuous API does not enable automatic confirmation. A separate audited settings update with expected_version and reason is necessary to select shadow or enforce.
6. Prove the supported positive case from actual complete continuous observations and a negative incomplete/gapped case. Confirm system review/history and quarterly count only for the positive case; use normal administrator correction to reverse it. An OFF settings update stops future automatic writes without rewriting historical reviews. No broad backfill or synthetic observations are part of this deployment.

For the new artifact SHA256 `3a0a001e20c6df50fa8653747ce08214ba98220599539da533a00f8c42203006`, the detector's model_version is `ppe-3a0a001e20c6df50`. Its three classes are helmet, vest and person, with detection-score semantics. It supplies no calibrated absence probability. The four recorded native demo events have positive observation gaps and observation_gap close reasons, so they are negative automatic-rule cases despite complete reviewable video. Keep them pending unless an administrator reviews them; changing thresholds or treating detection confidence as approval would not establish a valid positive case.

## Validation

`python -B -m unittest backend.tests.test_release_continuous_environment -v` passes four tests using actual Docker Compose CLI config resolution (no daemon/containers) and an isolated SQLite-backed FastAPI TestClient. Tests verify inherited API/maintenance/db-init values; both protocols default OFF; explicit context opt-in requires provenance; missing clock policy blocks complete capture; a configured clock budget still does not bypass complete evidence or enable automatic settings. No operating DB, source, credentials or model is used.
