# Runtime launch completion handoff — 2026-11-15 KST

Base: `33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a`.
Branch: `feat/runtime-launch-completion-20261115`.
Worktree: `C:\Users\robin\Projects\CBNU-CAPSTONE-Kyo\.private_media\worktrees\runtime-launch-completion-20261115`.
Local commit only; root selects integration/dependency order. PR #258 and the shared main/live source are preserved.

## Implemented

- `run_multi` keeps default legacy all-camera isolation and adds explicit continuous selection. Single-profile selection detects only its one primary. Repeatable `--continuous-profile PRIMARY=ABS_PROFILE` selects multiple detection primaries; `camera_ids` must match that primary set. Each child owns its complete calibrated mandatory snapshot. No legacy collector is launched for continuous profiles. Duplicate primaries and duplicate/nested state roots fail closed. Continuous automatic restart is 0 and explicit positive budgets are rejected.
- Profile validation runs in the bounded short-lived preflight; it calls the event owner's side-effect-free read_config and adds state/selection checks without creating profile state. Missing/stale calibration is identified by the preflight stage. Default preflight deadline is 120 seconds, configurable; external stop and Ctrl+C cancel it. External signal files do not become raw temporary storage directories.
- A same-user/same-host file lock keyed by server+primary prevents overlapping detection owners across worktrees; CaptureOutbox separately owns its state journal. Neither is a distributed camera read lease. Confirmed with event owner: GET chunks/list are non-destructive, reader cursors are independent, and InferenceLoad is only priority. References may overlap; read/decode/spool capacity multiplies.
- Real CLI preflight/supervision own descendant process trees. Windows uses a Job Object and pipe-gated guard before command launch; POSIX uses a new session/group. A crashed parent does not leave an encoder orphan. Cooperative stop retains the existing cleanup grace before hard containment cleanup.
- Legacy reference collector propagates 401/403 from top-level polling and per-request processing to the existing fatal exit78 wrapper. Transport/429/5xx and per-request404/409 deferral remain.
- New `deploy/host_runtime` contains strict settings forwarding, a versioned entry with read-only settings check and version/mode checks, PowerShell7 hidden/background wrapper, selected-interpreter check/explicit preparation command and a legacy settings example. Wrapper holds its runtime-state lock, preserves actual child exit code and separate stdout/stderr, and accepts PID-specific stop. Credentials remain inherited; personal Launcher/JSON/token storage is never patched.
- Dependency preparation defaults to check only. Only `--apply` calls pip in the explicitly chosen interpreter using versioned host requirements. Import smoke uses pinned tracking versions, no model/GPU inference or HTTP, and a temporary library cache. Preparation deadlines also clean the owned process tree.

Commands and settings examples: `docs/20261115_Versioned_Host_Runtime.md`.

## Validation

Executed from the new worktree:

```text
python -m unittest inference_worker.test_multi inference_worker.test_runtime_launch inference_worker.test_reference_collector inference_worker.test_multi_processes deploy.tests.test_runtime_processes deploy.tests.test_host_runtime -q
python -m compileall -q deploy/host_runtime inference_worker/run_multi.py inference_worker/reference_collector.py
git diff --check
```

Results: **66 tests passed**, no skips; compile/diff checks passed. New focused unit cases include strict/default forwarding, multi-primary command shape, no collector, duplicate state/primary, actual expired-clock profile rejection with no state creation, local lock conflict/release, fatal401/403 propagation/client close and preserved transient behavior.

Root cleanup review follow-up: added best-effort release across every owned tree, one retry for retained ownership, and a shared finite five-second reap deadline. Cleanup errors are reported after all peers are attempted. Windows setup failure cleanup is also bounded and always closes the job. A real two-tree injected first-release failure test verifies peers and grandchildren are all reaped with orphan0; a unit-injected preflight terminate failure verifies trees.close still runs through the nested finally already present in the initial commit. Focused follow-up suite has 47 tests (supervisor30 + runtime launch11 + process trees6), all passed; total accumulated cases are 68.

Real Windows OS helpers verified preflight deadline, external stop and abrupt-parent exit with **grandchild/encoder orphan0**. Two synthetic primary trees verified failed profile removal while healthy counter **13→38** continued, then final encoder orphan0. The encoder in this test is a sleeping Python subprocess, not real FFmpeg or video evidence. Existing direct-child9 scenario regression also passed (camera/reference exhaustion healthy34→59, orphan0).

Actual PowerShell7 wrapper launched a temporary fake-source Python module, returned its exact **exit17**, preserved stdout/stderr logs and handled literal spaces/`&` in paths. No pip installation or operating dependency check was performed: preparation calls are intercepted in tests. The initial test harness attempt to kill a nested process through taskkill was denied by the sandbox; the revised fixture self-exits abruptly, and all final containment cases passed without taskkill.

## Integration dependencies / limits

- Event owner worktree `inference-completion-20261115` supplies primary-only read_config support, reference-failure degradation without dropping mandatory evidence, truthful stop/tail/disposition and detector mode handling. This branch does not edit those sources. It aligns review-mode preflight with that owner's person/helmet(+vest/no_helmet) contract; current weights are not replaced.
- Root flagged nested chunk401/403 wrapping; event owner was asked to preserve HTTP auth classification through source/mux/runner. G3 does not parse exception text to invent fatal status. Event owner was also reminded that a returned nonzero continuous result must reach SystemExit at run_server's module boundary, otherwise runpy would discard it. These require integrated contract verification, not a claim based on G3 mocks.
- No backend/frontend edits or new endpoints, operating config edits, DB reads/writes, GPU runs, source/camera/broadcast operations, package installs, push/PR, main merge or deployment were performed. Automatic confirmation stays OFF. No existing event/media was touched.
- Windows containment is verified locally; POSIX group cleanup still needs the integrated Linux run. Actual continuous media/lease/source cleanup, calibrated clock profiles, GPU throughput/memory, real disconnect/reconnect, browser playback and real-video/manual comparison remain explicit validation gates. Model-independent launch implementation is available now; no replacement weights were awaited.
- This wrapper does not register scheduled tasks or copy/overwrite personal launch files. Root/user must review a later host action pointing the existing credential-loading stage to the versioned command. Default #258 isolation already connects without new launcher flags after source deployment; advanced policy/runner forwarding uses the new entry.
