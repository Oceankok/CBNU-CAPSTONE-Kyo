"""Opt-in bridge to installed KyoCameraLauncher configuration and DPAPI reader.

Run: python -m video_sender.context_launcher_bridge
Does not run legacy GUI/workers or modify their configuration/installation.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys


@contextmanager
def configured_environment(launcher_root, *, loader=None):
    root = Path(launcher_root).expanduser().resolve()
    if loader is None:
        spec = importlib.util.spec_from_file_location('kyo_installed_camera_launcher', root/'camera_launcher.py')
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
    saved = json.loads((root/'launcher-settings.json').read_text(encoding='utf-8'))
    # Only existing non-secret settings are accepted. Never serialize credentials.
    cfg = {key: saved[key] for key in ('repo', 'config', 'output', 'ffmpeg', 'index')}
    repo, config, output, settings, path = loader.validate(cfg)
    if not (repo/'video_sender'/'context_sender.py').is_file():
        raise ValueError('Configured repository lacks capture-context sender')
    token = loader.read_token(config)  # unchanged Windows-user-bound DPAPI reader
    if not token:
        raise ValueError('Missing camera credential')
    values = dict(PATH=path, VIDEO_NODE_TOKEN=token, VIDEO_SERVER_URL=settings['server_url'],
                  VIDEO_CAMERA_ID=settings['camera_id'])
    before = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield repo, output/'context-sessions', int(cfg['index'])
    finally:
        for key, value in before.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        token = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher-root', default=str(Path.home()/'KyoCameraLauncher'))
    args = parser.parse_args()
    try:
        with configured_environment(args.launcher_root) as (repo, sessions, index):
            from video_sender.context_starter import main as gui
            gui(session_root=sessions, camera_index=index, repository=repo)
    except Exception:
        # DPAPI/subprocess exception strings may contain credential material.
        import tkinter.messagebox
        tkinter.messagebox.showerror('실행 실패', '기존 스타터 설정·카메라 등록·업데이트된 저장소를 확인하세요. 비밀정보 보호를 위해 원문 오류는 표시하지 않습니다.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
