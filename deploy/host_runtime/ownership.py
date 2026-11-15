"""Same-host, same-user primary ownership. Not a distributed camera lease."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile


@contextmanager
def primary_ownership(server, primaries, directory=None):
    root = Path(directory or (Path(tempfile.gettempdir()) / 'kyo-inference-owners'))
    root.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for camera in sorted(set(primaries)):
            key = hashlib.sha256((server.rstrip('/')+'\0'+camera).encode()).hexdigest()
            path = root / (key+'.lock')
            if path.is_symlink():
                raise RuntimeError('Unsafe local primary lock')
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if os.name == 'nt':
                    import msvcrt
                    if not os.fstat(fd).st_size:
                        os.write(fd, b'0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                raise RuntimeError('Primary is already owned locally; stop the existing owner first') from None
            handles.append(fd)
        yield
    finally:
        for fd in reversed(handles):
            os.close(fd)

