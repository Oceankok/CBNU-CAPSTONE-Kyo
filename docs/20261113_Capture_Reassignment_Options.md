> Superseded status: stop → acknowledge → reassign → fresh context is now approved and implemented in the opt-in candidate. See `20261113_Capture_Context_Integration.md`. Historical proposal below is not current deployment status.

# W03 reassignment policy options (not implemented)

Current operational behavior remains registration-time zone snapshots. The pure capture-context validation candidate is NOT wired into issuance, receiver, sender or DB history. It cannot retrospectively recover unknown camera locations.

## Option A: stop, acknowledge, reassign, restart (recommended for this capstone)

1. An administrator stops recording/streaming for the camera; the server receives the stop acknowledgement and closes the active capture session/context.
2. The administrator changes the zone and physically positions the camera as needed.
3. Starting the sender obtains a new server-issued camera/session/assignment-version context and only then begins new capture.
4. Pending uploads keep their original immutable context. Data without a verifiable old context remains unknown/legacy registration attribution, never guessed from the newest zone.

Reject an administrative reassignment while a camera session is still active. A crashed/offline sender needs an explicit session-retirement procedure, not an assumption that a heartbeat timeout proves recording stopped. Restart must invalidate cached authorization before new capture. This adds operational interruption but is the simplest, most explainable protocol for a small demonstration and reduces boundary ambiguity.

## Option B: coordinated online rollover

Allow reassignment after the sender receives and acknowledges a server boundary, finishes/splits the old episode, and begins a new session/context. Preserve upload retries under the old context. Network loss, delayed acknowledgement, active tracker events, clock uncertainty and partial failures require a state machine. More seamless but significantly more complex; server save time alone is not a trustworthy physical relocation time.

## Option C: receipt-time attribution (current compatibility behavior)

Keep registration snapshots. It safely preserves registered events through later camera moves but does not solve capture-before-first-registration movement. Document this limitation rather than marking W03 complete.

## Decision and validation gates

Choose A or B before connecting this module. Define whether reassignment requires a recorded sender acknowledgement, who may retire a crashed session, and how clock error is established. Server contexts are administrative evidence, not proof of physical placement. Missing/untrusted clock bounds, intervals crossing retirement, stale session/version, offline context or ambiguous timestamps must not silently fall back to current zone.

Test cases: delayed old upload after reassignment, sender restart with stale context, offline move, failed stop acknowledgement, boundary timestamps with bounded clock uncertainty, multiple cameras in the same zone, active event at stop, and concurrent admin requests. No past rows are rewritten automatically.
