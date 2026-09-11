"""Exercise Rote subprocess cancellation without invoking Rote or a stage server."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cuepilot.integrations import rote


@unittest.skipUnless(os.name == 'posix', 'Rote desktop adapter uses POSIX process groups')
class ProcessLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_creation_task_reports_unconfirmed_cleanup_without_spinning(self):
        creation_started = asyncio.Event()
        creation = None

        async def blocked_creation(*args, **kwargs):
            nonlocal creation
            creation = asyncio.current_task()
            creation_started.set()
            await asyncio.sleep(60)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(rote.asyncio, 'create_subprocess_exec', blocked_creation):
            folder = Path(directory)
            task = asyncio.create_task(rote._cli([], folder, {}, folder, 'shutdown'))
            await creation_started.wait()
            # Event-loop shutdown can cancel the otherwise shielded child task.
            creation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            report = json.loads((folder / 'shutdown.json').read_text())
            self.assertFalse(report['cleanupConfirmed'])
            self.assertIsNone(report['exitCode'])

    async def test_timeout_kills_group_and_drains_redacted_evidence(self):
        await self._interrupt(cancel=False)

    async def test_cancellation_kills_group_and_reraises_before_return(self):
        await self._interrupt(cancel=True)

    async def test_cancellation_during_pipe_setup_kills_child_and_reaps_parent(self):
        await self._interrupt_during_creation(cancel_count=1)

    async def test_repeated_cancellation_during_pipe_setup_still_finishes_cleanup(self):
        await self._interrupt_during_creation(cancel_count=2)

    async def _interrupt_during_creation(self, cancel_count):
        loop = asyncio.get_running_loop()
        connect_read_pipe = loop.connect_read_pipe
        pipe_ready = asyncio.Event()
        release_pipe = asyncio.Event()

        async def delayed_pipe(*args, **kwargs):
            connected = await connect_read_pipe(*args, **kwargs)
            pipe_ready.set()
            await release_pipe.wait()
            return connected

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            pid_path = folder / 'creation-pids.json'
            source = (
                'import json,os,pathlib,subprocess,sys,time\n'
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],'
                'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                'pathlib.Path(sys.argv[1]+".tmp").write_text(json.dumps([os.getpid(),child.pid]))\n'
                'os.replace(sys.argv[1]+".tmp",sys.argv[1])\n'
                'time.sleep(30)\n'
            )
            parent_pid = None
            task = None
            try:
                with patch.object(rote, 'ROTE', Path(sys.executable)), \
                        patch.object(loop, 'connect_read_pipe', delayed_pipe):
                    task = asyncio.create_task(rote._cli(
                        ['-c', source, str(pid_path)], folder, os.environ.copy(),
                        folder, 'creation', timeout=10))
                    await asyncio.wait_for(pipe_ready.wait(), 2)
                    for _ in range(100):
                        if pid_path.exists():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(pid_path.exists(), 'child did not start during pipe setup')
                    parent_pid, child_pid = json.loads(pid_path.read_text())
                    # Cancel before create_subprocess_exec returns its Process.
                    # A second cancellation arrives while the adapter recovers it.
                    for _ in range(cancel_count):
                        task.cancel()
                        await asyncio.sleep(0)
                    release_pipe.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                report = json.loads((folder / 'creation.json').read_text())
                self.assertEqual(report['interrupted'], 'cancelled')
                self.assertTrue(report['cleanupConfirmed'])
                self.assertIsNotNone(report['exitCode'])
                with self.assertRaises(ProcessLookupError):
                    os.kill(parent_pid, 0)
                state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)],
                                       capture_output=True, text=True, check=False).stdout.strip()
                self.assertTrue(not state or state.startswith('Z'), state)
            finally:
                release_pipe.set()
                # Clean up the intentionally exposed race even if regression fails.
                if parent_pid is not None:
                    try:
                        os.killpg(parent_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, 2)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                await asyncio.sleep(0.05)

    async def _interrupt(self, cancel):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            pid_path = folder / 'pids.json'
            # This local Python process stands in for Rote and spawns a cue child.
            source = (
                'import json,os,pathlib,subprocess,sys,time\n'
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
                'print(os.environ["CUEPILOT_BRIDGE_TOKEN"],flush=True)\n'
                'pathlib.Path(sys.argv[1]+".tmp").write_text(json.dumps([os.getpid(),child.pid]))\n'
                'os.replace(sys.argv[1]+".tmp",sys.argv[1])\n'
                'time.sleep(60)\n'
            )
            token = 'test-bridge-secret-not-a-credential'
            with patch.object(rote, 'ROTE', Path(sys.executable)):
                task = asyncio.create_task(rote._cli(
                    ['-c', source, str(pid_path)], folder,
                    {**os.environ, 'CUEPILOT_BRIDGE_TOKEN': token}, folder, 'lifecycle',
                    timeout=10 if cancel else 0.5))
                for _ in range(100):
                    if pid_path.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(pid_path.exists(), 'subprocess never reached ready state')
                parent_pid, child_pid = json.loads(pid_path.read_text())
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaisesRegex(ValueError, 'rote_timeout'):
                        await task
            report = json.loads((folder / 'lifecycle.json').read_text())
            self.assertEqual(report['interrupted'], 'cancelled' if cancel else 'timeout')
            self.assertTrue(report['cleanupConfirmed'])
            self.assertIsNotNone(report['exitCode'])
            self.assertNotIn(token, report['stdout'])
            self.assertIn('[REDACTED]', report['stdout'])
            with self.assertRaises(ProcessLookupError):
                os.kill(parent_pid, 0)  # asyncio must have reaped the CLI parent.
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)],
                                   capture_output=True, text=True, check=False).stdout.strip()
            # A killed orphan may briefly await OS reaping; it must never run.
            self.assertTrue(not state or state.startswith('Z'), state)

    async def test_cli_child_nonzero_is_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rote, 'ROTE', Path(sys.executable)):
            with self.assertRaisesRegex(ValueError, 'rote_nonzero_failed'):
                await rote._cli(['-c', 'raise SystemExit(4)'], Path(directory), os.environ.copy(),
                                Path(directory), 'nonzero')
            self.assertEqual(json.loads((Path(directory) / 'nonzero.json').read_text())['exitCode'], 4)


if __name__ == '__main__':
    unittest.main()
