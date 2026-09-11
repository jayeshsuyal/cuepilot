"""Offline driver-boundary tests; these do not claim a live HydraDB run."""
import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from cuepilot.integrations import memory


NOTE = 'Introduction precedes Presentation; Presentation precedes Holding. Unavailable speaker or presentation falls back to Holding.'


def envelope():
    graph = memory._normalize_graph({
        'nodes': [{'id': 'cloud-' + str(i), 'label': label, 'type': 'Entity',
                   'properties': {'original': label, 'nested': {'source': 'production-note'}}}
                  for i, label in enumerate(['Introduction', 'Presentation', 'Holding', 'Unavailable speaker or presentation'])],
        'edges': [{'source': 'cloud-0', 'target': 'cloud-1', 'label': 'precedes'},
                  {'source': 'cloud-1', 'target': 'cloud-2', 'label': 'precedes'},
                  {'source': 'cloud-3', 'target': 'cloud-2', 'label': 'fallback_to'}]})
    return {'version': 2, 'receipt_id': uuid4().hex, 'graph': graph, 'proof': memory._prove_template(graph),
            'provenance': {'provider': 'cognee', 'dataset_id': str(uuid4()), 'dataset_name': 'synthetic-test',
                           'source_id': 'show-test', 'ingest_completed': True, 'note_sha256': memory._digest(NOTE),
                           'graph_sha256': memory._digest(memory._json(graph)), 'exported_at': '2026-09-11T20:00:00+00:00'}}


class DriverBoundary:
    """Stores driver parameters as vertices/edges; reads expose independent state.

    This deliberately has no recipe-derived graph responses. Corrupting a vertex
    or relationship affects traversal while the published envelope stays intact.
    """
    def __init__(self):
        self.nodes, self.edges, self.recipes, self.outcomes = {}, [], [], {}
        self.drop_import_edge = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def session(self, **kwargs):
        return self

    async def query(self, session, query, **p):
        if query.startswith('UNWIND $rows AS row MERGE'):
            assert 'n.__hydradb_update_if_newer_by=row.generation' in query
            for row in p['rows']:
                assert row['generation'] == 1
                if 'SET n:CuePilotMemory' in query:
                    self.nodes.setdefault(row['id'], row)
                elif 'SET n:CuePilotRecipe' in query:
                    if not any(r['id'] == row['id'] for r in self.recipes):
                        self.recipes.append(row)
                elif 'SET n:CuePilotOutcome' in query:
                    self.outcomes.setdefault(row['id'], row)
                else:
                    raise AssertionError('Unsupported label')
        elif query.startswith('UNWIND $rows AS row MATCH'):
            if not self.drop_import_edge:
                self.edges.extend(p['rows'])
        elif query.startswith('MATCH (n:CuePilotMemory'):
            return [{k: n[k] for k in ('id', 'original', 'source', 'dataset', 'note', 'graph', 'payload')}
                    for n in self.nodes.values() if n['receipt'] == p['receipt']]
        elif 'RETURN a.id AS source' in query:
            return [{'source': e['source'], 'target': e['target'], 'target_receipt': self.nodes[e['target']]['receipt'],
                     'label': e['label'], 'dataset': e['dataset'], 'receipt': e['receipt']}
                    for e in self.edges if e['source'] in self.nodes and e['target'] in self.nodes
                    and self.nodes[e['source']]['receipt'] == p['receipt']]
        elif query.startswith('MATCH (r:CuePilotRecipe {receipt_id:'):
            return [{'payload': r['payload']} for r in self.recipes if r['receipt'] == p['receipt']]
        elif query.startswith('MATCH (r:CuePilotRecipe {source_id:'):
            return [{'payload': r['payload'], 'receipt': r['receipt']} for r in self.recipes[::-1]
                    if r['source'] == p['source']][:1]
        elif query.startswith('MATCH (o:CuePilotOutcome {id:'):
            row = self.outcomes.get(p['id'])
            return [{'outcome': row['outcome'], 'payload': row['payload']}] if row else []
        elif query.startswith('MATCH (o:CuePilotOutcome {binding_sha256:'):
            return [{'outcome': r['outcome'], 'payload': r['payload']} for r in reversed(list(self.outcomes.values()))
                    if r['binding'] == p['binding'] and r['source'] == p['source']][:1]
        else:
            raise AssertionError('Unexpected driver query: ' + query)
        return []


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DriverBoundary()
        self.driver_patch = patch.object(memory, '_hydra_driver', return_value=(self.db, 'default'))
        self.query_patch = patch.object(memory, '_run', side_effect=self.db.query)
        self.driver_patch.start()
        self.query_patch.start()
        self.addCleanup(self.driver_patch.stop)
        self.addCleanup(self.query_patch.stop)

    async def publish(self):
        data = envelope()
        result = await memory._persist_graph(data['graph'], data['provenance'], data['proof'])
        data['receipt_id'] = result['receipt_id']
        return data

    async def test_graph_mapping_preserves_original_ids_properties_labels_and_provenance(self):
        data = await self.publish()
        self.assertEqual(len(self.db.nodes), 4)
        self.assertEqual(len(self.db.edges), 3)
        for original in data['graph']['nodes']:
            row = next(n for n in self.db.nodes.values() if n['original'] == original['id'])
            self.assertEqual(json.loads(row['payload']), original)
            self.assertEqual(row['dataset'], data['provenance']['dataset_id'])
            self.assertEqual(row['note'], memory._digest(NOTE))
        for edge in self.db.edges:
            self.assertNotEqual(edge['source'], edge['target'])
            self.assertEqual(self.db.nodes[edge['source']]['receipt'], data['receipt_id'])
            self.assertEqual(self.db.nodes[edge['target']]['receipt'], data['receipt_id'])
        result = await memory.recall_recipe('show-test')
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(result['recipe_id'], data['receipt_id'])
        self.assertEqual(result['note_sha256'], memory._digest(NOTE))
        self.assertEqual(result['supported_template'], memory.TEMPLATE)
        self.assertEqual([r['provider'] for r in result['records']], ['hydradb'])

    async def test_recall_rejects_mutations_to_actual_graph_despite_intact_envelope(self):
        for mutation in ('node_deleted', 'properties_changed', 'wrong_dataset', 'wrong_note', 'edge_deleted', 'extra_edge', 'edge_provenance'):
            with self.subTest(mutation=mutation):
                self.db.nodes.clear(); self.db.edges.clear(); self.db.recipes.clear()
                await self.publish()
                node = next(iter(self.db.nodes.values()))
                if mutation == 'node_deleted':
                    self.db.nodes.pop(node['id'])
                elif mutation == 'properties_changed':
                    node['payload'] = '{}'
                elif mutation == 'wrong_dataset':
                    node['dataset'] = str(uuid4())
                elif mutation == 'wrong_note':
                    node['note'] = '0' * 64
                elif mutation == 'edge_deleted':
                    self.db.edges.pop()
                elif mutation == 'extra_edge':
                    self.db.edges.append(dict(self.db.edges[0]))
                else:
                    self.db.edges[0]['dataset'] = str(uuid4())
                result = await memory.recall_recipe('show-test')
                self.assertEqual(result['status'], 'failed', result)
                self.assertNotIn('supported_template', result)

    async def test_incomplete_graph_import_never_publishes_a_recipe(self):
        self.db.drop_import_edge = True
        with self.assertRaises(memory.ProviderError):
            await self.publish()
        self.assertEqual(self.db.recipes, [])
        self.assertEqual((await memory.recall_recipe('show-test'))['status'], 'blocked')

    async def test_recipe_source_digest_and_proof_corruption_fail(self):
        for field in ('source_id', 'graph_sha256', 'proof', 'receipt_id'):
            with self.subTest(field=field):
                self.db.nodes.clear(); self.db.edges.clear(); self.db.recipes.clear()
                await self.publish()
                stored = json.loads(self.db.recipes[0]['payload'])
                if field == 'proof':
                    stored['proof']['ordering_edges'] = []
                elif field == 'receipt_id':
                    stored['receipt_id'] = 'another-recipe'
                else:
                    stored['provenance'][field] = 'wrong'
                self.db.recipes[0]['payload'] = memory._json(stored)
                result = await memory.recall_recipe('show-test')
                self.assertEqual(result['status'], 'failed', result)

    async def test_identical_note_reuses_extraction_but_performs_fresh_export(self):
        data = await self.publish()
        with patch.object(memory, '_reexport_graph', return_value=memory._record('cognee', 'verified', 'reexport_unchanged_graph')) as export, patch.object(memory, '_extract_graph') as extract:
            result = await memory.ingest_note(NOTE, 'show-test')
            self.assertEqual(result['status'], 'verified', result)
            self.assertEqual(result['recipe_id'], data['receipt_id'])
            export.assert_awaited_once()
            extract.assert_not_awaited()

    async def test_changed_note_requires_new_extraction_and_never_returns_old_hash(self):
        await self.publish()
        with patch.object(memory, '_extract_graph', side_effect=memory.ProviderError('extraction unavailable', blocked=True)) as extract, patch.object(memory, '_reexport_graph') as export:
            result = await memory.ingest_note(NOTE + ' New rule.', 'show-test')
            extract.assert_awaited_once()
            export.assert_not_awaited()
            self.assertEqual(result['status'], 'blocked')
            self.assertNotIn('note_sha256', result)

    async def outcome_arguments(self):
        data = await self.publish()
        run_id = str(uuid4())
        receipts = [dict(ok=True, id=str(uuid4()), runId=run_id, stepIndex=i, scene=scene,
                         stageRevision=i + 1, committedAt='2026-09-11T20:01:00+00:00')
                    for i, scene in enumerate(('intro', 'presentation', 'holding'))]
        procedure = {'id': 'cuepilot-verified-package', 'sha256': 'a' * 64}
        run = dict(id=run_id, showId='show-test', speakerId='maya', notes=NOTE, status='completed', executionMode='live',
                   memoryProof=dict(source_id='show-test', note_sha256=data['provenance']['note_sha256'], graph_sha256=data['provenance']['graph_sha256'], recipe_id=data['receipt_id']),
                   plan=dict(origin='sponsor', recipeId=data['receipt_id'], hash='b' * 64, showRevision=4,
                             cues=[{'index': i, 'scene': scene} for i, scene in enumerate(('intro', 'presentation', 'holding'))]), receipts=receipts)
        rote = dict(provider='rote', status='verified', operation='replay',
                    evidence=dict(runId=run_id, procedure=copy.deepcopy(procedure), receipts=copy.deepcopy(receipts), roteRunId='run_replay'))
        return dict(source_id='show-test', note_sha256=data['provenance']['note_sha256'],
                    graph_sha256=data['provenance']['graph_sha256'], recipe_id=data['receipt_id'],
                    run=run, rote_result=rote, procedure=procedure)

    async def test_outcome_roundtrip_is_idempotent_and_bound_to_rule_and_procedure(self):
        args = await self.outcome_arguments()
        first = await memory.record_successful_outcome(**args)
        second = await memory.record_successful_outcome(**args)
        self.assertEqual(first['status'], 'verified', first)
        self.assertEqual(first['outcome_id'], second['outcome_id'])
        self.assertEqual(len(self.db.outcomes), 1)
        read_args = {k: v for k, v in args.items() if k not in {'run', 'rote_result'}}
        recalled = await memory.recall_successful_outcome(**read_args)
        self.assertEqual(recalled['status'], 'verified', recalled)
        self.assertEqual(recalled['execution']['receipts'], args['run']['receipts'])
        self.assertEqual(recalled['graph_sha256'], args['graph_sha256'])
        for key in ('note_sha256', 'graph_sha256', 'recipe_id', 'procedure'):
            with self.subTest(key=key):
                changed = copy.deepcopy(read_args)
                changed[key] = {'id': 'other-procedure', 'sha256': 'd' * 64} if key == 'procedure' else ('d' * 64 if 'sha256' in key else 'other-recipe')
                self.assertEqual((await memory.recall_successful_outcome(**changed))['status'], 'blocked')

    async def test_outcome_rejects_practice_incomplete_failed_or_mismatched_evidence(self):
        original = await self.outcome_arguments()
        for change in ('practice', 'running', 'missing_cue', 'wrong_note', 'wrong_scene', 'wrong_receipt', 'failed_rote', 'wrong_procedure', 'wrong_run', 'wrong_run_graph', 'wrong_plan_recipe'):
            with self.subTest(change=change):
                args = copy.deepcopy(original)
                if change == 'practice': args['run']['executionMode'] = 'practice'
                elif change == 'running': args['run']['status'] = 'running'
                elif change == 'missing_cue': args['run']['receipts'].pop()
                elif change == 'wrong_note': args['run']['notes'] += ' changed'
                elif change == 'wrong_scene': args['run']['receipts'][1]['scene'] = 'intro'
                elif change == 'wrong_receipt': args['rote_result']['evidence']['receipts'][1]['id'] = str(uuid4())
                elif change == 'failed_rote': args['rote_result']['status'] = 'failed'
                elif change == 'wrong_procedure': args['rote_result']['evidence']['procedure']['sha256'] = '0' * 64
                elif change == 'wrong_run': args['rote_result']['evidence']['runId'] = str(uuid4())
                elif change == 'wrong_run_graph': args['run']['memoryProof']['graph_sha256'] = '1' * 64
                elif change == 'wrong_plan_recipe': args['run']['plan']['recipeId'] = 'another-recipe'
                result = await memory.record_successful_outcome(**args)
                self.assertEqual(result['status'], 'blocked', result)
                self.assertEqual(self.db.outcomes, {})

    async def test_learning_matches_raw_http_receipt_hashes(self):
        args = await self.outcome_arguments()
        args['rote_result']['operation'] = 'learn'
        evidence = args['rote_result']['evidence']
        evidence['learnedFromRunId'] = args['run']['id']
        evidence['receipts'] = [dict(ok=True, runId=r['runId'], stepIndex=r['stepIndex'],
                                     receiptSha256=memory._digest(json.dumps(r, ensure_ascii=False, separators=(',', ':'))))
                                for r in args['run']['receipts']]
        self.assertEqual((await memory.record_successful_outcome(**args))['status'], 'verified')
        evidence['receipts'][1]['receiptSha256'] = 'f' * 64
        self.assertEqual((await memory.record_successful_outcome(**args))['status'], 'blocked')

    async def test_outcome_recall_rejects_corrupted_payload_and_changed_current_recipe(self):
        args = await self.outcome_arguments()
        await memory.record_successful_outcome(**args)
        read_args = {k: v for k, v in args.items() if k not in {'run', 'rote_result'}}
        row = next(iter(self.db.outcomes.values()))
        original = row['payload']
        altered = json.loads(original)
        altered['execution']['receipts'][1]['scene'] = 'holding'
        row['payload'] = memory._json(altered)
        self.assertEqual((await memory.recall_successful_outcome(**read_args))['status'], 'failed')
        row['payload'] = original
        await self.publish()  # A different receipt for the source invalidates prior outcome reuse.
        self.assertEqual((await memory.recall_successful_outcome(**read_args))['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
