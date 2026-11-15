# Exclusive frame boundary precision

Local review follow-up to `cfa185a`; based on main `84022b7`. Not installed in the running demo or merged remotely.

The preserved native transition for `EVT_e7fe515667174d63a36640d38c6dd2fb` reconstructs writer origin `1794737406.712333`, exclusive end `1794737418.245667`, and 15 fps. Their difference represents `173.0000102519989` frames. The previous `1e-5` frame check rejects this boundary before encoder finalization. The independent read-only diagnosis is retained at the demo's `inference/frame-grid-diagnosis.json`; the original journal is unchanged.

`TimestampSegmentWriter.finish` now compares the end to its nearest canonical frame boundary in seconds, allowing the larger of the previous frame tolerance and one microsecond. It emits only to that validated canonical boundary. Merely enlarging the tolerance while emitting to the original timestamp could add a 174th frame; the new regression verifies exactly 173 encoded and decoded frames. No observation, detection threshold, automatic rule or existing artifact is changed.

The original exclusive-end/max-input-gap validation still runs first. Real off-grid ends (including two-microsecond errors at 15 fps and a half-frame boundary) remain rejected. At microsecond precision a canonical end can differ from the supplied timestamp by at most the bounded tolerance; no whole frame is added. This change does not repair the preserved failed outbox, prove native end-to-end recovery, or address other capture failures.

With the existing FFmpeg tools directory on PATH, `python -B -m unittest inference_worker.test_segment_spool -v` passes all 27 tests with no skips in 1.555 seconds. This includes three new regressions, actual FFmpeg encoding and OpenCV full frame decoding, and existing quota, expiry, input-gap, timeout and durable retry checks. Raw output: `.private_media/observation-precision/frame-grid-regression.log`.

Adjacent capture/context regression: python -B -m unittest inference_worker.test_multicamera_capture inference_worker.test_processor_capture_context -v passes all 30 tests, no skips, in 1.304 seconds; log frame-grid-capture-regression.log. git diff --check passes.
