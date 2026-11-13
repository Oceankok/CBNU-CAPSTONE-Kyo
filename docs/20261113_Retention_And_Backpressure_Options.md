# Separate opt-in retention and backpressure candidate

Default behavior is unchanged. No environment setting, local installation,
operational database, device, frontend or detector was changed by this candidate.

## Recommended initial option

Keep backend raw retention at **30 seconds** and enable the sender protection
only after choosing local storage values. This is bounded best-effort delivery
plus explicit failure, not a lossless continuous transport. No automatic budget
selection or activation is included.

Example to discuss, not applied: 1 GiB local soft threshold with 256 MiB reserved
headroom and 2 GiB minimum free space. The stop warning threshold is therefore
768 MiB in the current session directory; free-space protection also reserves
headroom. Prior failed sessions remain untouched and still consume real disk.
The policy is a polling-based protective stop, NOT an OS hard quota. FFmpeg
buffers, in-flight writes and a nonresponsive camera/process can overshoot.
An actual hard storage guarantee requires separately managed OS/filesystem quota.

Unacknowledged raw files remain for recovery. They may include identifiable people
and do not automatically expire or get deleted on failure. Use a private local
location and explicitly review/recover/delete abandoned sessions. A failed drain
must not be described as completed transmission or a confirmed camera stop.

## Backend retention option

`PPE_CONTEXT_RAW_RETENTION_SECONDS` accepts only `30` (default), `60`, or `120`.
It affects future accepted capture-context chunks only. Each has an absolute
expiry from its first server receipt. Existing chunk deadlines never change;
reads, retries and stop acknowledgements do not renew them. Legacy ingest remains
30 seconds. Invalid settings reject new context uploads instead of accepting an
unbounded retention value.

The stream response's legacy `buffer_seconds=30` remains the baseline/legacy
buffer value. Additive `raw_retention_seconds_for_new_uploads` describes the
currently configured policy for future context uploads, not prior chunks or a
persistent stream identity. It can change after explicit server reconfiguration;
such a change is not retroactive and does not prove old media remains available.

Raw retention 120 seconds increases pre-deidentification storage duration fourfold.
It does NOT increase the existing 64 MiB per-camera / 256 MiB aggregate tracked
raw quotas or 40-chunk camera limit. These are application accounting limits, not
OS disk quotas; temporary uploads and crash-orphan cleanup still have overhead.

With 1-second chunks, 40 slots fill in roughly 40 seconds, before a 120-second
first expiry. Even a fast processor cannot free those chunks early because no
multi-consumer durable consumption ACK protocol exists. With 3-second chunks,
40 slots span about 120 seconds, but bytes may fill sooner. The 64 MiB / 120-second
budget permits only about 4.47 Mbps average encoded payload per camera (17.9 Mbps
aggregate), excluding overhead and uneven bursts. These are sizing ceilings,
not guarantees.

Therefore choose 120 seconds only after measuring chunk sizes and processing
latency, preferably with 3-second chunks and sufficient current budgets. Bounded
429 retry can still exhaust and safely stop; a longer TTL is not inherently safer.
To support arbitrarily slower consumers would require a separate, explicit
multi-consumer durable consumption protocol and privacy/capacity policy. Deleting
raw on mere download ACK would break crash recovery or other reference consumers
and is deliberately not implemented.
