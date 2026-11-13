# Offline complete-scenario collector (opt-in, not operational)

This standalone command processes an explicitly selected **local, constant-FPS
video** with an existing trusted model. It does not open a live camera, connect to
the backend, create events, invoke automatic review, or modify existing runners.
It is for safe solo demonstration recordings, never for removing necessary PPE
near operating machinery. Existing detector/tracker/association code is unchanged.

```sh
python -m inference_worker.collect_scenario \
  --video /path/to/local-demo.mp4 --model /path/to/trusted-best.pt \
  --output /path/to/new-observations.jsonl \
  --scenario-id solo01 --camera-id demo01 \
  --helmet-label worn --vest-label not_worn --assume-constant-fps
```

Labels are mandatory per scenario (`worn`, `not_worn`, `ambiguous`). Declare them
before examining output; they are not automatically verified truth for every
track. Use one intended participant, check the source recording independently,
and mark unexpected people/visibility changes rather than pooling their labels.
No calibrated threshold or automatic-decision confidence is produced.

## Minimized output
Exclusive-create JSONL header, all frame records and footer. Each frame retains
relative time, session-local track ID and helmet/vest assessment status only.
Normal, unknown and no-person frames are retained, not filtered to suspected
violation events. No pixels, face crops, bounding boxes, source path or person
names are saved. Do not put personal names in scenario/camera identifiers.
Existing model-version provenance is retained (the detector's shortened hash).
The source video remains at its original path and is not copied or deleted.

Default processing limit: 3,600 frames and 120 seconds. Explicitly chosen limits
remain capped at 100,000 frames and 600 seconds. Larger/longer inputs fail with an
incomplete footer rather than silently produce a full-episode claim. Output is
never overwritten. New files request owner-only permissions where supported by
the operating system; Windows ACL behavior should be checked on the target PC.
No automatic retention job is installed. Keep only approved diagnostic results
for the current experiment and let the owner decide when to remove them.

## Completion and timing
Only a footer with `completed: true` means collection reached the declared EOF.
Exceptions leave `completed: false`; abrupt process loss may leave no footer.
Consumers MUST reject either as a complete episode. Declared frame count is
compared to decoded count; a codec metadata mismatch fails closed and requires
source inspection, not assuming the missing frames were normal. This check does
not prove corruption-free video or independently correct source metadata.

Timestamps use frame index / declared FPS, which is why constant-FPS assumption
must be explicit. Variable-rate, discontinuous or dropped-time footage cannot
be evaluated correctly by this command. Original capture clock, event identity
and cross-camera identity are not inferred. Track loss/reacquisition is retained
as local track IDs and must be reviewed; IDs are not human identity guarantees.

## Interpretation and remaining work
`unknown` is the current geometric heuristic, not independently verified body
visibility. Positive-box absence is not proof of non-wearing. The collector is an
observation source for the offline absence/replay tools but does not automatically
convert records into labelled event scores; scenario labels are not track labels.
Real source recordings, association/visibility checks and an explicit acceptance
policy are still required. Automatic confirmation remains OFF. This code has
synthetic tests; actual camera, GPU/model inference and target-PC run are untested.
