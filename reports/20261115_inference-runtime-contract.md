# Inference runtime launcher contract — implemented review contract (2026-11-15)

Base:33c39ae6bd22c3d6669dcf4cb4a5b7fa9fdc968a. Event owner worktree:
C:/Users/robin/Projects/CBNU-CAPSTONE-Kyo/.private_media/worktrees/inference-completion-20261115
Branch:feat/inference-completion-20261115. PR257 source/cache worktree is preserved.

G3 owns run_multi.py/reference_collector.py/host profiles/preparation. Event owner owns run_server/run_continuous/detector/lifecycle/capture/transport. Implementation and verification are documented in 20261115_inference-completion-handoff.md; root chooses integration/push.

## Launch

Existing child interface remains:
`python -m inference_worker.run_server --camera-id PRIMARY --model-path LOCAL.pt --submit-review-candidates --continuous-config ABSOLUTE_PROFILE.json --stop-file STOP --max-chunks LIMIT`

- Explicit review assessment supports current person/helmet and optional vest even if the detector also names no_helmet. It does not infer final violation probability or activate automatic judgment.
- A continuous supervisor launches ONE child for one primary plus its entire configured camera capture snapshot. Do not also run reference_collector for that capture session, nor one continuous child per reference camera.
- G3 may expose --runner legacy|continuous, --primary-camera-id and --continuous-config. Legacy launch stays separate; its initial qualification interval is never reclassified as whole-event evidence.
- Single primary and zero references is supported by the updated continuous config validator. Multi-camera remains supported; every configured camera is mandatory. API start camera snapshot must exactly match the server response (including group references); mismatch stops without weakening the snapshot.
- One owner per profile/state directory/source primary. CaptureOutbox's journal lifetime lock prevents concurrent ownership; G3 validates duplicate profile state/primary assignments before starting. No live tracking identity is shared between child processes.

## Profile / preflight

run_continuous.read_config(path, primary, now=time.time()) remains the public side-effect-free config validator. It reads JSON and validates known keys/signatures; does not load weights, contact HTTP, create state, or acquire lifetime locks.
Required top-level keys stay EXACTLY:
state_directory, clocks, capture, mux, outbox, delivery, transport, lifecycle, source, decoder, metadata_steps_per_pump, max_lag_seconds.
No new top-level JSON fields were introduced.

clocks maps one or more mandatory camera IDs, includes PRIMARY. Every value:
offset, error_bound, verified:true, epoch(nonempty explicit common time basis label), valid_until(aware current timestamp).
No silent default calibration. Single source still requires an explicit verified source-clock assertion; it is not automatically synchronized by model IO.

state_directory is absolute private durable state. journal/, raw/, delivery ledger belong to this profile. Keep the same state directory for metadata recovery, but never restore person IDs or a live episode from it.
capture requires existing pre_seconds=2,post_seconds=2; lifecycle threshold_seconds=3. Other budgets/config constructor signatures remain existing explicit values. Raw evidence TTL is never extended on retry/restart.

Preflight uses current single-artifact PPEDetector contract + PersonTracker + assessment pipeline. Existing row fields are class_id/class_name/confidence/bbox. Missing/invalid score is not fabricated; an invalid observation is unknown/failed, not a valid absence. No two-model fusion/new class aliases/score-free tracking are being added.

## Stop / restart disposition

Stop file/max chunks/source EOS must cooperatively close lifecycle with the actual reason, stop capture, persist missing/incomplete evidence and perform a bounded delivery-only tail. It must not mark complete merely because shutdown was orderly.
Success requires the selected tail's obligations to be drained; unresolved metadata/receipt/recovery work is reported and exits nonzero. Auth401/403 and explicit contract/snapshot errors propagate for G3 fatal handling; no new supervisor exit code is planned.
G3 may contain the process tree for encoder hard cancellation. Event owner closes mux/decoder/writers/transports/outboxes and releases the lease/client even when cleanup operations fail.

A fresh process uses a fresh lifecycle session/track IDs. Exact saved requests may retry with their ORIGINAL event/session/model body. Interrupted live events must be dispositioned as incomplete using durable observation history and actual server revision/manifest state, not resumed as new observations or silently completed.
If remote state cannot safely reconcile, startup remains fail-closed with a diagnostic instead of starting overlapping live capture. An already-reviewed/merged/retained-expired event is never mutated blindly.

Legacy pending clip jobs receive durable metadata-only unavailable-status intent before risky transfer or reset. Reports are bounded/backed off; received media is never downgraded, and raw clips are still deleted per existing policy. No new raw-video persistence is introduced.

## Backend integration boundary / known protocol check

Existing APIs:POST continuous-events(start), POST /EVENT/updates(progress/close), POST /EVENT/segments, GET /EVENT?session_id=ORIGINAL, camera capture reports, receipts.
Restart disposition inspects server phase/revision/manifest and uses the read-only POST /EVENT/operation-receipt from backend92b079b for exact original operation status. All previous-session endpoint bindings are validated before any external IO. Recovery dispatch is scoped to one reviewed event at a time; protected/conflicting/unattributed state fails closed. Original commands are immutable; retirement and a new truthful disposition are an atomic audited append.
Backend owner supplies isolated API/shadow verification. Event owner has also verified six actual consumer ASGI/PostgreSQL recovery cases and a 451-test full inference run (3 Windows symlink capability skips). No TCP/camera/new weights/UI playback is claimed. Automatic confirmation remains OFF.


## Reference failure and all-camera detection

Only PRIMARY produces detections in each continuous profile. To detect every camera, use G3 repeatable --continuous-profile PRIMARY=ABS_PATH assignments with unique primary/state ownership; read-only reference sources may overlap. Configured cameras remain mandatory evidence, including failed references.

Reference initial/midstream/per-camera HTTP/decode failures persist source_failed and incomplete evidence without blocking primary. A bound reference with no first frame gets max_lag_seconds grace before failure; the actual poll_source runner expires that grace before checking mux progress. Primary failure, global stream listing failure and 401/403 remain fatal. No reference reattachment within an old runtime is attempted.

Remaining registered complete source bytes can be delivered using original session bindings and verified manifest/hash. Missing/unmapped raw never becomes complete by orderly restart. Raw lifetime is not extended. Exact historical operation ACK does not authorize mutation of protected events or imply evidence readiness.
