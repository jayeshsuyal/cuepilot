import concurrent.futures
import hashlib
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from cuepilot.api import create_app


class CuePilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / 'db.sqlite3', operator_token='operator-test', bridge_token='bridge-test')
        self.client = TestClient(self.app)
        self.operator = {'Authorization': 'Bearer operator-test'}
        self.bridge = {'Authorization': 'Bearer bridge-test'}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create(self, speaker='maya', approve=True):
        response = self.client.post('/api/v1/runs', json={'speakerId': speaker, 'executionMode': 'practice'}, headers=self.operator)
        self.assertEqual(response.status_code, 201, response.text)
        run = response.json()
        if approve:
            response = self.client.post(f"/api/v1/runs/{run['id']}/approve", json={'planHash': run['plan']['hash']}, headers=self.operator)
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()
        return run

    def cue(self, run_id, step, request_id):
        return self.client.post('/api/v1/tools/stage/cue', json={'runId':run_id,'stepIndex':step,'requestId':request_id}, headers=self.bridge)

    def test_practice_has_explicit_fixture_origin_and_three_real_receipts(self):
        run = self.create()
        self.assertEqual(run['plan']['origin'], 'fixture')
        self.assertEqual(run['traces'][0]['status'], 'fixture')
        for step, scene in enumerate(['intro','presentation','holding']):
            response = self.cue(run['id'], step, f'step-{step}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['ok'])
            self.assertEqual(self.client.get('/api/v1/stage').json()['scene'], scene)
        result = self.client.get(f"/api/v1/runs/{run['id']}").json()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['receipts']), 3)

    def test_mutation_tokens_are_scoped(self):
        payload = {'speakerId':'maya'}
        self.assertEqual(self.client.post('/api/v1/runs', json=payload).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/runs', json=payload, headers=self.bridge).status_code, 401)
        run = self.create()
        response = self.client.post('/api/v1/tools/stage/cue', json={'runId':run['id'],'stepIndex':0,'requestId':'a'}, headers=self.operator)
        self.assertEqual(response.status_code, 401)

    def test_unapproved_and_out_of_order_cues_do_not_mutate_stage(self):
        run = self.create(approve=False)
        self.assertEqual(self.cue(run['id'],0,'a').status_code,409)
        self.client.post(f"/api/v1/runs/{run['id']}/approve",json={'planHash':run['plan']['hash']},headers=self.operator)
        self.assertEqual(self.cue(run['id'],1,'b').status_code,409)
        self.assertEqual(self.client.get('/api/v1/stage').json()['revision'],0)

    def test_approval_requires_exact_plan_and_current_revision(self):
        run = self.create(approve=False)
        url = f"/api/v1/runs/{run['id']}/approve"
        self.assertEqual(self.client.post(url,json={'planHash':'0'*64},headers=self.operator).status_code,409)
        self.client.patch('/api/v1/assets/slides-ravi',json={'status':'missing','expectedRevision':1},headers=self.operator)
        self.assertEqual(self.client.post(url,json={'planHash':run['plan']['hash']},headers=self.operator).status_code,409)

    def test_missing_asset_holds_immediately_and_blocks_old_sequence(self):
        run = self.create()
        self.assertEqual(self.cue(run['id'],0,'a').status_code,200)
        response = self.client.patch('/api/v1/assets/slides-maya',json={'status':'missing','expectedRevision':1},headers=self.operator)
        self.assertEqual(response.status_code,200)
        stage = self.client.get('/api/v1/stage').json()
        self.assertEqual(stage['scene'],'holding')
        self.assertIn('unavailable',stage['reason'])
        self.assertEqual(self.cue(run['id'],1,'b').status_code,409)
        current = self.client.get(f"/api/v1/runs/{run['id']}").json()
        self.assertEqual(current['status'],'blocked')
        self.assertEqual(len(current['receipts']),1)

    def test_retry_returns_original_receipt_even_after_readiness_changes(self):
        run = self.create()
        first = self.cue(run['id'],0,'same').json()
        self.client.patch('/api/v1/assets/slides-maya',json={'status':'missing','expectedRevision':1},headers=self.operator)
        response = self.cue(run['id'],0,'same')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),first)
        self.assertEqual(self.client.get('/api/v1/stage').json()['scene'],'holding')

    def test_operator_retry_does_not_advance_next_cue(self):
        run = self.create()
        url = f"/api/v1/runs/{run['id']}/advance"
        first = self.client.post(url,json={'requestId':'same'},headers=self.operator).json()
        second = self.client.post(url,json={'requestId':'same'},headers=self.operator).json()
        self.assertEqual(first,second)
        self.assertEqual(self.client.get(f"/api/v1/runs/{run['id']}").json()['nextStep'],1)

    def test_delayed_operator_intent_keeps_its_step_after_another_tab_advances(self):
        run = self.create()
        url = f"/api/v1/runs/{run['id']}/advance"
        other_tab = self.client.post(url, json={'requestId': 'other-tab', 'stepIndex': 0}, headers=self.operator)
        self.assertEqual(other_tab.status_code, 200)
        delayed = self.client.post(url, json={'requestId': 'delayed-intent', 'stepIndex': 0}, headers=self.operator)
        self.assertEqual(delayed.json(), other_tab.json())
        self.assertEqual(self.client.get(f"/api/v1/runs/{run['id']}").json()['nextStep'], 1)
        wrong_step = self.client.post(url, json={'requestId': 'delayed-intent', 'stepIndex': 1}, headers=self.operator)
        self.assertEqual(wrong_step.status_code, 409)
        self.assertEqual(self.client.get('/api/v1/stage').json()['scene'], 'intro')

    def test_concurrent_retry_commits_one_cue(self):
        run = self.create()
        store = self.app.state.store
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:store.cue(run['id'],0,'retry'),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(store.get_run(run['id'])['receipts']),1)

    def test_two_speakers_cannot_interleave_stage_ownership(self):
        first, second = self.create('maya'), self.create('ravi')
        self.assertEqual(self.cue(first['id'],0,'a').status_code,200)
        response = self.cue(second['id'],0,'b')
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['code'],'stage_busy')

    def test_live_plan_cannot_use_fixture_or_stale_sponsor_results(self):
        store = self.app.state.store
        run = store.create_run('maya','Synthetic note','live')
        with self.assertRaisesRegex(Exception,'Live planning needs'):
            store.plan(run['id'])
        for provider in ['cognee','hydradb','hotdata']:
            store.add_trace(run['id'],{'provider':provider,'status':'verified','operation':'unit-test-transport','evidence':{},'supported_template':'speaker-segment-v1','note_sha256':hashlib.sha256(run['notes'].encode()).hexdigest()})
        proof = {'status':'verified','records':[], 'supported_template':'speaker-segment-v1',
                 'note_sha256':hashlib.sha256(run['notes'].encode()).hexdigest(),
                 'graph_sha256':'a'*64, 'recipe_id':'graph-receipt', 'source_id':run['showId']}
        store.add_trace(run['id'],proof,'ingest-memory')
        store.add_trace(run['id'],proof,'recall-recipe')
        with self.assertRaisesRegex(Exception,'Show state changed after'):
            store.plan(run['id'])

    def test_body_limit_and_strict_fields(self):
        response = self.client.post('/api/v1/runs',json={'speakerId':'maya','extra':'unexpected'},headers=self.operator)
        self.assertEqual(response.status_code,422)
        response = self.client.post('/api/v1/runs',content=b'x'*32769,headers=self.operator)
        self.assertEqual(response.status_code,413)

    def test_rejected_practice_creation_leaves_no_queued_run_or_stage_change(self):
        for group, item, field, unavailable, ready, error in (
            ('assets', 'slides-maya', 'status', 'missing', 'ready', 'presentation_unavailable'),
            ('speakers', 'maya', 'ready', False, True, 'speaker_unavailable'),
        ):
            with self.subTest(readiness=group):
                before_runs = self.client.get('/api/v1/runs').json()
                before_stage = self.client.get('/api/v1/stage').json()
                revision = self.client.get('/api/v1/show').json()['revision']
                changed = self.client.patch(f'/api/v1/{group}/{item}',
                    json={field: unavailable, 'expectedRevision': revision}, headers=self.operator)
                self.assertEqual(changed.status_code, 200)
                response = self.client.post('/api/v1/runs',
                    json={'speakerId': 'maya', 'executionMode': 'practice'}, headers=self.operator)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['detail']['code'], error)
                self.assertEqual(self.client.get('/api/v1/runs').json(), before_runs)
                self.assertEqual(self.client.get('/api/v1/stage').json(), before_stage)
                restored = self.client.patch(f'/api/v1/{group}/{item}',
                    json={field: ready, 'expectedRevision': changed.json()['revision']}, headers=self.operator)
                self.assertEqual(restored.status_code, 200)
                recovered = self.create(approve=False)
                self.assertEqual(recovered['status'], 'needs_approval')
                self.assertIsNotNone(recovered['plan'])

    def test_cleanup_failure_cannot_erase_completed_cue_receipts(self):
        run = self.create()
        for step in range(3):
            self.assertEqual(self.cue(run['id'],step,f'cue-{step}').status_code,200)
        self.app.state.store.add_trace(run['id'],{'provider':'rocketride','status':'blocked','operation':'cleanup','reason':'termination_unconfirmed','evidence':{}})
        result = self.app.state.store.get_run(run['id'])
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(result['receipts']),3)
        self.assertEqual(result['traces'][-1]['status'],'blocked')

    def test_operator_cancel_holds_active_segment_without_erasing_receipt(self):
        run = self.create()
        first = self.cue(run['id'], 0, 'intro').json()
        route = f"/api/v1/runs/{run['id']}/cancel"
        self.assertEqual(self.client.post(route, headers=self.bridge).status_code, 401)
        cancelled = self.client.post(route, headers=self.operator).json()
        self.assertEqual(cancelled['status'], 'blocked')
        self.assertEqual(cancelled['receipts'], [first])
        self.assertEqual(self.client.get('/api/v1/stage').json()['scene'], 'holding')
        self.assertEqual(self.cue(run['id'], 1, 'next').status_code, 409)
        self.assertEqual(self.client.post(route, headers=self.operator).json(), cancelled)

    def test_orchestration_phase_is_claimed_once_before_subprocess_dispatch(self):
        store = self.app.state.store
        run = store.create_run('maya', 'Synthetic note', 'live')
        store.claim_orchestration(run['id'], 'prepare')
        with self.assertRaisesRegex(Exception, 'already been dispatched'):
            store.claim_orchestration(run['id'], 'prepare')
        restarted = create_app(Path(self.temp.name) / 'db.sqlite3', operator_token='operator-test', bridge_token='bridge-test')
        self.assertEqual(restarted.state.store.get_run(run['id'])['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
