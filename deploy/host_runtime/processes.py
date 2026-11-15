"""Own a launched process tree, including encoders, without killing by process name."""
import os
import signal
import subprocess
import sys


class ProcessTrees:
    def __init__(self):
        self.jobs = {}

    def spawn(self, command, **kwargs):
        if os.name != 'nt':
            child = subprocess.Popen(command, start_new_session=True, **kwargs)
            self.jobs[child] = None
            return child
        # The guard cannot start the requested command until it is in our job.
        # Descendants inherit the job, including if the direct parent later dies.
        from ctypes import WinDLL, wintypes
        import ctypes
        kernel = WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [('per_process', ctypes.c_int64), ('per_job', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read', 'write', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]

        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        child = None
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            child = subprocess.Popen([sys.executable, '-m', 'deploy.host_runtime.processes', '--guard', *command],
                                     stdin=subprocess.PIPE, **kwargs)
            if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(child._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            child.stdin.write(b'1')
            child.stdin.flush()
            child.stdin.close()
            self.jobs[child] = (kernel, job)
            return child
        except BaseException:
            if child is not None:
                if child.stdin and not child.stdin.closed:
                    child.stdin.close()
                if child.poll() is None:
                    child.kill()
                child.wait()
            kernel.CloseHandle(job)
            raise

    def release(self, child):
        """Retire a dead/reaped slot and any descendants it left behind."""
        if child not in self.jobs:
            return
        job = self.jobs.pop(child)
        if os.name == 'nt':
            kernel, handle = job
            try:
                if not kernel.TerminateJobObject(handle, 1):
                    import ctypes
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(handle)
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        children = list(self.jobs)
        try:
            for child in children:
                self.release(child)
        finally:
            for child in children:
                child.wait()


def guarded_command(command):
    if sys.stdin.buffer.read(1) != b'1':
        return 78  # Owner failed before assigning containment; never launch.
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != '--guard':
        raise SystemExit(2)
    raise SystemExit(guarded_command(sys.argv[2:]))
