# W03 stop/reassign/restart linked candidate

## Scope and activation

Cloud candidate only. No running PC, operational database, camera, frontend or detection model changed. Backend migration v31 installs attribution/session/owner/receipt tables and extends event-zone snapshot source. Normal sessions remain legacy-compatible while `PPE_CAPTURE_CONTEXT_ENABLED` is unset. Enabling it requires upgraded source and processor together. Existing GUI/PowerShell starters do not enable this runner. Set processor config `source.require_capture_context=true` only with context-enabled sources.

Approved policy: stop capture fully, drain sender outbox, receive server stop acknowledgement, change camera zone, start a NEW capture session. A timeout/disconnect never proves stop. No online reassignment or guessed retirement exists.

## API and transaction contract

Authenticated source endpoints:
- POST `/api/video/capture-sessions`: camera ID and fresh UUID session ID. Server stores zone, version and owner. Same active ID is idempotent; retired ID cannot reopen.
- POST `/api/video/streams`: camera/context/requested UUID stream ID. Exactly one stream per context, retry-safe creation.
- POST existing chunk endpoint: contiguous sequence required for context streams; conflicts and gaps return 409. Backlog older than legacy 60 seconds is allowed within context boundaries. Camera capture timestamps must be after session start and not future. Durable hash/sequence receipts allow lost-response retry after raw TTL, without treating retransmission as new evidence.
- POST `/api/video/capture-sessions/{context}/stop`: `capture_stopped=true` plus exact stream→last sequence manifest. Only owner may acknowledge. Missing/extra/gapped final sequence fails. Repeated ACK preserves stop timestamp.
- Compatibility POST `/api/video/streams/{stream}/stop` explicitly closes authenticated legacy source after local stop and exact final sequence; not a timeout or automatic migration.

Camera zone PATCH and source-node reassignment reject active contexts with 409. This persisted guard remains if the feature flag later turns off. Legacy nonclosed streams also block moves while enabled. Historically context-enabled cameras cannot silently fall back to contextless ingest/events. Disabling flag therefore is NOT a full rollback: finish sessions before planned rollback. No automatic forced recovery is offered for uncertain crash state.

Events bind primary camera context immutably and store captured zone, even when delayed until after a zone move. Lifecycle progress cannot cross stop boundary. Existing events are not rewritten. Legacy event start retry canonicalization remains compatible.

## Delivery versus inference

Sender waits for camera release, successful FFmpeg termination/final-publication proof, all local uploads ACKed and cleanup before server stop. On failure unacknowledged local files and context journal remain. Stop proof assumes trusted source software/operator; it is not hardware attestation. Context time bounds assume source UTC; no calibration accuracy claim.

Closed context streams with buffered raw remain visible to already-bound processors. A reader chooses its original stream rather than a concurrently started new stream, drains to declared final sequence, then stops without mixing context. New readers exclude stopped streams. Existing raw 30-second TTL/quota still applies; delivery ACK is NOT proof all frames were inferred. The EOS-aware mux drains every still-available acknowledged final chunk even with uneven camera endings. A live reference at primary EOS receives a reader-local frozen sequence cutoff, never a false source stop acknowledgement. Primary events close at the last primary frame; missing reference/post-roll evidence remains incomplete. Source-node reassignment can also end old owner's access. These limitations must not be described as complete evidence recovery.

## Deployment/UX work still separate

Frontend must show 409 stop-required state and guide stop ACK → zone PATCH → fresh start; no frontend edits in this candidate. Physical stop/restart, real camera performance, UTC calibration and PostgreSQL v31 runtime validation remain unverified. See the context launcher document for optional Python GUI integration and its separate installation status. Metadata/receipts are intentionally retained with no automatic purge; retention/capacity policy needs operational sizing. Test-only synthetic stream/media files are not deployable artifacts.
