"""Compare final evidence rules on identical saved retrieval candidates."""

import unittest

from docscope.evaluation.compare_ablation_selection import compare_selection_row


def ranked(chunk_id, source, score):
    return {'chunk_id': chunk_id, 'source_document_id': source,
            'chunk_text': f'{source} fact', 'rerank_score': score}


class SelectionComparisonTest(unittest.TestCase):
    def test_second_hop_source_survives_only_under_reserved_rule(self):
        ranking = [ranked(f'a{i}', 'a', 10 - i) for i in range(5)] + [ranked('b', 'b', 0)]
        row = {'sample_id': 'sample', 'mode': 'E', 'question': 'Who uses the relay?',
               'retrievals': [{'evidence_ids': [f'a{i}' for i in range(5)]},
                              {'evidence_ids': ['b']}],
               'final_ranking': ranking, 'final_evidence_ids': [f'a{i}' for i in range(5)]}
        case = {'evidence_list': [{'document_id': 'a', 'fact': 'a fact'},
                                  {'document_id': 'b', 'fact': 'b fact'}]}
        chunks = {'a': [{'chunk_id': 'a1', 'chunk_text': 'a fact'}],
                  'b': [{'chunk_id': 'b1', 'chunk_text': 'b fact'}]}
        result = compare_selection_row(row, case, chunks, 5)
        self.assertEqual(result['old']['source_articles_found'], 1)
        self.assertEqual(result['new']['source_articles_found'], 2)
        self.assertEqual(result['old']['facts_found'], 1)
        self.assertEqual(result['new']['facts_found'], 2)
        self.assertEqual(result['new']['audit']['reserved_chunk_id'], 'b')
        self.assertEqual(result['old']['audit']['second_hop_new_retained_count'], 0)
        self.assertEqual(result['new']['audit']['second_hop_new_retained_count'], 1)
        self.assertEqual(len(result['new']['selected_ids']), 5)

    def test_fact_mapping_failure_is_not_counted_as_miss(self):
        row = {'sample_id': 'sample', 'mode': 'D', 'question': 'question',
               'retrievals': [{'evidence_ids': ['a']}, {'evidence_ids': ['b']}],
               'final_ranking': [ranked('a', 'a', 2), ranked('b', 'b', 0)],
               'final_evidence_ids': ['a']}
        case = {'evidence_list': [{'document_id': 'a', 'fact': 'a fact'},
                                  {'document_id': 'b', 'fact': 'split across chunks'}]}
        chunks = {'a': [{'chunk_id': 'a1', 'chunk_text': 'a fact'}],
                  'b': [{'chunk_id': 'b1', 'chunk_text': 'split across'},
                        {'chunk_id': 'b2', 'chunk_text': 'chunks'}]}
        result = compare_selection_row(row, case, chunks, 1)
        self.assertEqual(result['fact_mapping_status'], 'unmapped')
        self.assertIsNone(result['new']['facts_found'])
        self.assertIsNone(result['old']['facts_found'])
        self.assertEqual(result['new']['source_articles_found'], 1)

    def test_rejects_saved_old_selection_inconsistent_with_scored_order(self):
        row = {'sample_id': 'sample', 'mode': 'E', 'question': 'question',
               'retrievals': [{'evidence_ids': ['a']}, {'evidence_ids': ['b']}],
               'final_ranking': [ranked('a', 'a', 2), ranked('b', 'b', 0)],
               'final_evidence_ids': ['b']}
        with self.assertRaisesRegex(ValueError, 'saved'):
            compare_selection_row(row, {'evidence_list': []}, {}, 1)


if __name__ == '__main__':
    unittest.main()
