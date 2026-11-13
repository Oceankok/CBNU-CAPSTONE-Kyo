> Superseded status: stop → acknowledge → reassign → fresh context is now approved and implemented in the opt-in candidate. See `20261113_Capture_Context_Integration.md`. Historical proposal below is not current deployment status.

# Capture-context validation preparation

This is an unconnected pure validation module, not deployed capture-time zone attribution.

A trusted server context binds camera, session, assignment version and administrative zone. A capture interval is supported only when it fits explicit effective boundaries with a trusted finite clock-error bound. Missing context, unknown clock, mismatch or boundary uncertainty returns unknown; current zone is never guessed.

The module does not issue/authenticate tokens, persist history, alter event snapshots, change statistics, or prove physical camera placement. Existing and legacy events are unchanged.

Before integration the owner must choose a reassignment contract. Recommended safe approach: retire the current capture session explicitly and issue a new server context before recording in the new assignment. Offline senders and uncertain physical move times cannot be retroactively inferred. Session interruption and clock verification need real integration and tests.

The historical event registration snapshot remains the operational compatibility path; this candidate does not silently replace it.
