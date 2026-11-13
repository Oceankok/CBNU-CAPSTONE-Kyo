"""Opt-in desktop controls for context_sender; never changes camera assignments.

Run from repository root: python -m video_sender.context_starter
Credentials must already be set in environment; never printed or saved by GUI.
"""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4


class Starter:
    def __init__(self, root, *, executable=None, spawn=None, repository=None):
        self.root = Path(root).resolve()
        self.repository = Path(repository).resolve() if repository else Path(__file__).resolve().parents[1]
        self.executable = executable or sys.executable
        self.spawn = spawn or subprocess.Popen
        self.process = None
        self.directory = None
        self.mode = None
        self.stop_pending = False

    def journal(self):
        if self.directory is None:
            raise ValueError('세션 폴더를 선택하세요.')
        path = self.directory / 'session.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        for key in ('run_id', 'session_id', 'stream_id'):
            if str(UUID(state[key])) != state[key]:
                raise ValueError('세션 식별자가 올바르지 않습니다.')
        if type(state.get('closed')) is not bool or type(state.get('capture_started')) is not bool:
            raise ValueError('세션 상태가 올바르지 않습니다.')
        return state

    def active(self):
        return self.process is not None and self.process.poll() is None

    def start(self, directory=None, *, mode='new', camera_index=0):
        if self.active():
            raise ValueError('실행 중인 작업을 먼저 종료하고 ACK를 확인하세요.')
        if mode not in ('new', 'resume-start', 'resume-drain'):
            raise ValueError('지원하지 않는 실행 모드입니다.')
        if not 0 <= camera_index <= 99:
            raise ValueError('카메라 번호는 0~99입니다.')
        # A failed local process is not server stop acknowledgement. Do not
        # switch away from its unresolved session via the new-session button.
        if self.directory is not None and mode == 'new':
            # Missing state alone cannot prove that a previous session was safe.
            # Only our new-mode child can positively report pre-session failure.
            try:
                closed = self.journal()['closed']
            except FileNotFoundError:
                if not self.pre_session_failure():
                    raise
            else:
                if not closed:
                    raise ValueError('이전 세션의 종료 ACK가 없습니다. 복구를 먼저 진행하세요.')
        target = Path(directory).resolve() if directory else self.root / str(uuid4())
        previous = self.directory
        self.directory = target
        try:
            if mode == 'new':
                if target.exists():
                    raise ValueError('새 세션에는 존재하지 않는 폴더가 필요합니다.')
            else:
                state = self.journal()
                if state['closed']:
                    raise ValueError('이미 종료 ACK를 받은 세션입니다. 새로 시작하세요.')
                if mode == 'resume-start' and state['capture_started']:
                    raise ValueError('이미 촬영을 시작한 세션은 다시 촬영할 수 없습니다.')
                if mode == 'resume-drain' and not (target / f".capture-result-{state['run_id']}.json").exists():
                    raise ValueError('촬영 종료 증거가 없어 전송 복구를 진행할 수 없습니다.')
            args = [self.executable, '-m', 'video_sender.context_sender', '--directory', str(target),
                    '--camera-index', str(camera_index)]
            if mode != 'new':
                args.append('--' + mode)
            # Never put token in argv or show unsanitized exceptions/output.
            self.process = self.spawn(args, cwd=str(self.repository),
                                      env=os.environ.copy(), stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.mode = mode
            self.stop_pending = False
        except Exception:
            self.directory = previous
            raise

    def pre_session_failure(self):
        # Keep the code local so importing this lightweight GUI does not load
        # sender/media dependencies. context_sender uses the same reserved code.
        return (self.mode == 'new' and self.process is not None
                and self.process.poll() == 78
                and self.directory is not None
                and not (self.directory / 'session.json').exists())

    def request_stop(self):
        if not self.active():
            raise ValueError('현재 이 창에서 실행한 프로세스가 없습니다. 종료 ACK를 확인하세요.')
        self.stop_pending = True
        self._publish_stop()

    def _publish_stop(self):
        try:
            state = self.journal()
        except FileNotFoundError:
            return  # Startup can still be persisting the journal; poll retries.
        (self.directory / f".stop-{state['run_id']}").touch(exist_ok=True)

    def status(self):
        if self.directory is None:
            return '대기: 새 세션 또는 기존 세션 복구를 선택하세요.'
        if self.stop_pending and self.active():
            self._publish_stop()
        try:
            state = self.journal()
        except FileNotFoundError:
            if self.pre_session_failure():
                return '시작 전 준비 실패: 서버 연결·촬영 전 종료했습니다. 설정을 확인하고 새로 시작하세요.'
            return ('시작 중: 세션 발급 대기' if self.active() else
                    '시작 실패: 세션 파일이 없습니다. 연결·환경 설정을 확인하세요.')
        active=self.active()  # Poll before inspecting returncode, including protective exits.
        reason={'spool_soft_stop_threshold':'임시 저장량 제한',
                'spool_free_space_reserve':'디스크 여유 공간 부족'}.get(state.get('protective_stop_reason'))
        if reason:
            if state['closed'] and self.process is not None and self.process.returncode == 0:
                return f'보호 중지: {reason}. 종료 ACK 확인. 설정·저장 공간 확인 후 다시 시작하세요.'
            return f'보호 중지: {reason}. 종료 ACK 미확인, 구역 변경 금지. 전송 복구가 필요합니다.'
        if active:
            return ('종료 요청 중: 파일 전송과 서버 ACK 대기. 아직 구역 변경 불가.' if self.stop_pending else
                    '작업 중: 촬영/전송/연결 단계. 구역 변경 불가.')
        if state['closed'] and self.process is not None and self.process.returncode == 0:
            return '종료 ACK 확인. 관리자 웹에서 구역 변경 후 새 세션을 시작하세요.'
        return '종료 미확인: 구역 변경 금지. 연결 복구 또는 전송 복구가 필요합니다.'


def _gui_main(*, session_root=None, camera_index=0, repository=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    window = tk.Tk(); window.title('Kyo Camera · Capture Context (opt-in)')
    controller = Starter(session_root or Path.home() / 'KyoCameraSessions', repository=repository)
    selected = tk.StringVar(); camera = tk.StringVar(value=str(camera_index)); status = tk.StringVar(value=controller.status())
    ttk.Label(window, text='카메라 송신 PC 전용 · 서버의 capture-context 기능 활성화 필요').pack(padx=16, pady=8)
    ttk.Label(window, text='전송 중지 → 종료 ACK 확인 → 관리자 웹에서 구역 변경 → 새 세션').pack(padx=16)
    row = ttk.Frame(window); row.pack(padx=16, pady=8)
    ttk.Label(row, text='카메라 번호').pack(side='left'); ttk.Entry(row, textvariable=camera, width=5).pack(side='left')
    ttk.Label(window, text='복구할 세션 폴더 (새 시작에는 사용하지 않음)').pack()
    ttk.Entry(window, textvariable=selected, width=65).pack(padx=16)
    ttk.Button(window, text='폴더 선택', command=lambda: selected.set(filedialog.askdirectory() or selected.get())).pack()

    def action(mode):
        try:
            if mode == 'stop': controller.request_stop()
            else:
                if mode != 'new' and not selected.get():
                    raise ValueError('복구할 세션 폴더를 먼저 선택하세요.')
                controller.start(None if mode == 'new' else selected.get(), mode=mode, camera_index=int(camera.get()))
                selected.set(str(controller.directory))
        except ValueError as exc:
            messagebox.showerror('실행 불가', str(exc))
        except Exception:
            messagebox.showerror('실행 불가', '설정·세션 파일·실행 환경을 확인하세요. 비밀정보 보호를 위해 오류 원문은 표시하지 않습니다.')
    buttons = ttk.Frame(window); buttons.pack(padx=16, pady=10)
    for label, mode in [('새 세션 시작', 'new'), ('전송 중지 요청', 'stop'),
                        ('연결 복구 (촬영 전만)', 'resume-start'), ('남은 전송 복구', 'resume-drain')]:
        ttk.Button(buttons, text=label, command=lambda m=mode: action(m)).pack(side='left')
    ttk.Label(window, textvariable=status, wraplength=620).pack(padx=16, pady=12)
    ttk.Label(window, text='이 창은 구역을 직접 변경하거나 중지 미확인 세션을 강제로 해제하지 않습니다.').pack(padx=16, pady=8)
    def tick():
        try: status.set(controller.status())
        except Exception: status.set('세션 상태 확인 실패. 종료로 간주하지 않습니다.')
        window.after(500, tick)
    def close():
        if controller.active():
            messagebox.showwarning('작업 중', '전송 중지를 요청한 뒤 작업 종료를 기다려 주세요. 창 닫기는 중지가 아닙니다.')
        else: window.destroy()
    window.protocol('WM_DELETE_WINDOW', close); tick(); window.mainloop()


@contextmanager
def launcher_lock(kernel=None):
    """Share the original legacy launcher's mutex; never start alongside it."""
    import ctypes
    if kernel is None and os.name != 'nt':
        yield
        return
    kernel = kernel or ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Local\\KyoCameraLauncherV1')
    if not handle:
        raise RuntimeError('Launcher lock unavailable')
    try:
        if kernel.GetLastError() == 183:
            raise ValueError('기존 카메라 스타터가 열려 있습니다. 먼저 종료하세요.')
        yield
    finally:
        kernel.CloseHandle(handle)


def main(*, session_root=None, camera_index=0, repository=None):
    with launcher_lock():
        _gui_main(session_root=session_root, camera_index=camera_index, repository=repository)


if __name__ == '__main__':
    main()
