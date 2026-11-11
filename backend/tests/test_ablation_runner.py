"""DOCS-016 D/E planning order and shared evidence budget."""

import unittest

from docscope.evaluation.run_ablation import (EvalRetriever, EvaluationResponder, attach_source_ids,
                                             run_mode, select_final_points)
from docscope.llm import LLMCallError


def point(name, title, text):
    return {'score': 0.8, 'payload': {'chunk_id': name, 'document_id': title,
            'document_title': title, 'source_path': title, 'page_number': None,
            'section': title, 'line_start': 1, 'chunk_text': text, 'index_version': 1}}


FIRST = point('first', 'aster.md', 'Aster audit export uses Polaris Relay. score-1')
SECOND = point('second', 'polaris.md', 'Polaris Relay is operated by East team. score-2')
QUESTION = 'Who operates the service used by Aster audit export?'


class Scorer:
    def score(self, question, texts):
        return [float(item.split('score-')[-1]) for item in texts]


class Responder:
    def __init__(self, events):
        self.events = events

    def _complete_json(self, stage, instructions, message, max_tokens):
        self.events.append(('plan_once', message, max_tokens))
        return {'second_query': 'Polaris Relay operator'}

    def plan_next(self, question, evidence):
        self.events.append(('plan_next', [item['chunk_id'] for item in evidence]))
        return {'evidence_status': 'missing', 'missing_fact': 'operator of Polaris Relay',
                'next_query': 'Polaris Relay operator', 'bridge_text': 'Polaris Relay',
                'source_chunk_id': evidence[0]['chunk_id']}

    def generate(self, question, evidence):
        self.events.append(('answer', [item['chunk_id'] for item in evidence]))
        return {'status': 'answered', 'answer': 'East team',
                'citation_ids': [item['chunk_id'] for item in evidence]}


class AblationRunTest(unittest.TestCase):
    def test_frozen_ablation_does_not_inherit_online_retry_budget(self):
        events = []

        class FailingResponder(Responder):
            def generate(self, question, evidence):
                events.append(("answer", [item["chunk_id"] for item in evidence]))
                raise LLMCallError("request_timeout", {"stage": "answer", "completion_tokens": None})

        result = run_mode("A", QUESTION, self.search(events), FailingResponder(events), Scorer(), 5)
        self.assertEqual(result["run"]["status"], "error")
        self.assertEqual(result["run"]["calls"]["model"], 1)
        self.assertEqual(result["run"]["model_request_budget"], 2)
        self.assertEqual(result["run"]["retry_requests"], 0)
        self.assertEqual([item[0] for item in events], ["retrieve", "answer"])

    def search(self, events):
        def perform(query, mode):
            events.append(('retrieve', query, mode))
            points = [SECOND] if 'Polaris Relay' in query else [FIRST]
            return {'points': points, 'candidate_count': len(points), 'elapsed_seconds': 0.01}
        return perform

    def test_d_plans_before_any_retrieval_and_e_plans_after_first_evidence(self):
        d_events = []
        d = run_mode('D', QUESTION, self.search(d_events), Responder(d_events), Scorer(), 5)
        self.assertEqual([item[0] for item in d_events], ['plan_once', 'retrieve', 'retrieve', 'answer'])
        self.assertEqual(d['run']['calls'], {'retrieval': 2, 'model': 2})
        self.assertEqual(set(d['final_evidence_ids']), {'first', 'second'})
        e_events = []
        e = run_mode('E', QUESTION, self.search(e_events), Responder(e_events), Scorer(), 5)
        self.assertEqual([item[0] for item in e_events], ['retrieve', 'plan_next', 'retrieve', 'answer'])
        self.assertEqual(e_events[1][1], ['first'])
        self.assertEqual(e['run']['calls'], {'retrieval': 2, 'model': 2})
        self.assertEqual(set(e['final_evidence_ids']), {'first', 'second'})

    def test_a_uses_one_retrieval_one_model_and_no_planner(self):
        events = []
        result = run_mode('A', QUESTION, self.search(events), Responder(events), Scorer(), 5)
        self.assertEqual([item[0] for item in events], ['retrieve', 'answer'])
        self.assertEqual(result['run']['calls'], {'retrieval': 1, 'model': 1})

    def test_revised_eval_responder_uses_same_caps_for_d_and_e_planning(self):
        events = []
        responder = EvaluationResponder(2048, 4096)
        responder._complete_json = lambda stage, instructions, message, max_tokens: (
            events.append((stage, max_tokens)) or
            ({'second_query': 'Polaris Relay operator'} if stage == 'plan_once' else
             {'evidence_status': 'missing', 'missing_fact': 'operator of Polaris Relay',
              'next_query': 'Polaris Relay operator', 'bridge_text': 'Polaris Relay',
              'source_chunk_id': 'first'} if stage == 'plan_next' else
             {'status': 'answered', 'answer': 'East team', 'citation_ids': ['first']}))
        run_mode('D', QUESTION, self.search([]), responder, Scorer(), 5)
        run_mode('E', QUESTION, self.search([]), responder, Scorer(), 5)
        self.assertIn(('plan_once', 4096), events)
        self.assertIn(('plan_next', 4096), events)
        self.assertIn(('answer', 2048), events)

    def test_all_modes_use_frozen_retrieval_path_and_cde_rerank(self):
        calls = []

        class Embedder:
            def encode(self, texts):
                return [[0.1]]

        class Index:
            def query(self, vector, ready_ids, limit):
                calls.append(('dense', limit))
                return [FIRST]

            def query_hybrid(self, vector, query, ready_ids, limit, candidate_limit):
                calls.append(('hybrid', limit, candidate_limit))
                return [FIRST, SECOND]

        retriever = EvalRetriever(Embedder(), Index(), Scorer(), 5, 20)
        for mode in 'ABCDE':
            self.assertTrue(retriever.search(QUESTION, mode)['points'])
        self.assertEqual(calls, [('dense', 5), ('hybrid', 5, 20),
                                 ('hybrid', 20, 20), ('hybrid', 20, 20), ('hybrid', 20, 20)])

    def test_retrieval_audit_keeps_all_candidates_and_scores_once(self):
        class CountingScorer(Scorer):
            def __init__(self):
                self.calls = 0

            def score(self, question, texts):
                self.calls += 1
                return super().score(question, texts)

        class Index:
            def query_hybrid(self, vector, query, ready_ids, limit, candidate_limit):
                return [FIRST, SECOND]

        class Embedder:
            def encode(self, texts):
                return [[0.1]]

        scorer = CountingScorer()
        result = EvalRetriever(Embedder(), Index(), scorer, 1, 20).search(QUESTION, 'E')
        self.assertEqual(scorer.calls, 1)
        self.assertEqual([x['chunk_id'] for x in result['candidates']], ['first', 'second'])
        self.assertEqual([x['qdrant_rank'] for x in result['candidates']], [1, 2])
        self.assertEqual([x['rerank_score'] for x in result['candidates']], [1.0, 2.0])
        self.assertEqual([x['selected_rank'] for x in result['candidates']], [2, 1])
        self.assertEqual([x['payload']['chunk_id'] for x in result['points']], ['second', 'first'])

    def test_final_audit_shows_second_hop_dropped_after_per_round_selection(self):
        weaker_second = point('second', 'polaris.md', 'Polaris Relay is operated by East team. score-0')

        def search(query, mode):
            selected = [weaker_second] if 'Polaris Relay' in query else [FIRST]
            return {'points': selected, 'candidate_count': 1, 'elapsed_seconds': 0.01,
                    'candidates': [{'chunk_id': selected[0]['payload']['chunk_id'],
                                    'qdrant_rank': 1, 'qdrant_score': 0.8,
                                    'rerank_score': float(selected[0]['payload']['chunk_text'].split('score-')[-1]),
                                    'selected_rank': 1}]}

        result = run_mode('E', QUESTION, search, Responder([]), Scorer(), 1)
        self.assertEqual([x['evidence_ids'] for x in result['retrievals']], [['first'], ['second']])
        self.assertEqual(result['final_evidence_ids'], ['first'])
        self.assertEqual([(x['chunk_id'], x['from_rounds'], x['selected_rank'])
                          for x in result['final_ranking']],
                         [('first', [1], 1), ('second', [2], None)])
        self.assertEqual(result['final_ranking'][1]['rerank_score'], 0.0)

    def test_audit_source_ids_are_added_only_to_offline_record(self):
        retrievals = [{'query': QUESTION, 'candidates': [
            {'chunk_id': 'first', 'document_id': 'aster.md', 'chunk_text': 'Aster',
             'qdrant_rank': 1, 'selected_rank': 1}]}]
        labeled = attach_source_ids(retrievals, {'aster.md': 'source-aster'})
        self.assertEqual(labeled[0]['candidates'][0]['source_document_id'], 'source-aster')
        self.assertNotIn('source_document_id', retrievals[0]['candidates'][0])

    def test_final_selection_can_be_replayed_without_an_llm(self):
        weaker_second = point('second', 'polaris.md', 'Polaris Relay operator. score-0')
        rounds = [{'points': [FIRST]}, {'points': [weaker_second]}]
        selected, ranking, _ = select_final_points(QUESTION, rounds, Scorer(), 1)
        self.assertEqual([x['payload']['chunk_id'] for x in selected], ['first'])
        self.assertEqual([(x['chunk_id'], x['selected_rank']) for x in ranking],
                         [('first', 1), ('second', None)])

    def test_reserved_second_hop_replaces_lowest_global_entry_for_both_modes(self):
        first = [point(f'f{i}', 'first.md', f'first score-{10 - i}') for i in range(5)]
        second = [point('s1', 'second.md', 'second score-1'),
                  point('s2', 'second.md', 'second score-0')]
        rounds = [{'points': first}, {'points': second}]
        for mode in ('D', 'E'):
            selected, ranking, audit = select_final_points(
                QUESTION, rounds, Scorer(), 5, policy='reserve_second_hop_one')
            ids = [p['payload']['chunk_id'] for p in selected]
            self.assertEqual(ids, ['f0', 'f1', 'f2', 'f3', 's1'], mode)
            self.assertEqual(audit['reserved_chunk_id'], 's1')
            self.assertEqual(audit['reserved_round_rank'], 1)
            self.assertEqual(audit['reserved_global_rank'], 6)
            self.assertEqual(audit['second_hop_new_count'], 2)
            self.assertEqual(audit['second_hop_new_retained_count'], 1)
            self.assertEqual([r['chunk_id'] for r in ranking if r['selected_rank'] is not None], ids)
            self.assertEqual(next(r for r in ranking if r['chunk_id'] == 'f4')['selected_rank'], None)

    def test_reserved_policy_is_identical_when_one_round_or_no_novel_chunk(self):
        first = [FIRST, SECOND]
        for rounds in ([{'points': first}], [{'points': first}, {'points': [SECOND]}]):
            old, _, _ = select_final_points(QUESTION, rounds, Scorer(), 1)
            new, _, audit = select_final_points(QUESTION, rounds, Scorer(), 1,
                                                policy='reserve_second_hop_one')
            self.assertEqual([p['payload']['chunk_id'] for p in new],
                             [p['payload']['chunk_id'] for p in old])
            self.assertIsNone(audit['reserved_chunk_id'])
            self.assertEqual(audit['second_hop_new_retained_count'], 0)

    def test_reserved_policy_keeps_original_top_five_if_second_hop_already_selected(self):
        first = [point(f'f{i}', 'first.md', f'first score-{i}') for i in range(5)]
        second = [point('s1', 'second.md', 'second score-9')]
        rounds = [{'points': first}, {'points': second}]
        old, _, _ = select_final_points(QUESTION, rounds, Scorer(), 5)
        new, _, audit = select_final_points(QUESTION, rounds, Scorer(), 5,
                                            policy='reserve_second_hop_one')
        self.assertEqual([p['payload']['chunk_id'] for p in new],
                         [p['payload']['chunk_id'] for p in old])
        self.assertEqual(audit['reserved_chunk_id'], 's1')
        self.assertEqual(audit['reserved_global_rank'], 1)

    def test_run_mode_records_reserved_evidence_sent_to_answer(self):
        weak = point('second', 'polaris.md', 'Polaris Relay operator. score-0')

        def search(query, mode):
            selected = [weak] if 'Polaris Relay' in query else [FIRST]
            return {'points': selected, 'candidate_count': 1, 'elapsed_seconds': 0.01}

        for mode in ('D', 'E'):
            events = []
            outcome = run_mode(mode, QUESTION, search, Responder(events), Scorer(), 1,
                               policy='reserve_second_hop_one')
            self.assertEqual(outcome['final_evidence_ids'], ['second'])
            self.assertEqual(outcome['selection_audit']['reserved_chunk_id'], 'second')
            self.assertEqual(events[-1], ('answer', ['second']))
            self.assertEqual(outcome['run']['calls'], {'retrieval': 2, 'model': 2})


if __name__ == '__main__':
    unittest.main()
