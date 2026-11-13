# Optional reference collector process recovery

`run_multi` retains fail-fast by default. To retry its single reference collector
without restarting healthy camera inference processes, explicitly supply both:

```
--reference-restart-limit 3 --reference-restart-delay 5
```

These are example operator choices, not calibrated service defaults. The budget
is total launches after failure over the supervisor lifetime, not reset by a
short successful period. Camera retry settings remain independent. A stop signal
during backoff cancels the launch. Exhaustion stops all peers rather than silently
continuing indefinitely without reference service. Initial startup failures still
fail fast. No credentials appear in arguments or restart logs.

## Continuity and limits

A restarted reference collector polls the server's persisted pending requests; it
does not invent footage, reset request deadlines, bypass frame-gap checks, or
change privacy processing. Missing or expired intervals retain the existing
unavailable handling. Already accepted uploads retain their existing idempotent
request handling. This patch changes process supervision only, not clip assembly.

Healthy camera processes retain their in-memory tracker and ongoing incident
state during a reference-only restart. This is **not** tracker recovery after a
camera inference process itself crashes. Such a process restart still loses its
in-memory track identity; do not claim an old and new track are one incident.
Reference recovery also cannot guarantee footage remains in retention long enough
for a delayed retry. Real cameras, GPU, crash recovery of active incidents, and
redaction quality require separate integration validation.

Synthetic supervisor tests cover independent retries, exhausted budgets, launch
errors, stop during backoff and invalid policy. They do not launch a real camera.

An abrupt collector crash can leave raw decoder temporary files inside the
supervisor-owned temporary directory until supervisor shutdown cleanup. Restart
itself does not erase those files or make them available through an API. This is
an existing crash-cleanup limitation; restarting is not a new privacy guarantee.
