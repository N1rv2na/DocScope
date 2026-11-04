"""DOCS-016 frozen comparison and accounting contracts."""

import json
import unittest
from pathlib import Path

from docscope.evaluation.ablation import (MODE_NAMES, final_context, plan_queries,
                                          render_report, summarize_comparison, validate_config)
from docscope.evaluation.run_agent_answer import validate_subset
from docscope.evaluation.run_dense import load_inputs


ROOT = Path(__file__).resolve().parents[2]


class Scorer:
    def score(self, question, texts):
        return [float(text.split('-')[-1]) for text in texts]


class AblationContractTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'evaluation/configs/ablation_dev_v1.json').read_text())
        self.answer = json.loads((ROOT / 'evaluation/configs/agent_answer_dev_v1.json').read_text())
        self.rerank = json.loads((ROOT / 'evaluation/configs/rerank_dev_v1.json').read_text())

    def test_config_freezes_same_dev_ids_models_and_budgets(self):
        validate_config(self.config, self.answer, self.rerank)
        self.assertEqual(tuple(self.config['modes']), MODE_NAMES)
        altered = {**self.config, 'answer_subset_ids': self.config['answer_subset_ids'][1:]}
        with self.assertRaisesRegex(ValueError, 'subset'):
            validate_config(altered, self.answer, self.rerank)
        altered = {**self.config, 'max_model_attempts': 3}
        with self.assertRaisesRegex(ValueError, 'model budget'):
            validate_config(altered, self.answer, self.rerank)

    def test_frozen_config_is_accepted_by_reused_subset_validator(self):
        articles, questions, _ = load_inputs(ROOT, self.config)
        self.assertEqual(len(validate_subset(ROOT, self.config, questions, articles)), 14)

    def test_revised_eval_only_caps_keep_route_and_subset_frozen(self):
        revised = json.loads((ROOT / 'evaluation/configs/ablation_dev_v2.json').read_text())
        validate_config(revised, self.answer, self.rerank)
        self.assertEqual(revised['answer_subset_ids'], self.config['answer_subset_ids'])
        self.assertEqual(revised['requested_model'], self.config['requested_model'])
        self.assertEqual((revised['answer_max_tokens'], revised['planning_max_tokens']), (2048, 4096))

    def test_v3_policy_is_explicit_and_frozen(self):
        revised = json.loads((ROOT / 'evaluation/configs/ablation_dev_v3.json').read_text())
        validate_config(revised, self.answer, self.rerank)
        self.assertEqual(revised['final_merge_policy'], 'reserve_second_hop_one')
        self.assertEqual(revised['answer_subset_ids'], self.config['answer_subset_ids'])
        self.assertEqual(revised['final_evidence_limit'], 5)
        with self.assertRaisesRegex(ValueError, 'policy'):
            validate_config({**revised, 'final_merge_policy': 'unknown'}, self.answer, self.rerank)

    def test_final_context_deduplicates_and_limits_same_evidence_for_d_and_e(self):
        def point(name, score):
            return {'score': 0.5, 'payload': {'chunk_id': name, 'chunk_text': f'text-{score}'}}
        first = [point('a', 1), point('b', 2)]
        second = [point('b', 2), point('c', 3)]
        selected = final_context('question', first + second, Scorer(), 2)
        self.assertEqual([item['payload']['chunk_id'] for item in selected], ['c', 'b'])
        self.assertEqual(len(selected), 2)
        with self.assertRaisesRegex(ValueError, 'limit'):
            final_context('question', first + second, Scorer(), 0)

    def test_d_one_shot_plan_sees_question_only_and_keeps_first_query_equal(self):
        seen = []

        def complete(stage, instructions, message, max_tokens):
            seen.append((stage, json.loads(message)))
            return {'second_query': 'Polaris Relay operator'}

        queries = plan_queries('Who operates the Aster audit export service?', complete)
        self.assertEqual(queries, ['Who operates the Aster audit export service?',
                                   'Polaris Relay operator'])
        self.assertEqual(seen[0][1], {'question': 'Who operates the Aster audit export service?'})
        with self.assertRaisesRegex(ValueError, 'distinct'):
            plan_queries('Same?', lambda *args: {'second_query': 'Same?'})

    def test_actual_and_matched_budget_results_have_explicit_denominators(self):
        def row(sample_id, mode, retrieval, model, correct, channel=None):
            return {'sample_id': sample_id, 'mode': mode, 'question_type': 'inference_query',
                    'status': 'answered',
                    'metrics': {'exact_match': correct, 'citation_id_legal': []},
                    'calls': {'retrieval': retrieval, 'model': model},
                    'candidate_count': 20 * retrieval, 'final_evidence_count': 5,
                    'llm_calls': [{'actual_model': 'same', 'actual_channel': channel,
                                   'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                                   'billed_cost_cny': None}]}
        rows = [row('x', 'A', 1, 1, True), row('y', 'A', 1, 1, True),
                row('x', 'D', 2, 2, False), row('x', 'E', 2, 2, True),
                row('y', 'D', 2, 2, True), row('y', 'E', 1, 2, False)]
        summary = summarize_comparison(rows, ['x', 'y'])
        self.assertEqual(summary['actual']['D']['answer_exact_match']['denominator'], 2)
        self.assertEqual(summary['actual']['E']['answer_exact_match']['denominator'], 2)
        self.assertEqual(summary['matched_de']['pair_count'], 1)
        self.assertEqual(summary['matched_de']['sample_ids'], ['x'])
        self.assertFalse(summary['strict_same_channel_control'])
        self.assertIsNone(summary['actual']['D']['billed_cost_cny'])
        report = render_report({'config': self.config, 'corpus_count': 609, 'sample_count': 2,
                                'summary': summary, 'rows': rows, 'run_window_utc': {'start': 's', 'end': 'e'}})
        self.assertIn('2 道固定子集题', report)
        self.assertIn('1/2', report)
        self.assertIn('渠道未知', report)
        self.assertIn('非全量 Benchmark', report)
        self.assertIn('相对 A 的答案 EM', report)
        self.assertIn('输出截断', report)
        self.assertIn('严格字符串匹配', report)

    def test_v3_report_discloses_new_merge_policy(self):
        revised = json.loads((ROOT / 'evaluation/configs/ablation_dev_v3.json').read_text())
        summary = summarize_comparison([], [])
        report = render_report({'config': revised, 'corpus_count': 609, 'sample_count': 0,
                                'summary': summary, 'rows': [],
                                'run_window_utc': {'start': 's', 'end': 'e'}})
        self.assertIn('reserve_second_hop_one', report)


if __name__ == '__main__':
    unittest.main()
