# Clock proof v1 review candidate

Base main: `7074fd565ffc40f928791fe83606d68cfbe303b2`. This is opt-in candidate
code. Nothing in this change enables, installs, merges, or deploys it. It does
not enable automatic judgment. Tests using synthetic timing are not field proof.

## Scope and trust

The source agent reports actual samples of one fixed monotonic-to-UTC mapping.
The server authenticates ownership, bounds freshness, stores the samples, and
binds proof IDs immutably to accepted raw chunk sequences. A processing host
validates those records and renews its current lease without replacing offsets,
error bounds, tracker, or episode state. Source UTC timestamps are not corrected
before ingress; the reader applies the configured offset exactly once.

The source remains a trusted producer. Authentication/challenges cannot prove a
malicious source sampled a physical clock or real camera. `drift_ppm`, sample
error, step tolerance, maximum error, TTL, and permitted RTT are explicit
engineering assumptions. RTT and offset intervals are measured. Neither a
larger error budget nor a longer TTL is inferred from a good sample.

v1 requires one API clock coordinator process, matching the release Dockerfile's
single uvicorn process. Each process has a boot ID: API restart or worker changes
invalidate live renewal instead of silently accepting a changed UTC basis. A
source detecting that change stops. Already captured media keeps its original
authenticated proof and interval and may still drain after restart; it is never
relabeled under a new proof. Existing context stop/drain procedures still apply.
Multi-worker deployments require a shared coordinator, not removal of this guard.

## Storage and wire contract

Schema 37 adds only `capture_clock_stream`, `capture_clock_sample`, and
`capture_clock_chunk`. Existing tables/columns/rows are retained. No production
database is touched by the tests. All three tables also have SQLite/PostgreSQL
migration definitions. A per-stream maximum of 100000 retained samples fails
closed without discarding pending media or proof. Active/unresolved sessions do
not prune samples by time: a source may still own unuploaded chunks or an
ambiguous response. Only an explicit stopped-source final-sequence ACK permits
pruning unreferenced samples. Accepted chunk associations and the latest published
proof remain for audit and consumer sequence stability. Long-running operations must plan metadata
retention/archival before reaching this capacity; v1 does not delete receipts.

- Source-authenticated `POST /api/video/streams/STREAM/clock-challenges` binds
  version 1, source run UUID, source epoch UUID, increasing sample sequence,
  source UTC send sample and monotonic send sample. Binding must occur before
  the first chunk. Server response includes a random proof ID, server sample,
  configured server epoch, process boot ID, and explicit policy.
  Exact request retries return the original nonce, server sample and stored
  policy, including after a lost response; a conflicting request at that sequence
  is rejected. Retry does not extend the original time basis.
- Source-authenticated `POST /api/video/streams/STREAM/clock-proofs/PROOF` supplies
  receive UTC and monotonic samples. The server checks ownership, version,
  nonce/sequence, finite values, timing agreement, bounded RTT/age, and uncertainty.
  Expiry is server sample plus configured TTL, never caller supplied. Exact
  publication retry returns original bytes/expiry; conflicting retry is rejected.
- Processor-authorized `GET /api/processing/video/streams/STREAM/clock-proofs`
  returns the latest proof with fresh server time, or `?proof_id=UUID` reads one
  original media proof. Retired/inactive source ownership remains checked.
- Proof-mode uploads use `X-Clock-Proof-Id`. Accepted sequences retain their
  proof association across duplicate upload. The original raw capture start,
  duration, hash, context start and server future-end gates remain strict.
  Listed chunks include proof ID, clock version/epoch/run and existing context.
- Source-authenticated read-only `GET /api/video/streams/STREAM/clock-observation`
  supplies current server time and owned stream identity for historical upload
  admission. It publishes no clock proof and renews no live lease.

A proof covers only capture after its receive sample and before its conservative
bounded expiry. It cannot retroactively certify a gap or previously uncovered
chunk. The host can consume delayed, already certified media under its original
proof while its current live calibration is valid; raw retention is not extended.

The source journals the exact pending challenge request before IO, then the
challenge response and actual receive samples before publication IO. An accepted
publication with a lost response is recovered by exact retry, preserving its
original expiry. An acknowledged challenge whose sample is too old/wide is
abandoned as a measurement, but its consumed sequence is retained; a genuinely
fresh measurement must follow. Recovery settles ownership before a new live
measurement and does not represent an old receipt as fresh calibration.

## Rollout configuration, after independent review

Do not use these instructions to overwrite the current source laptop or installed
runtime. The actual Dual sender has local replay/retain/retry changes; merge and
test those explicitly in a later source integration. This branch starts from
main and does not incorporate or replace those unknown additions.

1. Back up and rehearse schema 36 to 37 against a restored isolated database;
   compare representative/all existing rows. Publish matching reviewed server
   and host source revisions. Existing release migration approval/backup practice
   remains in force. Rolling the application back to schema-36 code after a
   schema-37 upgrade requires its existing compatibility/restore procedure.
2. In reviewed `server.env`, keep the existing continuous/context feature flags
   and processing mode explicit. Add `PPE_CLOCK_PROOF_ENABLED=true`, a nonempty
   deployment `PPE_CLOCK_SERVER_EPOCH`, and all six policy values below. The
   release Compose file forwards them to API, maintenance, and db-init. Its
   default is OFF; `.env.example` leaves all policy values blank.
3. On the source set `VIDEO_CLOCK_PROOF_ENABLED=true` (or pass `--clock-proof`),
   `VIDEO_CLOCK_SERVER_EPOCH` equal to the server label,
   `VIDEO_CLOCK_SOURCE_NODE_ID` equal to the authenticated source node,
   `VIDEO_CLOCK_POLICY_JSON` to the identical policy JSON, and explicit
   `VIDEO_CLOCK_REFRESH_SECONDS` less than half TTL. Existing server URL/token/
   camera environment variables remain required. The existing starter inherits
   these variables; the direct path is
   `python -m video_sender.context_sender --directory NEW_PRIVATE_PATH --clock-proof`.
   No token enters command arguments or saved state.
4. After the source connects, `session.json` contains context/session/stream/run
   IDs and `clock.epoch/anchor/proof`. Read a fresh backend proof with the
   processor token, choose each camera's measured fixed offset and justified
   envelope, and pin the exact bindings below in that primary's profile. Every
   primary profile still includes its mandatory reference-camera snapshot.
5. Select `runner: "continuous"` and existing `continuous_profiles` in host
   settings; use matching review mode and model contract. Do not leave the
   installed launcher at legacy mode. `--check-settings` validates locally;
   supervisor preflight reads proof before model load, and the child verifies
   again before creating live frame state. The candidate does not install these
   settings or change the active service.

## Repeatable next start and historical recovery

Successful renewal occurs inside one unchanged source run/stream/epoch and
retains the same tracker/episode. A source restart or a new stream is a new
capture identity; it must not reuse pinned old profile bindings as though the
original person tracking continued.

1. Cooperatively stop host inference and source capture as appropriate. Wait for
   actual source capture-result proof; missing process/heartbeat is not stop proof.
2. For pending completed source chunks, run the existing session's
   `--resume-drain`. It verifies capture completion, keeps every chunk's original
   proof ID, and uses current read-only server observations to wait for the strict
   raw future-end gate. It does not construct a ClockPublisher, require a usable
   old wall/monotonic anchor, or publish a new proof. A changed API boot/clock
   label cannot rewrite original evidence. Pending ambiguous live exchanges are
   irrelevant to already certified media. A failed wait/upload keeps files and
   the source context unresolved.
3. Confirm the exact final sequence/stop ACK and complete prior host outbox
   recovery. Do not auto-retire an unresolved context or delete its pending data.
4. Start a new source session with a new private session directory and actual
   fresh measurement. It obtains new context/session/stream/run/clock-epoch IDs.
5. Build a new versioned host profile from that source's current authenticated
   proof and bindings, with a justified fresh fixed offset/error envelope. Keep
   prior profiles/requests intact; do not edit old proof timestamps or expiry to
   make a new start pass. Reuse durable host state only after its previous
   obligations have reconciled; preserve old evidence until then.
6. Select the new continuous profile, run proof/version preflight, and start
   inference. Old pinned bindings must fail closed against the new stream.
   There is no implicit cross-stream person or episode continuity.

If the per-stream sample cap is reached, stop capture and use this same bounded
drain/final-ACK procedure. Capacity does not authorize deleting an active source's
proof. API restart blocks new live proof renewal in the old coordinator basis,
while authentic captured intervals can finish normal historical delivery.

Illustrative policy only, requiring measured operating acceptance:

```json
{"ttl_seconds":300,"max_rtt_seconds":1,"drift_ppm":100,
 "sample_error_seconds":0.02,"max_error_seconds":0.25,"step_tolerance_seconds":0.05}
```

The corresponding server variables are `PPE_CLOCK_TTL_SECONDS`,
`PPE_CLOCK_MAX_RTT_SECONDS`, `PPE_CLOCK_DRIFT_PPM`,
`PPE_CLOCK_SAMPLE_ERROR_SECONDS`, `PPE_CLOCK_MAX_ERROR_SECONDS`, and
`PPE_CLOCK_STEP_TOLERANCE_SECONDS`. The global
`PPE_CONTINUOUS_MAX_CLOCK_UNCERTAINTY_SECONDS` must be explicit and at least the
proof maximum, at most 0.25. Each fixed camera envelope must also fit the profile's
existing capture/mux maxima. A sample with excessive interval width is rejected
even if its RTT is below the policy's separate maximum.

Optional new profile object; placeholders must be replaced with actual bindings:

```json
"clock_proof": {
  "version": 1,
  "origin": "https://actual-server-origin",
  "server_epoch": "reviewed-deployment-clock-label",
  "policy": {"ttl_seconds":300,"max_rtt_seconds":1,"drift_ppm":100,
             "sample_error_seconds":0.02,"max_error_seconds":0.25,"step_tolerance_seconds":0.05},
  "poll_seconds": 10,
  "max_host_rtt_seconds": 1,
  "bindings": {
    "CAM_001": {"source_node_id":"ACTUAL_NODE","camera_id":"CAM_001",
                "stream_id":"ACTUAL_STREAM_UUID","capture_context_id":"ACTUAL_CONTEXT_UUID",
                "capture_session_id":"ACTUAL_SESSION_UUID","run_id":"ACTUAL_RUN_UUID",
                "epoch":"ACTUAL_SOURCE_EPOCH_UUID"}
  }
}
```

The original `clocks` object remains required, with a common declared time-basis
label, finite fixed offset/error, verified assertion and parseable `valid_until`.
In proof mode only, an old static `valid_until` no longer blocks startup: an actual
fresh accepted proof must establish validity before inference starts. No arbitrary
expiry extension is used. Removing `clock_proof` preserves legacy static-profile
expiry checks; a static reader refuses a proof-bound source rather than falling
back silently. A proof-mode reader refuses chunks missing exact proof bindings.

Renewal accepts `[L,U]` only if
`max(abs(L-fixed_offset), abs(U-fixed_offset)) + TTL_drift <= fixed_error`.
It retains original mappings/error in buffers and capture reports. Provider
responses are checked for origin, identity, epoch, version, policy, sample order,
replay, source sample interval, host RTT, boot ID, and monotonic deadline.

Primary calibration loss/expiry closes at actual last valid observations and
uses the existing incomplete evidence and bounded delivery tail. A failed
reference is permanently degraded for that session, retains mandatory incomplete
evidence, and no longer blocks a healthy primary. Authentication loss and global
server/processing-host clock changes remain fatal. There is no reference
reattachment or automatic episode restart.

## Raw ingress and frame timing

With measured source-to-server interval `[L,U]`, the sender waits before capture
until raw source UTC is at least `context.valid_from + max(0,-L) + guard`, and
before upload until the conservative server-now lower bound exceeds raw chunk
end. Both waits are bounded and repeatedly check source clock/proof validity.
The guard includes explicit sample error and millisecond timestamp quantization.
The sender never rewrites stored capture timestamps to make an upload pass.

Capture timestamps use actual monotonic observations of each chunk's first
captured frame under one UTC anchor. Missing/late chunk observations remain gaps;
no perfect one-second timeline is synthesized from frame count. Existing FFmpeg
frame timing, intrachunk sampling, strict gap policies, and observation rules are
unchanged, so this feature is not a new claim of per-frame hardware timing accuracy.

## Acceptance required

In addition to focused unit/API tests: a real Windows source/server run through
multiple renewals with one continuing person/episode; rewear then new episode;
one failed reference while another primary remains healthy; source/server clock
steps and restart; bounded raw-start/future-end waits for both offset signs;
delivery interruption/recovery with real source evidence; and schema/data restore
rehearsal for the actual deployment database. No live camera/field durability is
established by synthetic tests. A changed clock basis requires an explicit new
capture session and fresh profile bindings after safe previous-session handling.
