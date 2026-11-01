"""DOCS-015 frozen Agent answer and refusal denominators."""

import unittest

from docscope.evaluation.agent_answer import score_case, summarize, review_support


class AgentAnswerMetricsTest(unittest.TestCase):
    def test_null_and_self_no_evidence_are_not_retrieval_denominators(self):
        rows = [
            score_case('a', 'inference_query', 'East team', ['doc-a'],
                       {'status': 'answered', 'answer': 'East team',
                        'retrieved_evidence_ids': ['c1'],
                        'citations': [{'chunk_id': 'c1'}]}, {'c1': 'doc-a'}),
            score_case('n', 'null_query', None, [],
                       {'status': 'insufficient_evidence', 'answer': None,
                        'retrieved_evidence_ids': [], 'citations': []}, {}),
            score_case('s', 'self_no_evidence', None, [],
                       {'status': 'answered', 'answer': 'Guess',
                        'retrieved_evidence_ids': ['c2'],
                        'citations': [{'chunk_id': 'c2'}]}, {'c2': 'doc-b'}),
        ]
        summary = summarize(rows)
        self.assertEqual(summary['fixed_subset_count'], 3)
        self.assertEqual(summary['answerable_count'], 1)
        self.assertEqual(summary['unanswerable_count'], 2)
        self.assertEqual(summary['retrieval_all_articles']['denominator'], 1)
        self.assertEqual(summary['appropriate_abstention']['numerator'], 1)
        self.assertEqual(summary['appropriate_abstention']['denominator'], 2)
        self.assertEqual(summary['false_abstention']['denominator'], 1)

    def test_model_errors_are_not_counted_as_refusals_and_invalid_citations_are_separate(self):
        rows = [
            score_case('a', 'comparison_query', 'Yes', ['doc-a'],
                       {'status': 'insufficient_evidence', 'answer': None,
                        'retrieved_evidence_ids': ['c1'], 'citations': []}, {'c1': 'doc-a'}),
            score_case('n', 'null_query', None, [],
                       {'status': 'error', 'answer': None,
                        'retrieved_evidence_ids': [], 'citations': []}, {}),
            score_case('b', 'inference_query', 'No', ['doc-b'],
                       {'status': 'answered', 'answer': 'No',
                        'retrieved_evidence_ids': ['c2'],
                        'citations': [{'chunk_id': 'c2'}, {'chunk_id': 'fabricated'}]},
                       {'c2': 'doc-b'}),
        ]
        summary = summarize(rows)
        self.assertEqual(summary['false_abstention'], {'numerator': 1, 'denominator': 2, 'rate': 0.5})
        self.assertEqual(summary['appropriate_abstention']['numerator'], 0)
        self.assertEqual(summary['error_count'], 1)
        self.assertEqual(summary['citation_id_legal']['denominator'], 2)
        self.assertEqual(summary['citation_id_legal']['numerator'], 1)

    def test_support_rate_remains_unknown_until_every_citation_is_reviewed(self):
        self.assertEqual(review_support([{'support': 'supported'}, {'support': 'pending'}])['rate'], None)
        scored = review_support([{'support': 'supported'}, {'support': 'unsupported'}])
        self.assertEqual(scored, {'numerator': 1, 'denominator': 2, 'pending': 0, 'rate': 0.5})

    def test_exact_and_f1_are_diagnostics_not_manual_support(self):
        row = score_case('a', 'temporal_query', 'East Region Operations team', ['doc-a'],
                         {'status': 'answered', 'answer': 'The East Region Operations team.',
                          'retrieved_evidence_ids': ['c1'], 'citations': [{'chunk_id': 'c1'}]},
                         {'c1': 'doc-a'})
        self.assertFalse(row['exact_match'])
        self.assertGreater(row['token_f1'], 0.8)
        self.assertNotIn('citation_support', row)


if __name__ == '__main__':
    unittest.main()
