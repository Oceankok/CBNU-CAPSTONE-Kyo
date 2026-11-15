"""Real OS children, synthetic heartbeats only: no camera, GPU, HTTP or database."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from inference_worker.run_multi import supervise


CHILD = r'''
from pathlib import Path
import os, sys, time
root, role = Path(sys.argv[1]), sys.argv[2]
with (root/(role+'.starts')).open('a') as f:
    f.write(str(os.getpid())+'\n')
while not (root/'stop').exists():
    marker = root/(role+'.exit')
    if marker.exists():
        code = int(marker.read_text())
        if (root/'once').exists():
            marker.unlink()
        raise SystemExit(code)
    with (root/(role+'.ticks')).open('a') as f:
        f.write('tick\n')
    time.sleep(.03)
'''


class SupervisorProcessTests(unittest.TestCase):
    def scenario(self, name, *, role='a', recover=False, launch_error=False,
                 normal=False, stop_backoff=False, fatal=False, all_dead=False,
                 policy='isolate'):
        with TemporaryDirectory(prefix='g3-process-test-') as folder:
            root = Path(folder)
            stop = root/'stop'
            children, calls, errors, evidence = [], [], [], {}
            real_popen = subprocess.Popen

            def spawn(command, **kwargs):
                slot = command[-1]
                calls.append(slot)
                if launch_error and slot == role and calls.count(slot) > 1:
                    raise OSError('synthetic relaunch failure')
                child = real_popen(command, **kwargs)
                children.append((slot, child))
                return child

            def count(slot):
                path = root/(slot+'.ticks')
                return len(path.read_text().splitlines()) if path.exists() else 0

            def wait_for(predicate):
                deadline = time.monotonic()+10
                while not predicate():
                    if time.monotonic() > deadline:
                        raise AssertionError('child milestone timed out')
                    time.sleep(.02)

            def control():
                try:
                    wait_for(lambda: all(count(slot) >= 2 for slot in ('a', 'b', 'reference')))
                    if recover:
                        (root/'once').touch()
                    (root/(role+'.exit')).write_text('78' if fatal else '0' if normal else '1')
                    if all_dead:
                        (root/'b.exit').write_text('1')
                    wait_for(lambda: any(slot == role and child.poll() is not None for slot, child in children))
                    evidence['healthy_before'] = count('b')
                    if fatal or all_dead or policy == 'fail-fast':
                        return
                    if not normal and not stop_backoff:
                        wait_for(lambda: calls.count(role) == 2)
                        if recover:
                            wait_for(lambda: count(role) >= 5)
                        elif not launch_error:
                            wait_for(lambda: all(child.poll() is not None for slot, child in children if slot == role))
                    evidence['healthy_before'] = count('b')
                    # More than one supervisor poll after failure/exhaustion.
                    wait_for(lambda: count('b') >= evidence['healthy_before']+25)
                    evidence['healthy_after'] = count('b')
                except BaseException as error:
                    errors.append(error)
                finally:
                    if not (fatal or all_dead or policy == 'fail-fast') or errors:
                        stop.touch()

            controller = threading.Thread(target=control, daemon=True)
            watchdog = threading.Timer(15, stop.touch)
            watchdog.daemon = True
            controller.start()
            watchdog.start()
            failure = None
            try:
                with patch('inference_worker.run_multi.subprocess.Popen', side_effect=spawn):
                    try:
                        supervise([[sys.executable, '-c', CHILD, str(root), slot]
                                   for slot in ('a', 'b', 'reference')], stop,
                                  camera_count=2, restart_limit=0 if all_dead or policy == 'fail-fast' else 1,
                                  restart_delay=5 if stop_backoff else .2,
                                  reference_restart_limit=1, reference_restart_delay=.2,
                                  failure_policy=policy)
                    except RuntimeError as error:
                        failure = str(error)
            finally:
                stop.touch()
                watchdog.cancel()
                controller.join(timeout=11)
                survivors = [child.pid for _, child in children if child.poll() is None]
                for _, child in children:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            self.assertFalse(controller.is_alive())
            self.assertEqual(survivors, [], 'supervisor left live children before harness cleanup')
            if errors:
                raise errors[0]
            if fatal or all_dead or policy == 'fail-fast':
                self.assertIsNotNone(failure)
            else:
                self.assertIsNone(failure)
                self.assertGreater(evidence['healthy_after'], evidence['healthy_before'])
            self.assertEqual(calls.count('b'), 1)
            expected = 1 if normal or stop_backoff or fatal or all_dead or policy == 'fail-fast' else 2
            self.assertEqual(calls.count(role), expected)
            self.assertTrue(all(child.returncode is not None for _, child in children))
            evidence.update(scenario=name, launches=calls,
                            reaped_pids=[child.pid for _, child in children], orphans=0)
            print('[G3 ACCEPTANCE] '+json.dumps(evidence), flush=True)

    def test_camera_recovery(self):
        self.scenario('camera_recovers', recover=True)

    def test_camera_exhaustion(self):
        self.scenario('camera_exhausted')

    def test_camera_relaunch_failure(self):
        self.scenario('camera_launch_error', launch_error=True)

    def test_reference_exhaustion(self):
        self.scenario('reference_exhausted', role='reference')

    def test_stop_during_backoff(self):
        self.scenario('stop_backoff', stop_backoff=True)

    def test_normal_exit(self):
        self.scenario('normal_exit', normal=True)

    def test_fatal_error(self):
        self.scenario('fatal_configuration', fatal=True)

    def test_all_cameras_dead(self):
        self.scenario('all_cameras_dead', all_dead=True)

    def test_fail_fast(self):
        # Explicit zero budgets select historical immediate peer shutdown.
        self.scenario('fail_fast', policy='fail-fast')


if __name__ == '__main__':
    unittest.main()
