# Capture-context desktop starter candidate

This is an additive opt-in GUI, not a replacement for the user's existing
`camera_launcher.py`. After Library ZIP download failed with HTTP 503, the
original source was read via the authorized local filesystem, without execution,
UI interaction or modification. Its validate/read_token functions are reused by
the bridge. No local installation or default launcher switch has occurred.

Run on the camera sender PC, not the inference server:

    python -m video_sender.context_launcher_bridge

The bridge loads existing ~/KyoCameraLauncher/launcher-settings.json and
camera_launcher.py, validates existing repo/config/output/ffmpeg/index settings,
and uses its unchanged Windows DPAPI read_token function. An alternate existing
launcher directory can be supplied via --launcher-root. The selected repository
must contain the updated context_sender module. Existing settings are not edited.
The GUI acquires the original Local\KyoCameraLauncherV1 Windows mutex and
refuses to open while the original launcher remains open. Do not run manually
started legacy capture scripts concurrently; they do not participate in this lock.

For environments that already supply credentials directly:

    python -m video_sender.context_starter

For direct context_starter invocation, a trusted configuration loader must supply VIDEO_SERVER_URL,
VIDEO_CAMERA_ID and VIDEO_NODE_TOKEN to the process environment. Do not paste
secrets into arguments, source files, shared logs or plaintext config. The bridge delegates DPAPI to the original reader and never saves tokens.
Use the same Python environment with sender dependencies, FFmpeg and Tk.
The backend capture-context feature must be enabled explicitly beforehand.

## Workflow

- New session: private fresh directory under ~/KyoCameraSessions; backend
  authenticates context and stream before capture.
- Stop: writes explicit stop marker; waits for completed outbox and server ACK.
  Process exit, missing heartbeat, timeout, window closing are NOT stop proof.
- Only journal.closed=true plus successful runner exit displays ACK confirmed.
- Change zone in the existing authenticated administrator web interface.
- Start a new session; backend binds the new assignment context.
- Connection recovery only permits a journal that never started capture.
- Transmission recovery never restarts camera and requires a completion file.
  The underlying sender verifies that file and exact final sequence inventory.
- No forced stop/unlock or automatic device kill. Unresolved sessions remain
  locked and need recovery; raw clips can remain in the private outbox.
- Closing the GUI while its child is running is blocked. Unexpected OS shutdown
  is still an unconfirmed capture and cannot safely imply stop.

## Validation

Fourteen headless controller/bridge tests pass: secret-free argv, overlap prevention,
early stop request, zero-exit without ACK, ACK plus exit gating, resume-start
safety, resume-drain proof requirement and malformed journal rejection.
The copied original validate implementation is exercised against synthetic config
with credential decryption mocked; original launcher source is an optional local
reference test fixture, not shipped as a replacement. Tk display, actual Windows
camera/FFmpeg and real DPAPI decryption have not been tested. No production DB, frontend or model changes.

If child startup fails before journal creation, reopen the GUI after verifying the
child has exited; the current window intentionally does not infer safe restart.
