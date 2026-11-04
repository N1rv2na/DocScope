"""Offline DOCS-016 retrieval replay pinpoints where a source disappears."""

import unittest
import tempfile
from pathlib import Path

from docscope.evaluation.run_ablation_replay import replay_case, run_replay, select_source_rows


def point(chunk_id, document_id, score):
    return {'score': 0.8, 'payload': {'chunk_id': chunk_id, 'document_id': document_id,
            'document_title': document_id, 'chunk_text': f'evidence score-{score}'}}


class Scorer:
    def score(self, question, texts):
        return [float(text.split('score-')[-1]) for text in texts]


class ReplayCaseTest(unittest.TestCase):
    def setUp(self):
        self.source_row = {'sample_id': 'sample', 'mode': 'E', 'question': 'first query',
                           'status': 'insufficient_evidence',
                           'retrievals': [{'query': 'first query'}, {'query': 'second query'}]}

    def test_source_selected_in_round_two_but_dropped_in_final_merge(self):
        first = point('first', 'doc-first', 2)
        second = point('second', 'doc-target', 0)
        seen = []

        def search(query, mode):
            seen.append((query, mode))
            selected = first if query == 'first query' else second
            return {'points': [selected], 'candidate_count': 1, 'elapsed_seconds': 0.01,
                    'candidates': [{'chunk_id': selected['payload']['chunk_id'],
                                    'document_id': selected['payload']['document_id'],
                                    'chunk_text': selected['payload']['chunk_text'],
                                    'qdrant_rank': 1, 'qdrant_score': 0.8,
                                    'rerank_score': 2 if query == 'first query' else 0,
                                    'selected_rank': 1}]}

        result = replay_case(self.source_row, search, Scorer(), 1,
                             {'doc-first': 'source-first', 'doc-target': 'source-target'},
                             {'source-target'})
        self.assertEqual(seen, [('first query', 'E'), ('second query', 'E')])
        self.assertEqual(result['support_article_path'][0]['stage'], 'dropped_final_merge')
        self.assertEqual(result['second_hop_new_count'], 1)
        self.assertEqual(result['second_hop_new_retained_count'], 0)
        self.assertEqual(result['retrievals'][1]['candidates'][0]['source_document_id'], 'source-target')
        self.assertEqual(result['final_ranking'][1]['source_document_id'], 'source-target')
        self.assertIsNone(result['final_ranking'][1]['selected_rank'])

    def test_source_present_in_rrf_candidates_but_dropped_by_round_rerank(self):
        first = point('first', 'doc-first', 2)
        other = point('other', 'doc-other', 1)
        target = point('target', 'doc-target', 0)

        def search(query, mode):
            if query == 'first query':
                return {'points': [first], 'candidate_count': 1, 'elapsed_seconds': 0.01,
                        'candidates': [{'chunk_id': 'first', 'document_id': 'doc-first',
                                        'selected_rank': 1}]}
            return {'points': [other], 'candidate_count': 2, 'elapsed_seconds': 0.01,
                    'candidates': [{'chunk_id': 'other', 'document_id': 'doc-other',
                                    'selected_rank': 1},
                                   {'chunk_id': 'target', 'document_id': 'doc-target',
                                    'selected_rank': None}]}

        result = replay_case(self.source_row, search, Scorer(), 1,
                             {'doc-first': 'source-first', 'doc-other': 'source-other',
                              'doc-target': 'source-target'}, {'source-target'})
        self.assertEqual(result['support_article_path'][0]['stage'], 'dropped_per_round_rerank')
        self.assertEqual(result['support_article_path'][0]['candidate_rounds'], [2])
        self.assertEqual(result['support_article_path'][0]['round_selected_rounds'], [])

    def test_replay_accepts_only_frozen_samples_and_saved_queries(self):
        d = {**self.source_row, 'mode': 'D'}
        source = {'rows': [d, self.source_row]}
        selected = select_source_rows(source, ['sample'], {'sample'})
        self.assertEqual([row['mode'] for row in selected], ['D', 'E'])
        with self.assertRaisesRegex(ValueError, 'frozen'):
            select_source_rows(source, ['sealed-test'], {'sample'})
        with self.assertRaisesRegex(ValueError, 'saved'):
            select_source_rows({'rows': [d]}, ['sample'], {'sample'})

    def test_replay_never_overwrites_an_existing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'replay.json'
            existing.write_text('original')
            with self.assertRaises(FileExistsError):
                run_replay(Path('.'), Path('evaluation/results/ablation_dev_v2.json'),
                           existing, ['mhr-q-f4da8c5620114c384e0b'])
            self.assertEqual(existing.read_text(), 'original')


if __name__ == '__main__':
    unittest.main()
