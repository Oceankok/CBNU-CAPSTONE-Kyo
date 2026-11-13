# Explicit raw-tail availability, not unlimited retention

The backend retains the original 30-second raw TTL, 64 MiB camera budget,
256 MiB aggregate budget and 40-chunk camera count. No polling, download or
availability query extends retention. No expired clip is reconstructed or revived.

Why not simply raise TTL to 120 seconds? With one-second chunks, 40 slots fill
around 40 seconds while the first expiry would only occur at 120 seconds. The
current sender preserves unacknowledged files but treats HTTP 429 as a failed
run and requests capture stop; it does not provide a bounded live backpressure
queue. Blanket retention extension would introduce routine capture failures.
Changing retention safely needs bounded sender backoff/spool capacity and an
explicit privacy/capacity budget, not just a larger timeout. Stop-only extension
also competes with the next session's camera quota and is not enabled here.

## API

Authenticated processors may GET
`/api/processing/video/streams/{stream_id}/availability?after_sequence=N`.
The stream must have capture-context binding and still meet the current source
access rules. This works for an already-bound closed stream even after it is
absent from discovery. It does not disclose a filesystem path or credential.

Response binds stream/camera/context/session/version, highest received sequence,
next sequence and whether source stop was explicitly acknowledged. A stopped
stream includes exact final sequence. A cursor ahead of highest received is 409.

- `available`: receipt, unexpired raw DB record and file exist at observation.
- `unavailable`: missing receipt, expired raw row, missing raw row or file;
  a stable reason distinguishes these cases without inventing an expiry cause
  after cleanup has removed that evidence.
- `not_received`: requested sequence is beyond receipt boundary. It is not EOS
  unless source stop is independently verified and the consumer reached final.

A successful availability read is not a lease. Expiry or file failure can still
occur between observation and download. The processor must convert a resulting
404/410 to explicit evidence loss and preserve incomplete state.

## Processor behavior and limits

Bound stream loss and expected-sequence loss are explicit source failures, not
normal empty polls or successful completion. The old context is never replaced
with the newest stream. The source failure records only bounded administrative
metadata, never raw video or credentials, in the existing private durable state.
Events close incomplete when evidence is lost. A delivery receipt never proves inference success.

This change makes loss detectable and auditable. It does not guarantee media
survives arbitrary processing delay, nor restore video already deleted for
privacy. Real PostgreSQL/device performance and operating deployment remain
separate validation steps. No frontend/model or live device changes.
