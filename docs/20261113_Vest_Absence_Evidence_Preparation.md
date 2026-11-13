# Vest postprocessing evidence preparation

## Repository-only findings

Read the existing `best.pt` and
`Exp01_yolov8n_640_clean_3class-13/weights/best.pt` archive metadata with
`zipfile`/`pickletools`, without deserializing or executing the checkpoint. Both
advertise classes `helmet`, `vest`, `person`. Neither advertises `no_helmet` or
`no_vest`. `configs/merged_ppe.yaml` includes `no_helmet`, so configuration text
alone is not evidence of the checkpoint's classes. No operational PC/model was
accessed or changed.

The current `PPEDetector` keeps detection class confidence. `assess_ppe` associates
positive boxes with people, marks cropped/small/overlapping observations unknown,
and otherwise produces `suspected_no_vest` on box absence. Its
`verified_ppe_confidence` stays null. `assess_helmets` can use a real `no_helmet`
class in a different model, but this does not make the repository's three-class
checkpoint a negative-class model.

## Prepared metric, not a confidence probability

`inference_worker.ppe_absence_evidence.summarize_absence` consumes one explicit
camera/session/model/track/PPE episode (scope required on every observation) of existing assessment statuses. It reports:
- present/absent/unknown counts;
- positive-box absence fraction among observable samples;
- explicit unknown fraction, sampling gaps, unobserved edges;
- consecutive absence-pair duration;
- `decision_metric` only for a complete >=3s episode without unknown/gap evidence.

The metric is an absence persistence feature, NOT a model negative-class score or
an estimated violation probability. No formula such as `1 - vest_confidence` or
person confidence substitution is used. `verified_ppe_confidence` remains null,
`automatic_confirmation_allowed` is always false, and there is no live runner,
model, API, DB, threshold default or review-writer change.

A value of 1 still means only that the detector repeatedly failed to produce a
matched vest box on geometrically observable samples. It can arise from detector
misses, lighting, vest variation or imperfect association. Per-PPE labelled
validation is required before a decision policy can use this feature. Unknown
samples cannot be discarded to manufacture a high score. Rejected episodes and
reasons must be counted alongside metrics to avoid selective reporting.

## Remaining integration

Collect real normal/violation/ambiguous episodes with actual frame assessments,
keep model hash and camera/session provenance, establish sampling/time assumptions,
and evaluate vest separately from helmet. The two PPE types cannot automatically
share a calibrated threshold. Keep ambiguous/unknown episodes in manual review.
A live score producer, policy acceptance and safe writer remain separate work.

Sampling warning: an episode created only after `suspected_no_vest` persistence
has already selected absence-heavy observations. Computing a ratio on only those
selected positive-candidate frames makes a value near 1 tautological. Validation
must include the actual preselected observation window, normal and unknown frames,
and independently selected normal/ambiguous recordings. “Observable” here means
only the existing geometric heuristic, not ground-truth visibility. No field in
this report authorizes automatic confirmation.
