# Private continuous upload adapter (not live wired)

`continuous_transport.py` uploads only an explicitly bound event/request/camera
source. The caller must first durably save that binding and verify the final
capture range. No adapter is instantiated by the existing runner.

The file must be a regular, nonsymlink source below the dedicated private root,
with exact expected size and SHA-256. A bounded private temporary snapshot is
copied and verified before any network send, so concurrent source changes cannot
replace the uploaded bytes. Root ownership must be exclusive: this does not
claim protection against a privileged attacker racing ancestor directories.

Only HTTPS or explicit local-loopback HTTP is allowed. Environment proxies are
not inherited, redirects are never followed, and secrets/private paths/server
error bodies are not included in raised errors. Response identity encoding and
bounded chunks are required. Event, camera and clip-request IDs must match the
server receipt; processing/ready state is validated. A processing receipt means
private server custody, NOT successful redaction or public readiness.

The adapter never deletes the original source. Outbox must persist the verified
receipt before cleanup. A lost response must be resolved through the server's
existing request/media binding: this upload endpoint returns a conflict for a
received request, so blind resubmission or inventing a new request is not a safe
recovery algorithm.

Explicit per-I/O and whole-operation time limits apply. Deadline checks occur
between I/O operations; one blocked operation can consume its I/O timeout beyond
the total deadline. A supervisor must provide hard cancellation/TTL and cleanup
for process termination. Reserve one additional max_upload_bytes disk allowance
per active upload snapshot, alongside SQLite journal, sources and encoder space.
The adapter is single-owner and does not enforce the cross-process global budget.

Tests cover private receipt/no deletion, path/hash/size/range/ID rejection,
symlink refusal, redirects, oversized/error responses, wrong receipt binding,
and source mutation after snapshot. An isolated real API test also generated a
7-second MP4, uploaded via this adapter to the actual processing endpoint,
verified processing state with no public URL and inaccessible raw paths, and
confirmed the local original remained. This did not run a real face redactor or
prove redaction quality. API result/publication tests remain a separate gate.

## Ambiguous custody and manual recovery

An HTTP 409 is not delivery proof. Recovery requires an authenticated same-owner
lookup binding the event, session, request, camera and media to the exact raw
SHA-256 and byte count. Without a server-persisted content identity, leave custody
unknown, do not re-upload under a new identity and do not perform success cleanup.
The legacy response fields alone do not establish that stronger content binding.

Unknown custody must not mean unlimited raw retention. A configured privacy TTL
may create a durable incomplete/expired cleanup obligation, but the outbox does
not execute deletion. Operator recovery must distinguish privacy-expiry disposal
from delivery-ack cleanup, preserve the failure record, and never claim a disposed
unknown source was delivered. The operational TTL/disposal policy and supervised
cleanup path must be settled before enabling the runner.

### Receipt recovery implementation

The paired backend now persists immutable source SHA-256/byte-count receipts in
its successful ingest transaction. `recover_receipt` queries the owner/session
endpoint and verifies event/request/camera/role/content identity and optional
previous media ID. It returns historical acceptance separately from current
source_state; expired/deleted/unavailable never authorize automatic re-upload or
success cleanup. HTTP 404 returns unknown, including administrator reprocessing
that has no original successful receipt. No receipt is synthesized retroactively.

An isolated API test now injects a lost response after actual source ingestion,
then recovers using only the durable plan and server receipt. Wrong content hash
is rejected, a wrong session receives 403, and TTL expiry preserves historical
acceptance without extending source lifetime or deleting the local original.
The transport does not run outbox acknowledgments or deletion automatically.
