# Reference collector process recovery

Since the 2026-11-15 G3 change, `run_multi` defaults to isolation and bounded
recovery of its single reference collector without restarting healthy cameras:

```
--reference-restart-limit 3 --reference-restart-delay 5
```

These are initial defaults, not calibrated recovery timings. The budget
is total launches after failure over the supervisor lifetime, not reset by a
short successful period. Camera retry settings remain independent. A stop signal
during backoff cancels the launch. Exhaustion logs REFERENCE UNAVAILABLE and leaves
healthy cameras running. Zero exit is not restarted under isolation. All cameras
inactive, shared preflight failure, initial launch failure, or fatal child exit
2/78 stops the group. Use `--failure-policy fail-fast --camera-restart-limit 0
--reference-restart-limit 0` for the historical immediate-stop behavior. Positive
budgets with fail-fast retain the historical retry-then-stop contract. No credentials
appear in arguments or restart logs.

## Continuity and limits

A restarted reference collector polls the server's persisted pending requests; it
does not invent footage, reset request deadlines, bypass frame-gap checks, or
change privacy processing. Missing or expired intervals retain the existing
unavailable handling. Media-less pending requests time out through existing
maintenance after 300 seconds without activity and subject to segment deadlines.
This is `unavailable / clip_delivery_timeout`, not an immediate collection-incomplete
report. The media API returns these requests; the event media panel shows them per
camera even if another video is playable. Pending/unavailable requests continue
to block automatic decision evidence checks. Already accepted uploads retain their
existing idempotent handling. Clip assembly is unchanged.

Healthy camera processes retain their in-memory tracker and ongoing incident
state during a reference-only restart. This is **not** tracker recovery after a
camera inference process itself crashes. Such a process restart still loses its
in-memory track identity; do not claim an old and new track are one incident.
Reference recovery also cannot guarantee footage remains in retention long enough
for a delayed retry. Real cameras, GPU, crash recovery of active incidents, and
redaction quality require separate integration validation.

Unit and real OS synthetic-child tests cover independent retries, exhausted budgets,
launch errors, stop during backoff, fatal failures, all-camera shutdown and invalid
policy. They do not launch a real camera. See [G3 validation](20261115_Camera_Fault_Isolation.md).

An abrupt collector crash can leave raw decoder temporary files inside the
supervisor-owned temporary directory until supervisor shutdown cleanup. Restart
itself does not erase those files or make them available through an API. This is
an existing crash-cleanup limitation; restarting is not a new privacy guarantee.
