# Continuous runner opt-in: implementation and verification limits

The legacy `run_server` path remains the default. The only selection change is
`--continuous-config PATH`, together with an existing explicit submission mode.
Replay and ambiguous detector/submission combinations are rejected. No project-
wide settings, model weights, frontend files or operating environment are changed.

The JSON instance configuration must explicitly supply the state directory,
camera calibration snapshots/epochs/expiry, lifecycle progress cadence and gap,
source/mux/decoder/capture/ledger/upload limits and maintenance/lag limits. There
is no generated production configuration and no guessed zero-error calibration.
The existing API requires three-second qualification and two-second pre/post-roll.
Private decoder and source byte/frame memory limits are separate from native
codec memory and upload snapshots; process budget must account for both.

## Connected path

Fresh stream sequence boundaries → bounded source HTTP → private tracked decoder
→ chronological two-camera mux → unchanged detector/PersonTracker/ROI functions
→ transactional lifecycle journal → precommitted spool ownership → full-interval
capture → exact acknowledged manifests → final close → camera coverage reports
→ content-bound private upload receipts → audited original cleanup.

Reference frames at a common timestamp are fed before primary inference. The
initial source pre-roll must warm before qualification; source gaps are not
invented footage. Media-time continuity uses the processed mux watermark, not
network arrival wall time. Wall-clock stalls and privacy retention are separate.
A changed stream/policy/calibration stops the session, records interruption and
never reuses old track identities. Final progress is flushed before manifests.

The server's start camera snapshot must exactly match the explicit local camera
set and roles. A mismatch or wrong metadata-command acknowledgment stops the
session instead of silently continuing with different cameras. Raw/publication
boundaries remain the backend's existing authenticated/redaction contract.
Successfully redacted partial footage may be viewed; blanket pre-close quarantine
is not an approved requirement. Automatic final judgment remains OFF.

## Verification

Unit and synthetic tests cover outbox rollback, delayed source arrival, source
ordering, post-roll, chunk limits, private decoder crash cleanup, incomplete
reference capture, receipt ambiguity, TTL, partial-source audit and cleanup retry.
`scripts/check_continuous_runner_contract.py` runs the actual opt-in `run()` with
synthetic MP4 chunks and a mocked existing detector interface against the paired
backend's isolated TestClient. It checks one closed incident, two private
processing media records, and no residual local raw MP4s. This is not proof of
model accuracy, face redaction quality or actual GPU/camera performance.

## Remaining operating/recovery gates

- Actual Windows/RTX 5080, two-camera clock calibration and sustained throughput.
- Instance limits based on those measurements, including maximum raw retention.
- Operator recovery of unknown ownership, full audit capacity, corrupted ledgers
  and interrupted old sessions. The runner deliberately refuses an unreconciled
  nonempty capture root; it does not guess that old sources belong to a new event.
- Incremental prefix upload is still a separate design, not enabled by this
  runner. Close-only upload requires whole-event source space until completion.
- Incomplete-reference events now preserve verified completed fragments from
  either camera without claiming full evidence. Only a contiguous manifest prefix
  inside the final required interval is declared. Missing-camera requests may
  remain pending until their server deadline; the cumulative camera report already
  records missing ranges and the incident is interrupted and unreviewable.
- `scripts/check_continuous_partial_contract.py` checks real isolated API upload,
  interrupted state, final-review denial, historical receipts and local raw
  cleanup for a reference-camera source gap. Redaction execution and quality are
  not simulated as successfully completed by this check.

No live server, user PC or production DB was used for these checks.
