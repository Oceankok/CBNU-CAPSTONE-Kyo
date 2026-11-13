# Continuous EOS delivery tail

Fixes an opt-in runner integration gap: the last source batch could seal multiple
camera segments, while one runtime pump attempted only one upload. The next
reader poll reported verified EOS and exited, leaving the rest queued even with
a healthy backend.

On `session_stopped` or `primary_source_ended`, the runner additionally requires
all configured mux sources ended and an empty frame queue. Only then it performs
bounded delivery-only pumps, metadata ACK handling, verified receipt cleanup and
release. It never polls a new stream, restarts a camera, performs new inference,
changes the model, changes a decision policy, or extends evidence retention.
Reference drain cutoff remains a cutoff, not physical source-stop evidence.

The additional tail allows at most 64 pumps and checks a 30-second wall budget
between pumps. Existing per-operation transport/encoder bounds remain in force;
the wall budget does not interrupt a call already in flight. A pump with no
observable delivery/cleanup progress stops immediately. No infinite network
retry is introduced. Ambiguous receipts retain the existing recovery behavior.

The local summary contains counts only. `drained` means delivery and cleanup
obligations finished, **not** that every event has complete usable evidence.
The separate `incomplete` count remains explicit. Pending metadata, close intents,
uploads, raw cleanup/recovery, or live capture records prevent a drained result.
Budget exhaustion or blocked delivery raises `eos_delivery_pending`; the durable
journals and existing recovery obligations remain. Source loss, timeout and
unverified EOS do not enter this normal-stop path.

Validated with synthetic two-camera real-FFmpeg segment creation plus mocked
transport, and actual runner control-flow tests with mocked device/network IO.
No operating database, camera, Windows GUI or production deployment was touched.
