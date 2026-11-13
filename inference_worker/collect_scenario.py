"""Collect diagnostic statuses from an explicitly supplied local constant-FPS video."""
import argparse
from pathlib import Path
from inference_worker.scenario_collector import collect_scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--scenario-id', required=True)
    parser.add_argument('--camera-id', required=True)
    for ppe in ('helmet', 'vest'):
        parser.add_argument('--'+ppe+'-label', required=True,
                            choices=('worn', 'not_worn', 'ambiguous'))
    parser.add_argument('--assume-constant-fps', action='store_true', required=True,
                        help='Explicitly accept frame-index/FPS timing; not valid for variable-FPS footage')
    parser.add_argument('--max-frames', type=int, default=3600)
    parser.add_argument('--max-seconds', type=float, default=120)
    args = parser.parse_args()
    video, model, output = (Path(p).resolve() for p in (args.video, args.model, args.output))
    if not video.is_file() or not model.is_file():
        parser.error('local video and existing trusted model files required')
    if output in (video, model) or output.exists():
        parser.error('output must be a new file, not an input')
    import cv2
    import math
    from inference_worker.ppe_detector import PPEDetector
    capture = cv2.VideoCapture(str(video))
    try:
        if not capture.isOpened(): raise ValueError('cannot open local video')
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0: raise ValueError('invalid FPS')
        expected_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(expected_frames) or expected_frames < 2:
            raise ValueError('reliable declared frame count required for completeness check')
        expected_frames = round(expected_frames)
        # No camera/network input: video was verified to be a local file above.
        detector = PPEDetector(model_path=model)
        def frames():
            index = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    if index != expected_frames:
                        raise ValueError('decoded frame count differs from declared source count')
                    break
                yield index / fps, frame
                index += 1
        summary = collect_scenario(frames(), detector, output,
            scenario_id=args.scenario_id, camera_id=args.camera_id,
            labels={'helmet': args.helmet_label, 'vest': args.vest_label},
            max_frames=args.max_frames, max_seconds=args.max_seconds,
            timing_basis='explicit_constant_fps_frame_index')
        print(f"Collected {summary['frame_count']} frames; diagnostic only; automatic confirmation OFF")
    finally:
        capture.release()


if __name__ == '__main__': main()
