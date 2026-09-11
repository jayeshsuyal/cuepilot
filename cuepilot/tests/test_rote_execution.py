"""Adapter flow checks with a synthetic CLI; these are not sponsor execution proof."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from cuepilot.integrations import rote
from cuepilot.tests.test_rote_adapter import BASE_URL, captures_fixture, export_fixture, replay_fixture, replay_output, run_fixture


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        authored = root / 'plays/stage-sequence'
        (authored / 'resources').mkdir(parents=True)
        shutil.copyfile(rote.AUTHORED / 'resources/cue.py', authored / 'resources/cue.py')
        shutil.copyfile(rote.AUTHORED / 'deps.toml', authored / 'deps.toml')
        for name, value in [('ROOT', root), ('PLAYS', root / 'plays'), ('AUTHORED', authored),
                            ('ACTIVE', root / 'plays/evidence/active.json'), ('RUNTIME', root / 'runtime')]:
            patched = patch.object(rote, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.cli = AsyncMock(side_effect=self.record_cli)
        self.status = unittest.mock.Mock()
        for name, value in [('_cli', self.cli), ('_run_status', self.status)]:
            patched = patch.object(rote, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        patched = patch.object(rote, '_inputs', side_effect=lambda run_id, base: (run_id, base, {}))
        patched.start()
        self.addCleanup(patched.stop)
        self.child_failure = False

    async def record_cli(self, args, cwd, env, evidence, label, **kwargs):
        if args[:2] == ['proc', 'run']:
            return 'response_id: @%s\nexit: code 0\n' % (int(args[-1]) + 1)
        if args[0] == 'query':
            index = int(args[1][1:]) - 1
            body = replay_fixture(captures_fixture(run_fixture(completed=True)))['steps'][index]['body']
            if self.child_failure:
                body['status']['exit']['code'] = 7
            return json.dumps(body)
        if args[:2] == ['workspace', 'export']:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / 'recorded-export.ts').write_text(export_fixture(rote.AUTHORED / 'resources/cue.py'))
        return ''

    async def learn(self):
        self.status.side_effect = [run_fixture(), run_fixture(completed=True)]
        return await rote.learn('run-recorded', BASE_URL)

    async def test_learning_then_new_speaker_replay_require_one_dag_and_matching_receipts(self):
        learned = await self.learn()
        self.assertEqual(learned['status'], 'verified', learned)
        procedure = learned['evidence']['procedure']
        self.assertEqual(learned['procedure'], procedure)
        package, proof = rote._active_package()
        self.assertEqual(procedure, rote._procedure(package, proof))
        self.assertEqual(learned['evidence']['receipts'], run_fixture(completed=True)['receipts'])
        before = run_fixture('run-fresh', 'speaker-two')
        after = run_fixture('run-fresh', 'speaker-two', completed=True)
        self.status.side_effect = [before, after]
        self.cli.reset_mock()
        self.cli.side_effect = None
        self.cli.return_value = replay_output(replay_fixture(captures_fixture(after)))
        result = await rote.replay(before['id'], BASE_URL)
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(result['evidence']['newSpeaker'])
        self.assertTrue(result['evidence']['newInput'])
        self.assertEqual(result['evidence']['procedure'], procedure)
        self.cli.assert_awaited_once()
        self.assertEqual(self.cli.call_args.args[0][:2], ['play', 'run'])
        self.assertFalse(result['evidence']['reconciliationRequired'])

    async def test_captured_nonzero_child_prevents_export_even_when_recorder_exits_zero(self):
        self.child_failure = True
        result = await self.learn()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'stage_cue_rejected')
        self.assertTrue(result['evidence']['reconciliationRequired'])
        self.assertFalse(rote.ACTIVE.exists())
        self.assertFalse(any(call.args[0][:2] == ['workspace', 'export'] for call in self.cli.await_args_list))

    async def test_failed_export_keeps_completed_stage_separate_from_learned_success(self):
        record_cli = self.record_cli
        async def failed_export(args, *args_rest, **kwargs):
            if args[:2] == ['workspace', 'export']:
                raise ValueError('rote_export_failed')
            return await record_cli(args, *args_rest, **kwargs)
        self.cli.side_effect = failed_export
        result = await self.learn()
        self.assertEqual(result['reason'], 'rote_export_failed')
        self.assertTrue(result['evidence']['executionAttempted'])
        self.assertTrue(result['evidence']['reconciliationRequired'])
        self.assertFalse(rote.ACTIVE.exists())

    async def test_aggregate_deadline_returns_reconciliation_and_releases_lock(self):
        async def slow_cli(*args, **kwargs):
            await asyncio.sleep(10)
        self.status.side_effect = [run_fixture()]
        self.cli.side_effect = slow_cli
        with patch.object(rote, 'OPERATION_TIMEOUT', 0.02):
            result = await rote.learn('run-recorded', BASE_URL)
        self.assertEqual(result['reason'], 'rote_operation_timeout')
        self.assertFalse(result['evidence']['executionAttempted'])
        self.assertFalse(rote._lock.locked())

    async def test_busy_execution_fails_fast_without_waiting_or_touching_cli(self):
        async with rote._lock:
            result = await rote.replay('run-fresh', BASE_URL)
        self.assertEqual(result['reason'], 'rote_execution_busy')
        self.cli.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
