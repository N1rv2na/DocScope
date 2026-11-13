"""Independent M4 memory comparison metric denominators and contamination."""

import unittest
import tempfile
import json
from pathlib import Path

from docscope.evaluation.run_memory import agent_diagnostics, score_turn, summarize_rows


class MemoryMetricTest(unittest.TestCase):
    def test_scores_expected_answer_query_and_citation_separately(self):
        turn = {'question': 'When was it built?', 'expected_fact': '2020',
                'expected_section': 'Vega', 'expected_query_entity': 'Vega',
                'forbidden_query_entity': 'Orion'}
        scored = score_turn(turn, 'When was the Vega telescope built?', 'answered',
                            'It was built in 2020.', ['Telescope Notes > Vega'])
        self.assertTrue(scored['answer_correct'])
        self.assertTrue(scored['citation_correct'])
        self.assertTrue(scored['query_entity_correct'])
        self.assertFalse(scored['history_polluted'])
        self.assertFalse(scored['answer_history_polluted'])
        contaminated = score_turn(turn, 'When was the Orion telescope built?', 'answered',
                                  'The Orion telescope was built in 2020.', ['Telescope Notes > Orion'])
        self.assertTrue(contaminated['answer_correct'])
        self.assertFalse(contaminated['citation_correct'])
        self.assertFalse(contaminated['query_entity_correct'])
        self.assertTrue(contaminated['history_polluted'])
        self.assertTrue(contaminated['answer_history_polluted'])
        negated = score_turn(turn, 'When was the Vega telescope built?', 'answered',
                             'Not 2020; it was built in 2022.', ['Telescope Notes > Vega'])
        self.assertFalse(negated['answer_correct'])

    def test_summary_keeps_denominators_for_missing_and_error_rows(self):
        rows = [
            {'arm': 'window_summary_rewrite', 'status': 'answered', 'answer_correct': True,
             'citation_correct': True, 'query_entity_correct': True, 'history_polluted': False,
             'expected_query_entity': 'Vega', 'forbidden_query_entity': 'Orion',
             'summary_stage': 'completed', 'request_count': 3},
            {'arm': 'window_summary_rewrite', 'status': 'error', 'answer_correct': False,
             'citation_correct': False, 'query_entity_correct': None, 'history_polluted': True,
             'expected_query_entity': None, 'forbidden_query_entity': 'Orion',
             'summary_stage': 'failed', 'request_count': 1},
        ]
        result = summarize_rows(rows)['window_summary_rewrite']
        self.assertEqual(result['turns'], 2)
        self.assertEqual(result['answer_correct'], {'count': 1, 'denominator': 2})
        self.assertEqual(result['query_entity_correct'], {'count': 1, 'denominator': 1})
        self.assertEqual(result['history_polluted'], {'count': 1, 'denominator': 2})
        self.assertEqual(result['summary_completed'], 1)
        self.assertEqual(result['summary_failed'], 1)
        self.assertEqual(result['remote_requests'], 4)

    def test_agent_diagnostics_reads_persisted_run_without_prompt_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runs').mkdir()
            (root / 'runs' / 'run-1.json').write_text(json.dumps({
                'run_id': 'run-1', 'trace': [{'stage': 'answer', 'llm_attempts': [{
                    'llm_call': {'stage': 'answer', 'total_tokens': 42},
                    'prompt_text': 'must not be exposed'}]}]}))
            self.assertEqual(agent_diagnostics(root, None, 'run-1'),
                             [{'stage': 'answer', 'total_tokens': 42}])


if __name__ == '__main__':
    unittest.main()
