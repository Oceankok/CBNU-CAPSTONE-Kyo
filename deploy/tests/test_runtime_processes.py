"""Real OS process-tree cleanup; only synthetic children, no operating services."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from deploy.host_runtime.processes import ProcessTrees
from inference_worker.run_multi import run_preflight, supervise


FAMILY = r'''
import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'])
Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]))
if len(sys.argv)>2:
    time.sleep(.3)
    os._exit(1)
time.sleep(90)
'''


def alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # On Linux a killed orphan may remain a zombie until init reaps it.
        stat = Path(f'/proc/{pid}/stat')
        return not (stat.exists() and stat.read_text().split()[2] == 'Z')
    except ProcessLookupError:
        return False


def wait_for(predicate, seconds=8):
    deadline = time.monotonic()+seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Process milestone timed out')
        time.sleep(.02)


class RuntimeProcessTests(unittest.TestCase):
    def test_release_failure_still_cleans_and_reaps_all_owned_peers(self):
        with TemporaryDirectory() as folder:
            trees = ProcessTrees()
            markers = [Path(folder)/f'family-{index}.json' for index in range(2)]
            children = [trees.spawn([sys.executable, '-c', FAMILY, str(marker)]) for marker in markers]
            release = trees.release
            attempts = []
            def fail_first(child):
                attempts.append(child)
                if len(attempts) == 1:
                    raise OSError('injected release failure before cleanup')
                return release(child)
            try:
                wait_for(lambda: all(marker.exists() for marker in markers))
                pids = sum([json.loads(marker.read_text()) for marker in markers], [])
                with patch.object(trees, 'release', side_effect=fail_first):
                    with self.assertRaisesRegex(RuntimeError, 'all trees were attempted'):
                        trees.close()
                self.assertTrue(all(child.returncode is not None for child in children))
                self.assertTrue(all(child in attempts for child in children))
                wait_for(lambda: all(not alive(pid) for pid in pids))
                self.assertFalse(trees.jobs)
                print('[RUNTIME TREE] injected-release-failure peers-reaped orphan0', flush=True)
            finally:
                trees.close()

    def test_profile_failure_leaves_healthy_primary_processing_and_reaps_encoders(self):
        worker = r'''
import json,os,subprocess,sys,time
from pathlib import Path
root,role=Path(sys.argv[1]),sys.argv[2]
encoder=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'])
(root/(role+'.family')).write_text(json.dumps([os.getpid(),encoder.pid]))
started=time.monotonic()
while not (root/'stop').exists():
    if role=='a' and time.monotonic()-started>.3: os._exit(1)
    with (root/(role+'.ticks')).open('a') as f:f.write('tick\n')
    time.sleep(.03)
'''
        with TemporaryDirectory() as folder:
            root = Path(folder)
            stop, errors, evidence = root/'stop', [], {}
            def count():
                path = root/'b.ticks'
                return len(path.read_text().splitlines()) if path.exists() else 0
            def controller():
                try:
                    wait_for(lambda: (root/'a.family').exists() and (root/'b.family').exists())
                    failed = json.loads((root/'a.family').read_text())
                    wait_for(lambda: all(not alive(pid) for pid in failed))
                    evidence['healthy_before'] = count()
                    wait_for(lambda: count() >= evidence['healthy_before']+25)
                    evidence['healthy_after'] = count()
                except BaseException as error:
                    errors.append(error)
                finally:
                    stop.touch()
            thread = threading.Thread(target=controller, daemon=True)
            thread.start()
            supervise([[sys.executable, '-c', worker, str(root), role] for role in ('a', 'b')], stop,
                      camera_count=2, failure_policy='isolate', process_trees=True, shutdown_grace_seconds=.5)
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]
            all_pids = sum([json.loads((root/(role+'.family')).read_text()) for role in ('a', 'b')], [])
            wait_for(lambda: all(not alive(pid) for pid in all_pids))
            self.assertGreater(evidence['healthy_after'], evidence['healthy_before'])
            print('[RUNTIME TREE] profile isolation '+json.dumps(evidence)+' encoder orphan0', flush=True)

    def test_owned_tree_kills_grandchild_even_after_parent_exits(self):
        with TemporaryDirectory() as folder:
            marker = Path(folder)/'family.json'
            trees = ProcessTrees()
            child = trees.spawn([sys.executable, '-c', FAMILY, str(marker), 'abrupt-exit'])
            try:
                wait_for(marker.exists)
                pids = json.loads(marker.read_text())
                self.assertTrue(all(alive(pid) for pid in pids))
                # Requested process abruptly exits; its grandchild remains live.
                child.wait(timeout=8)
                trees.release(child)
                wait_for(lambda: all(not alive(pid) for pid in pids))
                print('[RUNTIME TREE] dead-parent cleanup orphan0', flush=True)
            finally:
                trees.close()

    def test_preflight_deadline_cleans_full_tree(self):
        with TemporaryDirectory() as folder:
            marker = Path(folder)/'family.json'
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                run_preflight([sys.executable, '-c', FAMILY, str(marker)], timeout=1.5, process_trees=True)
            self.assertTrue(marker.exists())
            pids = json.loads(marker.read_text())
            wait_for(lambda: all(not alive(pid) for pid in pids))
            print('[RUNTIME TREE] preflight-timeout orphan0', flush=True)

    def test_external_stop_cancels_preflight_and_cleans_full_tree(self):
        with TemporaryDirectory() as folder:
            marker, stop = Path(folder)/'family.json', Path(folder)/'stop'
            errors = []
            def stop_after_start():
                try:
                    wait_for(marker.exists)
                    stop.touch()
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=stop_after_start, daemon=True)
            thread.start()
            result = run_preflight([sys.executable, '-c', FAMILY, str(marker)], timeout=10,
                                   stop_file=stop, process_trees=True)
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive()); self.assertFalse(errors); self.assertFalse(result)
            pids = json.loads(marker.read_text())
            wait_for(lambda: all(not alive(pid) for pid in pids))
            print('[RUNTIME TREE] external-stop orphan0', flush=True)

    def test_existing_stop_and_invalid_deadline_launch_nothing(self):
        with TemporaryDirectory() as folder:
            marker, stop = Path(folder)/'unused', Path(folder)/'stop'
            stop.touch()
            command = [sys.executable, '-c', FAMILY, str(marker)]
            self.assertFalse(run_preflight(command, stop_file=stop, process_trees=True))
            for timeout in (0, -1, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    run_preflight(command, timeout=timeout, process_trees=True)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
