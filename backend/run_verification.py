"""
백엔드 기능 검증 스크립트를 순서대로 실행하는 runner.

기본 실행:
    python -m backend.run_verification

실제 음성 출력 포함 실행:
    python -m backend.run_verification --include-audio
"""

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_command(title: str, command: list[str]) -> None:
    """검증 명령어를 실행하고 실패 시 전체 runner를 중단함."""
    print(f"\n{'=' * 60}")
    print(f"[RUN] {title}")
    print(f"{'=' * 60}")

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(f"Verification failed: {title}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run backend verification scripts."
    )
    parser.add_argument(
        "--include-audio",
        action="store_true",
        help="Run tests that output actual TTS audio.",
    )
    args = parser.parse_args()

    python = sys.executable

    run_command(
        "Initialize database",
        [python, "backend/db/init_db.py"],
    )

    run_command(
        "Event repository test",
        [python, "backend/db/test_event_repository.py"],
    )

    run_command(
        "Event re-review API regression test",
        [python, "-m", "backend.api.test_event_rereview_api"],
    )

    run_command(
        "Candidate event service test",
        [python, "-m", "backend.services.test_candidate_event_service"],
    )

    if args.include_audio:
        run_command(
            "TTS service test",
            [python, "-m", "backend.services.test_tts_service"],
        )

        run_command(
            "Warning broadcast service test",
            [python, "-m", "backend.services.test_warning_broadcast_service"],
        )
    else:
        print(
            "\n[SKIP] Audio tests skipped. "
            "Run with --include-audio to verify TTS and warning broadcast output."
        )

    print("\n[OK] Backend verification completed successfully.")


if __name__ == "__main__":
    main()