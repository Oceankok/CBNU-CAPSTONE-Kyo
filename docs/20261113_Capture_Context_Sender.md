# Opt-in capture-context sender candidate

This is a separate runner, not enabled by existing Start-Camera.ps1 or the GUI. Backend capture-session support must be enabled and deployed together. No operating deployment or actual camera test is claimed.

From repository root, with the existing server origin, camera ID and node token loaded in environment:

```
python -m video_sender.context_sender --directory <new-private-session-folder> --camera-index 0
```

The directory must not already exist. A context and idempotent stream ID are obtained before opening the camera. The capture child receives no node token. Ctrl+C in the runner requests graceful capture shutdown; it waits for camera release, FFmpeg exit and completed publication proof. Only after every sequence is acknowledged and local clip cleanup succeeds does the runner post the authenticated stop acknowledgement. A failed, hung or forcibly killed capture never becomes confirmed stopped.

Network and validation failures do not discard unacknowledged clips. The capture is asked to stop, context remains open and the journal preserves exact context/stream/sequence. If capture completed successfully, resume drainage without restarting the camera:

```
python -m video_sender.context_sender --directory <same-folder> --resume-drain
```

If a connection fails before capture has ever been marked started, `--resume-start` retries the same persisted context/session/stream identifiers. It refuses any run already marked capture-started, including an uncertain crash between marking and spawning.

The server origin must match the journal. OS locks prevent concurrent CLI runners. A journal marked capture-started cannot start another capture; a new session requires the previous server context to be retired. There is deliberately no timeout-as-stop, forced unlock, camera movement, or automatic unknown session retirement. If capture completion proof is absent after a crash, an operator must establish that capture is stopped and follow the server's explicit recovery policy; this runner will not guess.

The context UUID is an administrative identifier, not a bearer secret. Sender authentication still uses the existing node credential. Local proof/journal files assume a trusted local operator and are not hardware attestation. They should not be manually edited to bypass checks. Unsent raw clips may remain in the private session directory after failure; do not use a public/shared folder. This prototype does not claim a finished offline data-retention or manual recovery UI.

Backend contract:
- POST capture-sessions(camera_id, session_id), same owner retry returns same context.
- POST streams(camera_id, capture_context_id, stream_id), same identity retry preserves stream.
- Upload remains under that stream; context must be retained across network failures.
- POST capture-sessions/{context_id}/stop(capture_stopped=true, final_sequences={stream_id:last_acknowledged_sequence}), server verifies exact stream inventory and final sequences before returning retired=true.

Legacy capture and sender behavior remains unchanged unless this new runner supplies VIDEO_CAPTURE_RUN_ID. Model, frontend and detector weights are untouched.
