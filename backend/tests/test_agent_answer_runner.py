"""DOCS-015 runner guards: frozen dev IDs, label isolation and bounded calls."""

import json
import tempfile
import unittest
from pathlib import Path

from docscope.evaluation.run_agent_answer import evaluate_case, run, validate_subset, review_template


ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    status_code = 200

    def json(self):
        return {'run_id': 'run-1', 'status': 'answered', 'answer': 'East team',
                'stop_reason': 'answered', 'retrieved_evidence_ids': ['chunk-1'],
                'citations': [{'chunk_id': 'chunk-1', 'document_id': 'online-1',
                               'document_title': 'source.md', 'chunk_text': 'East team operates it.'}],
                'calls': {'retrieval': 1, 'model': 2}, 'trace': []}


class FakeClient:
    def __init__(self):
        self.seen = []

    def post(self, path, json):
        self.seen.append((path, json))
        return FakeResponse()


class AgentAnswerRunnerTest(unittest.TestCase):
    def test_frozen_subset_is_dev_only_and_stratified(self):
        settings = json.loads((ROOT / 'evaluation/configs/agent_answer_dev_v1.json').read_text())
        dense = json.loads((ROOT / 'evaluation/configs/dense_baseline_v1.json').read_text())
        manifest = json.loads((ROOT / settings['manifest']).read_text())
        self.assertEqual(settings['answer_subset_ids'], dense['answer_subset_ids'])
        self.assertTrue(set(settings['answer_subset_ids']) <= set(manifest['dev_ids']))
        self.assertFalse(set(settings['answer_subset_ids']) & set(manifest['test_ids']))
        self.assertEqual(len(settings['self_no_evidence']), 2)

    def test_only_question_crosses_agent_api_and_budget_is_checked(self):
        client = FakeClient()
        case = {'sample_id': 'a', 'question_type': 'inference_query', 'query': 'Who runs it?',
                'answer': 'East team', 'evidence_list': [{'document_id': 'offline-1', 'fact': 'SECRET LABEL'}]}
        row = evaluate_case(client, case, {'online-1': 'offline-1'}, {'chunk-1': 'offline-1'},
                            [], 2, 2)
        self.assertEqual(client.seen, [('/api/agent/run', {'question': 'Who runs it?'})])
        self.assertNotIn('SECRET LABEL', str(client.seen))
        self.assertEqual(row['metrics']['article_recall'], 1.0)
        self.assertEqual(row['run']['calls'], {'retrieval': 1, 'model': 2})
        self.assertEqual(row['llm_calls'], [])

    def test_over_budget_result_is_rejected(self):
        class OverBudget(FakeClient):
            def post(self, path, json):
                response = super().post(path, json)
                response.json = lambda: {**FakeResponse().json(), 'calls': {'retrieval': 3, 'model': 2}}
                return response
        with self.assertRaisesRegex(ValueError, 'budget'):
            evaluate_case(OverBudget(), {'sample_id': 'a', 'question_type': 'null_query',
                                         'query': 'Unknown?', 'answer': None, 'evidence_list': []},
                          {}, {}, [], 2, 2)

    def test_review_template_marks_support_pending_without_inventing_human_judgment(self):
        row = evaluate_case(FakeClient(), {'sample_id': 'a', 'question_type': 'inference_query',
                                           'query': 'Who?', 'answer': 'East team',
                                           'evidence_list': [{'document_id': 'offline-1', 'fact': 'East team'}]},
                            {'online-1': 'offline-1'}, {'chunk-1': 'offline-1'}, [], 2, 2)
        review = review_template([row], None)
        self.assertEqual(review['citations'][0]['support'], 'pending')
        self.assertIsNone(review['citations'][0]['reviewer'])
        self.assertEqual(review['summary']['rate'], None)

    def test_existing_human_review_cannot_be_overwritten_by_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'answer.json'
            output.with_name('answer.review.json').write_text('{"reviewer":"owner"}')
            with self.assertRaisesRegex(FileExistsError, 'human review exists'):
                run(ROOT, {}, output)


if __name__ == '__main__':
    unittest.main()
