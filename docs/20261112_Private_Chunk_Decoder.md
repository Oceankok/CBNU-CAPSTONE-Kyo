# Opt-in private chunk decoder

PrivateChunkDecoder(owner_outbox, explicit limits) is callable with the legacy
`decode(data, with_timing=False, duration_ms=None)` interface. It does not modify
the legacy decoder or live runner. Its only raw-file directory is
`owner_outbox.directory/decode`, protected by the parent's lifetime process lock
and single serialized decoder owner. Files use UUID names and private creation
permissions. Windows ACL inheritance still requires deployment verification.

Before raw writing, a FULL-synchronous SQLite ownership/audit row records expected
bytes, SHA-256, creation time and reserved identity. O_EXCL creation prevents
replacement; device/inode ownership is persisted before writing. Writes are bounded
by input length; completed files are decoded through cv2 with frame count/FPS/
dimensions/duration checks and per-frame pixel/count checks. Pre-read dimension
metadata is checked before frame allocation. Metadata is not a trusted memory
sandbox: malformed codecs or native OpenCV operations can still allocate or block.
Hard memory/CPU/decode-time isolation requires a separate supervised worker process.

Finally cleanup persists its disposition before unlink. Failure leaves a retryable
cleanup_pending record; maintain retries it. Restart cleans only journal-owned
UUID files with matching identity. Unknown UUIDs, symlinks, invalid manifest paths,
replaced files and files whose identity was not committed remain manual recovery.
A kill between file creation and identity commit therefore fails closed rather
than guessing ownership. Existing audit rows are bounded, never silently dropped;
audit admission stops until the caller durably reports and acknowledges completed
records. Restart/discard/TTL outcomes must become incomplete-input evidence at the
parent, not successful capture.

All byte/frame/pixel/FPS/tolerance/TTL/audit/database limits are explicit. There is
at most one raw input copy on disk per decoder. Reserve separately for the caller's
in-memory source bytes and that disk copy, plus bounded decoded frames retained by
the consumer, cv2/native allocations, SQLite main file and up to another metadata
budget for rollback journal. Upstream bytearray-to-bytes conversion can temporarily
add another complete input-sized allocation. This module does not cap process RSS.

A generator must be exhausted or explicitly closed. While paused, the owner must
call maintain on schedule: TTL expiry releases cv2, records privacy expiration and
deletes the known source; resume then fails. Close the decoder before releasing its
outbox owner. A hard-killed process cannot perform cleanup until restart or another
authorized supervisor. No public paths, upload, remote network, or anonymous temp
files are used. Tests emulate unclean process death and cover valid MP4, write and
unlink failure, TTL, malicious manifest path, unknown/symlink files and limits.
