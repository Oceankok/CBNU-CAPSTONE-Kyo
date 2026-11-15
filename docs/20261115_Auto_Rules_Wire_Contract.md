# Rule management wire contract (2026-11-15)

Frozen base: `33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a`. Backend completion worktree: `.private_media/worktrees/auto-rules-completion-20261115`, branch `feat/auto-rules-completion-20261115`. Existing PR255 is preserved. All routes below already exist. Examples use synthetic identifiers and illustrative thresholds, not approved operating thresholds. Actual model version/class capabilities must come from the existing producer; no violation score is required or invented.

## Settings and OFF/shadow workflow

Admin authentication is required. Read `GET /api/auto-decisions/settings`, preserve `version`, then PUT the complete editable policy with `expected_version` and `reason`:

```json
{
  "expected_version": 0,
  "reason": "Synthetic OFF/shadow comparison",
  "mode": "shadow",
  "decision_method": "continuous_rules_v1",
  "rule_criteria": {"minimum_observed_seconds": 5.0, "minimum_frame_samples": 10},
  "threshold": 0.9,
  "model_versions": ["fixture-model"],
  "ppe_types": ["helmet"],
  "camera_ids": ["CAM_001"],
  "zone_names": [],
  "validation_reference": null
}
```

Modes used by this implementation phase are OFF and shadow. GET returns the same policy fields plus `version`, `automatic_confirmation_enabled`, `activation_blockers`, `updated_by`, `updated_at`, `scope_semantics` and `score_semantics`. Rule score semantics are `rule_conditions_not_a_probability`. `threshold` is retained for compatibility but does not control rules. The `validation_reference` is optional for OFF/shadow; absence may appear in activation blockers, which does not prevent shadow. A reference string is not an approval. Existing model mode is `validated_model` and requires `rule_criteria:null`; its missing adapter still blocks activation.

Strict rule criteria: time 3..3600 seconds (finite number, not bool); samples 2..100000 (integer, not bool). Non-OFF policy requires explicit model, PPE and camera lists. Rule PPE support is helmet/vest only, additionally bounded by producer capability. Empty zones means no additional zone restriction; empty camera list is not a wildcard. Do not manufacture defaults to fill unknown observability.

PUT 409 `auto_decision_settings_changed` preserves draft inputs and requires explicit adoption of latest settings/version before retry. Unknown camera/zone is 422. Missing `decision_method` when modifying an existing rule policy to non-OFF gives 422 `explicit_decision_method_required`; always preserve method/criteria in requests. Switching OFF remains possible. Saving settings does not scan/backfill old events. OFF emits neither automatic reviews nor completion-hook shadow rows. Shadow emits diagnostic rows and keeps review state unchanged.

## Fresh read-only rule preview

`POST /api/auto-decisions/settings/preview`: send the complete editable policy above, remove `expected_version`/`reason`, and add `event_ids:["EVT_0123456789abcdef0123456789abcdef"]` (1..20 unique IDs). Do not use the older `/preview` endpoint or POST `/shadow` to evaluate a rule policy; those accept the model diagnostic request.

Illustrative relevant subset of a complete rule preview result:

```json
{
  "automatic_confirmation_enabled": false,
  "persisted": false,
  "mode": "unsaved_scoped_preview",
  "scope_applied": true,
  "items": [{
    "event_id": "EVT_0123456789abcdef0123456789abcdef",
    "decision_method": "continuous_rules_v1",
    "observed_confidence": null,
    "threshold_candidate": true,
    "structural_eligible": true,
    "blocking_reasons": [],
    "automatic_confirmation_allowed": false,
    "evidence_fingerprint": "<server SHA-256>",
    "score_semantics": "rule_conditions_not_a_probability",
    "rule_evaluation": {
      "method": "continuous_rules_v1",
      "eligible": true,
      "blocking_reasons": [],
      "criteria": {"minimum_observed_seconds": 5.0, "minimum_frame_samples": 10},
      "observed_seconds": 5.0,
      "frame_sample_count": 76,
      "gap_count": 0,
      "gap_seconds": 0.0,
      "close_reason": "ppe_present",
      "score": null,
      "score_semantics": "rule_conditions_not_a_probability",
      "limitations": ["producer_absence_heuristic_not_independent_ppe_verification", "reference_video_completeness_not_cross_camera_classification"]
    }
  }]
}
```

This is a subset example; retain/handle extra fields such as collection and whole-event inventory. Eligibility means a rule diagnostic met conditions, not a confirmed incident or measured accuracy. Unknown/incomplete observations, missing lifecycle/clip/capture report, pending/unavailable requests, expiration, existing review, abnormal/`*_after_unknown` ending and duplicate suspicion are exclusions. Render server blockers instead of inferring eligibility from duration or detector confidence. Original `review_only=true`/confidence null is score provenance and can coexist with a rule result.

## Persisted shadow history

Completion hooks create rule history using the saved shadow policy. Admin GET `/api/auto-decisions/shadow?event_id=EVT_0123456789abcdef0123456789abcdef&limit=20&offset=0` returns:

```json
{
  "event_id": "EVT_0123456789abcdef0123456789abcdef",
  "limit": 20, "offset": 0,
  "mode": "persisted_shadow_diagnostic", "persisted": true,
  "automatic_confirmation_enabled": false,
  "limitations": ["historical_diagnostic_not_current_state", "transaction_snapshot_not_commit_time_state", "no_automatic_review_written"],
  "items": [{
    "run_id": "<UUID>", "actor_id": "admin02", "created_at": "<UTC ISO>",
    "policy": {"mode":"shadow", "decision_method":"continuous_rules_v1", "rule_criteria":{"minimum_observed_seconds":5.0,"minimum_frame_samples":10}, "threshold":0.9, "model_versions":["fixture-model"], "ppe_types":["helmet"], "camera_ids":["CAM_001"], "zone_names":[], "validation_reference":null},
    "result": {"event_id":"EVT_0123456789abcdef0123456789abcdef", "source":"completion_hook_shadow", "settings_version":1, "policy_configured_by":"admin02", "automatic_confirmation_allowed":false, "decision_method":"continuous_rules_v1", "threshold_candidate":false, "blocking_reasons":["continuous_evidence_incomplete"], "rule_evaluation":{"method":"continuous_rules_v1","eligible":false,"blocking_reasons":["continuous_evidence_incomplete"],"score":null}}
  }]
}
```

`result` above is a subset for brevity; full rows include the diagnostic fields. History can mix old model diagnostics with rule results, and old policy JSON may omit method/criteria. Treat absent method as historical model diagnostic, not rules. Do not require `trusted_score_adapter` on rule results. Repeated unchanged hook calls deduplicate by saved policy version/event/evidence; later evidence can create a new immutable row. No poll/backfill worker is implied. `GET /api/auto-decisions/settings/history` is configuration audit and separate from these incident diagnostics.

## Recognized automatic review audit (existing schema)

An existing review's `review_comment` may be JSON with `source=automatic_continuous_rules_v1`, `settings_version`, `policy_configured_by`, `policy`, `model_version`, `evidence_fingerprint`, `rule_evaluation`. Display only a recognized, shape-checked object with string/number/null handling and fall back to plain comment text for malformed/other/manual comments. The actual review fields identify null system reviewer, `review_reason_code=automatic_policy_confirmed` and `second_review_needed=1`. Keep normal manual PUT review and review-history actions. Do not write/enforce a review to demonstrate this display: synthetic UI fixture or an already existing isolated test review suffices. Operating activation stays a separate later user decision.

## Handoff boundaries

Backend owns policy/rule/shadow/writer and actual continuous API/media OFF/shadow harness. Event owner owns producer/launch/journal/outbox/capture/transport and supplies actual restart dispositions. UI owner owns forms/preview/history/audit display. No new endpoint or wire change is currently proposed. If integration reveals a required protocol change, root receives a concrete proposal before the backend consumer changes. No operating enforce, fake clock calibration, default confidence=1, or detector-score relabeling is permitted.
