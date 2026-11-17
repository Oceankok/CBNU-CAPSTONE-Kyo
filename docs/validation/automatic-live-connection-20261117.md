# Rule-based automatic confirmation: live input connection

The installed manual-review input path intentionally excludes automatic
confirmation, even when the saved server policy is `enforce`. This change adds
authenticated source timing evidence, in-process proof renewal and an optional
host bootstrap that binds fresh camera sessions before inference. It never
relabels historical manual evidence or invents a PPE confidence score.

## Start flow

1. Back up the existing database and rehearse schema 36 to 37 on a restored copy.
   Schema 37 adds three proof tables; existing application records and camera
   assignments stay unchanged. Existing policy version/scope is read back before
   a test; changing the runner does not change that policy.
2. Use matching reviewed API, source and host code. Enable the proof service with
   explicit deployment epoch and engineering limits documented in
   `../20261115_Clock_Proof_Protocol.md`. Limits describe allowed measurements,
   not claims that a particular source has been measured successfully.
3. On the laptop, run `python -m video_sender.automatic_dual --settings ...
   --launcher ... --policy ... --check`. This reads the existing two-camera
   configuration and reports camera IDs and device indexes without reading
   credentials, making network requests or starting a camera. The separate
   source program preserves the original Dual installation and its pending
   sessions. New sessions and bounded source archives use separate directories.
4. Start the host with `runner=continuous`, `mode=review` and
   `continuous_bootstrap` pointing at an explicit private JSON template.
   Do not combine bootstrap with old static profile fields. The bootstrap waits
   for both live source sessions and their authenticated fresh proofs, checks
   source ownership, context, epoch, API boot, error bounds and freshness, then
   builds separate session-bound profiles. No operator copies UUIDs or edits
   clock expiry. A bounded timeout is a preparation failure, not inference
   success. `--check-settings` validates only the static template.
5. Start the separate laptop GUI and select both cameras. It samples and renews
   clock evidence automatically. A source clock step or changed identity does
   not silently restart tracking. Partial startup requests cooperative stop of
   the already-started camera. Unresolved sessions block a new recording;
   recovery requires actual capture completion and the existing drain protocol.

The template has exact fields `version`, `source_node_ids`, `server_epoch`,
`policy`, `wait_seconds`, `poll_seconds`, `proof_poll_seconds`,
`max_host_rtt_seconds`, `state_root`, and `profile`. The profile supplies all
existing strict runtime budgets except generated `clocks`, `clock_proof` and
`state_directory`. Private deployment-specific templates and source policy files
are staged outside version control. The bootstrap never enables manual gap
tolerance or overrides proof verification.

## Acceptance

- Eligible case: one camera observes a visible tracked person's same missing
  PPE continuously; leave its view while both streams keep recording long
  enough for the post-roll. All required camera evidence must finish and pass
  processing. Read the actual automatic review, original audit and statistics.
- Negative cases: unknown observations, missing evidence, source interruption
  and suspected cross-camera duplicates must remain pending. Seeing one person
  in both cameras may legitimately trigger duplicate suspicion and prevent an
  automatic positive. Do not remove that guard to force a demonstration.
- Shadow history is not a state change. Test shadow separately from enforce.
  A human correction must preserve the original automatic audit and update the
  same-quarter statistics after regeneration.
- Verify rewear/new episode, source recovery and archive capacity behavior.
  No camera-independent model accuracy or face privacy guarantee follows from
  these transport and rule tests.

The source is trusted and timestamps camera read completion, not hardware sensor
exposure. The encoder remains fixed-rate; synthetic timing/lease tests do not
establish sensor-level frame continuity. Actual laptop configuration, camera
startup, measured network conditions and real positive/negative acceptance remain
separate gates from CI or mocked API tests. No real-camera pass is claimed here.
