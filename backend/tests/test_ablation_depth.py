"""Fixed candidate, paired D/E per-round retention experiment."""

import unittest

from docscope.evaluation.compare_ablation_depth import _paired_summary, compare_depth_row, run_experiment


class Scorer:
    def score(self, question, texts):
        return [float(text.split('score-')[-1]) for text in texts]


def candidate(chunk_id, score, source=None):
    return {'chunk_id': chunk_id, 'document_id': source or chunk_id,
            'source_document_id': source or chunk_id, 'document_title': chunk_id,
            'chunk_text': f'evidence score-{score}', 'qdrant_rank': 1,
            'qdrant_score': 0.5, 'rerank_score': float(score)}


def row(rounds, final_ids=None):
    retrievals = []
    for n, candidates in enumerate(rounds):
        ranked = sorted(candidates, key=lambda item: -item['rerank_score'])
        for rank, item in enumerate(candidates, 1):
            item['qdrant_rank'] = rank
            item['selected_rank'] = next((i for i, point in enumerate(ranked[:5], 1)
                                           if point['chunk_id'] == item['chunk_id']), None)
        retrievals.append({'query': f'query-{n}', 'candidate_count': len(candidates),
                           'candidates': candidates,
                           'evidence_ids': [item['chunk_id'] for item in ranked[:5]]})
    return {'sample_id': 'sample', 'mode': 'E', 'question': 'original question',
            'retrievals': retrievals, 'final_evidence_ids': final_ids or []}


class PairedDepthTest(unittest.TestCase):
    def test_rank_nine_target_survives_round_but_may_be_lost_at_final(self):
        first = [candidate(f'a{i}', 20 - i, 'a') for i in range(10)]
        second = [candidate(f'b{i}', 10 - i, 'b') for i in range(8)]
        second.append(candidate('target', 2, 'target'))
        second.append(candidate('b9', 1, 'b'))
        source = row([first, second], ['a0', 'a1', 'a2', 'a3', 'b0'])
        case = {'evidence_list': [{'document_id': 'target', 'fact': 'evidence score-2'}]}
        chunks = {'target': [{'chunk_id': 'target', 'chunk_text': 'evidence score-2'}]}
        result = compare_depth_row(source, case, chunks, Scorer(), (5, 8, 10), 5)
        self.assertEqual(result['depths']['5']['selected_ids'], source['final_evidence_ids'])
        self.assertEqual(result['depths']['5']['required_fact_paths'][0]['stage'], 'dropped_per_round_rerank')
        self.assertEqual(result['depths']['8']['required_fact_paths'][0]['stage'], 'dropped_per_round_rerank')
        self.assertEqual(result['depths']['10']['required_fact_paths'][0]['stage'], 'dropped_final_merge')
        self.assertLessEqual(len(result['depths']['10']['selected_ids']), 5)
        self.assertLessEqual(result['depths']['10']['unique_union_count'], 20)

    def test_unmapped_fact_still_tracks_support_article_loss(self):
        first = [candidate(f'a{i}', 20 - i, 'a') for i in range(10)]
        second = [candidate(f'b{i}', 10 - i, 'b') for i in range(8)]
        second += [candidate('target', 2, 'target'), candidate('b9', 1, 'b')]
        source = row([first, second], ['a0', 'a1', 'a2', 'a3', 'b0'])
        case = {'evidence_list': [{'document_id': 'target', 'fact': 'fact spans two chunks'}]}
        chunks = {'target': [{'chunk_id': 'one', 'chunk_text': 'fact spans'},
                             {'chunk_id': 'two', 'chunk_text': 'two chunks'}]}
        result = compare_depth_row(source, case, chunks, Scorer())
        self.assertEqual(result['fact_mapping_status'], 'unmapped')
        self.assertEqual(result['depths']['5']['support_article_paths'][0]['stage'], 'dropped_per_round_rerank')
        self.assertEqual(result['depths']['10']['support_article_paths'][0]['stage'], 'dropped_final_merge')
        self.assertEqual(result['depths']['10']['required_fact_paths'], [])

    def test_fact_loss_uses_uniquely_mapped_source_chunk_text(self):
        source = row([[candidate('a', 1)]], ['a'])
        source['retrievals'][0]['candidates'][0]['chunk_text'] = 'unique fact extra score-1'
        case = {'evidence_list': [{'document_id': 'a', 'fact': 'unique fact'}]}
        chunks = {'a': [{'chunk_id': 'source-a', 'chunk_text': 'unique fact'}]}
        result = compare_depth_row(source, case, chunks, Scorer())
        self.assertEqual(result['fact_mapping_status'], 'mapped')
        self.assertEqual(result['depths']['5']['metrics']['article_recall'], 1)
        self.assertEqual(result['depths']['5']['metrics']['fact_coverage'], 0)
        self.assertEqual(result['depths']['5']['required_fact_paths'][0]['stage'], 'not_in_rrf_top20')

    def test_overlap_one_round_and_short_list(self):
        first = [candidate('a', 2), candidate('b', 1)]
        second = [candidate('b', 1)]
        for rounds, expected in (([first], 2), ([first, second], 2)):
            source = row(rounds, ['a', 'b'])
            result = compare_depth_row(source, {'evidence_list': []}, {}, Scorer(), (5, 8, 10), 5)
            for depth in ('5', '8', '10'):
                self.assertEqual(result['depths'][depth]['unique_union_count'], expected)
                self.assertEqual(result['depths'][depth]['second_hop_new_count'], 0)
                self.assertEqual(result['depths'][depth]['selected_ids'], ['a', 'b'])

    def test_ties_follow_qdrant_order_and_top_five_must_match_source(self):
        points = [candidate(f'a{i}', 1) for i in range(10)]
        source = row([points], [f'a{i}' for i in range(5)])
        result = compare_depth_row(source, {'evidence_list': []}, {}, Scorer(), (5, 8, 10), 5)
        self.assertEqual(result['depths']['10']['selected_ids'], [f'a{i}' for i in range(5)])
        source['final_evidence_ids'] = ['a9']
        with self.assertRaisesRegex(ValueError, 'Top-5'):
            compare_depth_row(source, {'evidence_list': []}, {}, Scorer(), (5, 8, 10), 5)

    def test_invalid_depth_or_incomplete_candidates_rejected(self):
        source = row([[candidate('a', 1)]], ['a'])
        for depths in ((5, 5), (5, 8, 11), (8, 10)):
            with self.subTest(depths=depths), self.assertRaises(ValueError):
                compare_depth_row(source, {'evidence_list': []}, {}, Scorer(), depths, 5)
        del source['retrievals'][0]['candidates'][0]['chunk_text']
        with self.assertRaises(ValueError):
            compare_depth_row(source, {'evidence_list': []}, {}, Scorer(), (5, 8, 10), 5)

    def test_combined_summary_uses_only_evaluable_denominators(self):
        first = row([[candidate('a', 2)]], ['a'])
        first['mode'] = 'D'
        second = row([[candidate('b', 1)]], ['b'])
        second['mode'] = 'E'
        rows = [compare_depth_row(first, {'evidence_list': [
                    {'document_id': 'a', 'fact': 'evidence score-2'}]},
                    {'a': [{'chunk_id': 'a', 'chunk_text': 'evidence score-2'}]}, Scorer()),
                compare_depth_row(second, {'evidence_list': []}, {}, Scorer())]
        summary = _paired_summary(rows)
        self.assertEqual(summary['overall']['row_count'], 2)
        self.assertEqual(summary['overall']['by_depth']['5']['article_recall']['denominator'], 1)
        self.assertEqual(summary['overall']['by_depth']['5']['fact_coverage']['denominator'], 1)
        self.assertEqual(summary['overall']['paired']['5_to_8']['article_recall']['not_evaluable'], 1)

    def test_existing_result_is_never_overwritten(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'result.json'
            output.write_text('existing')
            with self.assertRaises(FileExistsError):
                run_experiment(Path(folder), Path(folder) / 'missing-config.json', output, Scorer())
            self.assertEqual(output.read_text(), 'existing')

    def test_config_limits_are_independent_and_bounded(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / 'config.json'
            output = Path(folder) / 'new.json'
            base = {'per_round_keep_k_values': [5, 8, 10], 'candidate_limit': 20,
                    'final_evidence_limit': 5, 'final_merge_policy': 'reserve_second_hop_one',
                    'split': 'dev', 'modes': ['D', 'E'],
                    'reranker_model': 'cross-encoder/ms-marco-MiniLM-L6-v2',
                    'reranker_revision': '233902d25c440f23af6f7d6e94d2946bac0bee0a'}
            for change in ({'per_round_keep_k_values': [5, 8, 11]},
                           {'per_round_keep_k_values': [5, 8, 8]},
                           {'candidate_limit': 19}, {'final_evidence_limit': 6}):
                with self.subTest(change=change):
                    config_path.write_text(json.dumps({**base, **change}))
                    with self.assertRaisesRegex(ValueError, 'configuration'):
                        run_experiment(Path(folder), config_path, output, Scorer())


if __name__ == '__main__':
    unittest.main()
