# Clock-proof renewal review candidate

Prepared 2026-11-15/16. Base main:
`7074fd565ffc40f928791fe83606d68cfbe303b2`.
Branch: `feat/clock-proof-renewal-20261115`.
Worktree: `C:/Users/robin/Projects/CBNU-CAPSTONE-Kyo/.private_media/worktrees/clock-proof-renewal-20261115`.
Use `git rev-parse HEAD` for the exact committed candidate; the handoff reports
that hash separately so this file does not claim its own self-referential hash.

No main/installed runtime/service/settings/camera/production database changes,
push, merge, deployment, new weights, package installation, or paid calls occurred.

## Result

An opt-in vertical slice now measures a fixed source monotonic/UTC epoch,
publishes authenticated bounded fresh proof, binds accepted raw chunk sequences
to proof, and renews a running host's frozen mapping/error envelope without
resetting tracker or lifecycle. A new episode remains caused by rewear or a real
interruption, never by successful TTL renewal. Reference proof expiry/invalidity
degrades that reference explicitly with incomplete evidence and healthy-primary
continuation. Primary proof loss, global clock change, and authentication loss
remain fail-closed with existing observation close/incomplete/delivery handling.

Static profiles and sender mode remain default. A proof-bound stream cannot
silently fall back to an old static reader. Proof-mode startup can use an expired
static profile timestamp only when fresh backend proof certifies the fixed
offset/error/identity before processing. Existing raw backend gates, source gap
limits, event qualification and automatic-judgment settings are not relaxed.

Schema 37 adds three tables only. SQLite and PostgreSQL upgrade paths preserve
existing data. The release Compose environment, API Docker COPY/build allowlist,
source `--clock-proof`/inherited environment, host continuous profile settings,
and pre-model proof preflight are wired. Detailed protocol, exact opt-ins,
policies, source/host profile binding, source-laptop integration and deployment
boundaries are in `docs/20261115_Clock_Proof_Protocol.md`.

## Evidence

These are overlapping validation runs, not additive counts:

- Full inference regression before final provider-guard hardening: **506 tests,
  OK, 13 skipped**, 41.345 seconds. The
  shell's initial attempt lacked lap and ffprobe; it was rerun using the existing
  installed runtime `deps` and `bin` read-only. `PYTHONDONTWRITEBYTECODE=1`, isolated
  YOLO config, offline/autoinstall-off flags prevented installed-runtime writes
  or dependency downloads. No installed source directory entered `PYTHONPATH`.
  Raw local log: `.private_media/full-inference.log`. Skips include PostgreSQL-only
  cases without a dedicated test URL and Windows symlink capability cases.
- Affected backend/source/release/host compatibility: **63 tests, OK**, 22.464
  seconds. Includes the actual ASGI API, context and raw ingest behavior, static
  sender compatibility, release Compose configuration, and host settings checks.
- Focused clock/runtime run with local bundled FFmpeg: **10 tests, OK**, 4.908
  seconds. Real `ContinuousFrameRuntime`/journal/outbox/spool code preserved a
  continuing same-person event across valid renewals; expired reference evidence
  became incomplete while primary frames continued; actual rewear created the
  second event. Primary expiry closed at last observed time and stayed incomplete.
- Final shared-clock/source/API/release changes: **18 tests, OK**, 9.518 seconds.
  Actual source publisher -> authenticated API proof -> host verifier -> reader
  consumed exact proof/media bindings and applied correction once, including
  existing millisecond wire quantization. Nonce/replay/ownership/age/version,
  server time step and process restart, both offset-sign waits, expiry and wrong
  epoch/envelope cases were exercised with controlled clocks or synthetic media.
- Final source CLI pre-session validation and Docker allowlist/Compose wiring:
  **30 tests, OK**, 4.118 seconds. Bad opt-in policy cannot open a remote context.
- Schema-36-to-37 rehearsal: **1 test, OK**, 0.872 seconds. In a temporary SQLite
  fixture, existing context, representative raw receipt, seeded events/users/
  cameras and every other preexisting table's rows were compared before/after;
  only version and new clock tables changed. Repeat migration was a no-op. The
  same test has a dedicated PostgreSQL CI branch; it was not run against a live
  PostgreSQL service in this worktree.
- `git diff --check` passed. New source unit cases live in
  `backend/tests/test_clock_source.py` so existing backend/PG test discovery runs
  them. Their content was moved from the development `video_sender` test module.
- Final provider global-restart/service-failure guard: **11 clock/runtime tests,
  OK**, 3.319 seconds, including real FFmpeg runtime cases. This focused rerun
  covers the subsequent provider change; the entire 506-test suite was not
  redundantly repeated. Moved source-unit module: **6 tests, OK**, 0.129 seconds.

Windows ASGI tests required sandbox escalation because asyncio's local self-pipe
socket otherwise hangs in `_fallback_socketpair`. Tests used temporary isolated
databases and in-process ASGI; this was not a production network test.

## Review and integration boundaries

- Source producer trust is explicit: fresh authenticated samples are evidence
  from a trusted capture agent, not independent authentication of physical
  hardware or a malicious producer's clock. Declared combined drift/error limits
  are engineering policy, not measured future drift guarantees.
- Version 1 uses one API clock coordinator process and a process boot ID. API
  restart invalidates live renewal; it cannot silently shift an ongoing UTC basis.
  Original authenticated captured intervals still drain under their old proof.
  Multi-worker service support needs a shared coordinator. Source publication
  transport failures stop safely; exact durable pending exchanges support
  response-loss recovery without automatically restarting a source/episode.
- Metadata is bounded per stream; active-source proof is never pruned by TTL.
  An explicit final source ACK permits pruning settled unused samples; accepted
  receipt associations are retained, and a 100000 retained-sample limit fails
  closed. Long-duration metadata archival policy is a follow-up operating need.
- The actual Dual laptop sender has local replay/retain/retry additions outside
  main. None were incorporated or overwritten. Merge and test those additions
  explicitly before any real source deployment.
- Source timestamps come from actual first-frame monotonic observations. No
  perfect frame-count timeline or bridged chunk gaps are synthesized. Existing
  intrachunk decoding/PTS assumptions remain unchanged.
- Real Windows source/camera, network jitter, repeated TTL renewals, source/server
  clock-step interruption, live media playback, actual restored PostgreSQL/data
  comparisons, and deployment rollback still need independent acceptance. These
  tests establish a review candidate, not unattended field-proven durability.
- Do not merge, push, deploy, enable flags, replace host settings, or migrate the
  current database from this handoff. Parent review, CI and existing approvals
  choose the integration revision and rollout.

## Independent-review recovery correction, 2026-11-16

The predecessor `445c78b61e8026c768040b3cef922dce579dd3b0` had two confirmed
recovery defects: an accepted challenge/publication with a lost response could
strand the local sequence, and a proof older than two TTLs could be pruned while
its original local chunk was still waiting for upload. Its restart guard also
unnecessarily blocked historical upload recovery. These defects are corrected:

- Challenge request, response/receive samples and publication are journaled
  before each network boundary. Exact backend challenge retries return original
  nonce/time/stored policy; exact accepted publication retries preserve original
  expiry. Expired uncommitted measurements advance only an acknowledged cursor,
  never become fabricated proof, and are followed by a real fresh sample.
- Active/unresolved sessions retain all proofs and ambiguous challenge records.
  The existing 100000-sample cap applies backpressure without deleting evidence.
  Only stopped-source exact final-sequence ACK permits unused-sample pruning;
  accepted associations and latest published proof remain to avoid regressing
  consumer sequence state. Schema 37's new sample table also stores its original
  challenge policy. This is still an unreleased schema-36-to-37 candidate, not a
  migration of an installed schema-37 database.
- Historical `finish`/`--resume-drain` verifies local capture completion and uses
  authenticated read-only current server observations, not fresh publication or
  the old local wall anchor. Original proof IDs, policies and captured bounds
  survive API boot changes. Live renewal remains fail-closed for a new boot.
- Normal team next start is documented separately from within-stream renewal:
  resolve old capture/outbox obligations, obtain a new source context/run/stream
  and measured proof, prepare new pinned profile bindings, preflight and start.
  No implicit context retirement or cross-stream tracker continuity is added.

Correction validation: **63 tests, OK**, 21.524 seconds, covering actual ASGI
response-loss and persisted-state reconstruction, abandoned old measurement,
more-than-two-TTL proof retention, sample-cap backpressure, final-ACK pruning,
stable latest consumer proof, CLI historical drain after API restart with an
unusable old source anchor, static sender/context compatibility, schema rehearsal,
and the actual FFmpeg episode/reference runtime cases. Command:

```powershell
python -W ignore -m unittest backend.tests.test_clock_proofs backend.tests.test_clock_source backend.tests.test_capture_context_sender backend.tests.test_capture_session_api inference_worker.test_clock_leases
```

Pinned installed `bin` was used read-only for FFmpeg, with bytecode writes off.
The earlier 506-test inference run remains predecessor evidence; it was not
repeated for this source/backend recovery correction. PostgreSQL and real camera
acceptance remain for independent review/CI. No live settings, services, database,
source laptop, merge, push, or deployment changed.
