# Bound-source availability and durable loss diagnostics

The continuous reader never swaps a missing bound stream for a newer stream.
It queries `GET /api/processing/video/streams/{stream_id}/availability` with
`after_sequence` equal to its consumed cursor. The VideoClient method is
`stream_availability(stream_id, after_sequence)`. A 404 returns no metadata;
other HTTP errors fail closed. Responses must match the immutable camera,
stream, capture context, expected next sequence and non-regressing received
cursor. `not_received` is permitted only beyond the received cursor.

Missing discovery rows are recovered **before** primary EOS and finite reference
cutoff decisions. `available` allows normal draining; `unavailable` records a
known source loss. `not_received`, empty responses without an advertised upload,
or timeouts never authorize EOS. Existing stall limits remain active.

An advertised uploaded sequence with no chunk, a sequence gap, or a download
404/410 raises `source_chunk_unavailable`. A bound stream absent from both
interfaces raises `source_stream_unavailable`. Neither alone proves expiry.
Only a server-supplied availability reason records `raw_expired`, `raw_missing`,
`raw_unavailable`, or `receipt_missing`. Other values are not copied into audit.

Before cleanup, the runner records reason, camera, stream, expected sequence and
verified availability reason in the local `source_failures` ledger. One failure
per session is retained with the existing database-byte and record-count caps.
The normal interruption path closes active events incomplete using the same
reason; pending metadata and sealed source obligations remain. No raw bytes are
regenerated, no TTL is extended by the processor, and no newer context is joined.
If durable storage is full, recording fails visibly; cleanup is still attempted.

This is an inference-only candidate. Backend retention/availability behavior,
actual DB and device validation, and new-context restart are separate concerns.
