"""DOCS-016 paired A–E runs on one frozen dev corpus and one Qdrant index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from docscope.agent_run import AgentRunner
from docscope.api import ModelAnswer, create_app, validated_answer
from docscope.embedding import LocalEmbedder
from docscope.evaluation.ablation import (MODE_NAMES, choose_final_ids, plan_queries, rank_final_context, render_report,
                                          summarize_comparison, validate_config)
from docscope.evaluation.agent_answer import score_case
from docscope.evaluation.dense_baseline import map_facts
from docscope.evaluation.run_agent_answer import _observed_client, _upload, validate_subset
from docscope.evaluation.run_dense import EvaluationIndex, load_inputs, scroll_chunks
from docscope.llm import OpenAIResponder, PLANNER_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from docscope.reranking import LocalReranker, rank_points


def _evidence(point: dict, stage: str, rank: int) -> dict:
    payload = point['payload']
    return {key: payload.get(key) for key in (
        'document_id', 'chunk_id', 'document_title', 'source_path', 'page_number',
        'section', 'line_start', 'chunk_text', 'index_version')}


def _audit_point(point: dict) -> dict:
    payload = point['payload']
    return {'chunk_id': payload['chunk_id'], 'document_id': payload['document_id'],
            'document_title': payload['document_title'], 'chunk_text': payload['chunk_text']}


def attach_source_ids(retrievals: list[dict], source_by_online: dict[str, str]) -> list[dict]:
    """Add offline provenance to candidate records after all model calls finish."""
    return [{**item, 'candidates': [{**candidate,
             'source_document_id': source_by_online.get(candidate['document_id'])}
             for candidate in item['candidates']]} for item in retrievals]


class EvalRetriever:
    """Use the same Dense, Qdrant RRF and local Cross-Encoder primitives as the API."""

    def __init__(self, embedder, index, reranker, final_limit: int, candidate_limit: int):
        self.embedder = embedder
        self.index = index
        self.reranker = reranker
        self.final_limit = final_limit
        self.candidate_limit = candidate_limit

    def search(self, query: str, mode: str) -> dict:
        started = time.monotonic()
        vector = self.embedder.encode([query])[0]
        if mode == 'A':
            candidates = self.index.query(vector, None, self.final_limit)
            selected = candidates
        elif mode == 'B':
            candidates = self.index.query_hybrid(vector, query, None, self.final_limit, self.candidate_limit)
            selected = candidates
        elif mode in {'C', 'D', 'E'}:
            candidates = self.index.query_hybrid(vector, query, None, self.candidate_limit, self.candidate_limit)
            ranked = rank_points(query, candidates, self.reranker)
            selected = ranked[:self.final_limit]
        else:
            raise ValueError('invalid ablation mode')
        selected_rank = {point['payload']['chunk_id']: rank for rank, point in enumerate(selected, 1)}
        rerank_scores = ({point['payload']['chunk_id']: point['rerank_score'] for point in ranked}
                         if mode in {'C', 'D', 'E'} else {})
        audit = [{**_audit_point(point), 'qdrant_rank': rank,
                  'qdrant_score': point['score'],
                  'rerank_score': rerank_scores.get(point['payload']['chunk_id']),
                  'selected_rank': selected_rank.get(point['payload']['chunk_id'])}
                 for rank, point in enumerate(candidates, 1)]
        return {'points': selected, 'candidate_count': len(candidates),
                'candidates': audit,
                'elapsed_seconds': round(time.monotonic() - started, 3)}


class EvaluationResponder(OpenAIResponder):
    """Share frozen evaluation-only output caps across A–E."""

    def __init__(self, answer_max_tokens: int, planning_max_tokens: int):
        self.answer_max_tokens = answer_max_tokens
        self.planning_max_tokens = planning_max_tokens

    def generate(self, question: str, evidence: list[dict]) -> dict:
        return self._complete_json('answer', SYSTEM_INSTRUCTIONS, self._user_message(question, evidence),
                                   self.answer_max_tokens)

    def plan_next(self, question: str, evidence: list[dict]) -> dict:
        return self._complete_json('plan_next', PLANNER_INSTRUCTIONS, self._user_message(question, evidence),
                                   self.planning_max_tokens)


def select_final_points(question: str, retrievals: list[dict], scorer, limit: int,
                        policy: str = 'original_query_top_k') -> tuple[list[dict], list[dict], dict]:
    """Score the D/E union once, then apply the frozen final evidence policy."""
    if not 1 <= limit <= 5:
        raise ValueError('invalid final evidence limit')
    if policy not in {'original_query_top_k', 'reserve_second_hop_one'}:
        raise ValueError('invalid final merge policy')
    points = [point for item in retrievals for point in item['points']]
    first_ids = {point['payload']['chunk_id'] for point in retrievals[0]['points']} if retrievals else set()
    second_order = ([point['payload']['chunk_id'] for point in retrievals[1]['points']]
                    if len(retrievals) > 1 else [])
    if len(retrievals) <= 1:
        selected_ids, audit = choose_final_ids([point['payload']['chunk_id'] for point in points],
                                                first_ids, [], limit, policy)
        return [point for point in points if point['payload']['chunk_id'] in selected_ids][:limit], [], audit
    ranked = rank_final_context(question, points, scorer)
    chosen_ids, audit = choose_final_ids([point['payload']['chunk_id'] for point in ranked],
                                         first_ids, second_order, limit, policy)
    selected_ids = set(chosen_ids)
    selected = [point for point in ranked if point['payload']['chunk_id'] in selected_ids]
    selected_rank = {point['payload']['chunk_id']: rank for rank, point in enumerate(selected, 1)}
    rounds_by_id = {}
    for round_number, item in enumerate(retrievals, 1):
        for point in item['points']:
            rounds_by_id.setdefault(point['payload']['chunk_id'], []).append(round_number)
    ranking = [{**_audit_point(point), 'from_rounds': rounds_by_id[point['payload']['chunk_id']],
                'rerank_score': point['rerank_score'],
                'selected_rank': selected_rank.get(point['payload']['chunk_id'])}
               for point in ranked]
    return selected, ranking, audit


def run_mode(mode: str, question: str, search, responder, scorer, final_limit: int,
             policy: str = 'original_query_top_k') -> dict:
    """Execute one mode without access to answer or supporting-fact labels."""
    if mode not in MODE_NAMES or not 1 <= final_limit <= 5:
        raise ValueError('invalid mode or evidence limit')
    started = time.monotonic()
    retrievals = []
    final_ranking = []
    selection_audit = {}
    selected_cache = None

    def fetch(query: str) -> list[dict]:
        result = search(query, mode)
        retrievals.append({'query': query, **result})
        return [_evidence(point, mode, rank) for rank, point in enumerate(result['points'], 1)]

    def selected_points() -> list[dict]:
        nonlocal final_ranking, selected_cache, selection_audit
        if selected_cache is not None:
            return selected_cache
        selected_cache, final_ranking, selection_audit = select_final_points(
            question, retrievals, scorer, final_limit, policy=policy)
        return selected_cache

    final_evidence = []

    def generate(user_question: str, evidence: list[dict]) -> dict:
        nonlocal final_evidence
        final_evidence = [_evidence(point, mode, rank) for rank, point in enumerate(selected_points(), 1)]
        return responder.generate(user_question, final_evidence)

    def validate(raw: dict, evidence: list[dict]) -> dict:
        return validated_answer(ModelAnswer.model_validate(raw), final_evidence)

    if mode != 'D':
        agent = AgentRunner(fetch, generate, validate, lambda evidence: True,
                            plan_next=responder.plan_next if mode == 'E' else None,
                            extra_model_requests=0)
        run = agent.run(question)
    else:
        trace = []
        calls = {'retrieval': 0, 'model': 1}
        status, stop_reason, error_code = None, None, None
        answer, citations = None, []
        try:
            queries = plan_queries(question, responder._complete_json,
                                   getattr(responder, 'planning_max_tokens', 2048))
            trace.append({'stage': 'plan_once', 'status': 'completed', 'queries': queries})
        except ValueError:
            queries = []
            error_code = 'plan_output_invalid'
            trace.append({'stage': 'plan_once', 'status': 'failed'})
        except Exception:
            queries = []
            error_code = 'planner_failed'
            trace.append({'stage': 'plan_once', 'status': 'failed'})
        if error_code is None:
            for query in queries:
                calls['retrieval'] += 1
                try:
                    evidence = fetch(query)
                except Exception:
                    error_code = 'retrieval_failed'
                    trace.append({'stage': 'retrieve', 'status': 'failed', 'query': query})
                    break
                trace.append({'stage': 'retrieve', 'status': 'completed', 'round': calls['retrieval'],
                              'query': query, 'evidence_ids': [item['chunk_id'] for item in evidence]})
        if error_code is None:
            try:
                final_evidence = [_evidence(point, mode, rank) for rank, point in enumerate(selected_points(), 1)]
                if final_evidence:
                    calls['model'] += 1
                    outcome = validated_answer(ModelAnswer.model_validate(
                        responder.generate(question, final_evidence)), final_evidence)
                    status, answer, citations = outcome['status'], outcome['answer'], outcome['citations']
                    stop_reason = 'answered' if status == 'answered' else 'model_abstained'
                    trace.append({'stage': 'answer', 'status': 'completed'})
                else:
                    status, stop_reason = 'insufficient_evidence', 'no_evidence'
            except ValueError:
                error_code = 'model_output_invalid'
                trace.append({'stage': 'answer', 'status': 'failed'})
            except Exception:
                error_code = 'model_failed'
                trace.append({'stage': 'answer', 'status': 'failed'})
        if error_code is not None:
            status, stop_reason, answer, citations = 'error', error_code, None, []
        run = {'run_id': str(uuid.uuid4()), 'question': question, 'status': status,
               'stop_reason': stop_reason, 'answer': answer, 'citations': citations,
               'retrieved_evidence_ids': list(dict.fromkeys(
                   point['payload']['chunk_id'] for item in retrievals for point in item['points'])),
               'calls': calls, 'trace': trace,
               'elapsed_ms': round((time.monotonic() - started) * 1000, 3)}
        if error_code is not None:
            run['error_code'] = error_code
    if not final_evidence:
        try:
            final_evidence = [_evidence(point, mode, rank) for rank, point in enumerate(selected_points(), 1)]
        except Exception:
            final_evidence = []
    return {'run': run, 'final_evidence_ids': [item['chunk_id'] for item in final_evidence],
            'final_evidence': final_evidence,
            'final_ranking': final_ranking,
            'selection_audit': selection_audit,
            'retrievals': [{'query': item['query'], 'candidate_count': item['candidate_count'],
                            'elapsed_seconds': item['elapsed_seconds'],
                            'candidates': item.get('candidates', []),
                            'evidence_ids': [point['payload']['chunk_id'] for point in item['points']]}
                           for item in retrievals],
            'candidate_count': sum(item['candidate_count'] for item in retrievals),
            'final_evidence_count': len(final_evidence),
            'elapsed_seconds': round(time.monotonic() - started, 3)}


def _evaluate_rows(root: Path, settings: dict, articles: list[dict], cases: list[dict],
                   embedder, reranker, index, calls: list[dict], rows: list[dict]) -> None:
    with tempfile.TemporaryDirectory() as data_dir, TestClient(create_app(Path(data_dir), embedder, index)) as client:
        online_by_source = {}
        chunks_by_online = {}
        source_by_chunk = {}
        for position, article in enumerate(articles, 1):
            online, chunks = _upload(client, index, article['document_id'] + '.md', article['body'])
            online_by_source[article['document_id']] = online
            chunks_by_online[online] = chunks
            source_by_chunk.update({chunk['chunk_id']: article['document_id'] for chunk in chunks})
            if position % 50 == 0:
                print(f'indexed {position}/{len(articles)} articles', flush=True)
        source_by_online = {online: source for source, online in online_by_source.items()}
        retrieval = EvalRetriever(embedder, index, reranker, settings['final_evidence_limit'], settings['candidate_limit'])
        responder = EvaluationResponder(settings['answer_max_tokens'], settings['planning_max_tokens'])
        for case in cases:
            annotated = [{'document_id': online_by_source[item['document_id']], 'fact': item['fact']}
                         for item in case['evidence_list']]
            mapping = map_facts(annotated, chunks_by_online)
            for mode in MODE_NAMES:
                before = len(calls)
                outcome = run_mode(mode, case['query'], retrieval.search, responder, reranker,
                                   settings['final_evidence_limit'],
                                   policy=(settings.get('final_merge_policy', 'original_query_top_k')
                                           if mode in {'D', 'E'} else 'original_query_top_k'))
                run = outcome['run']
                actual_calls = calls[before:]
                max_calls = 2 if mode in {'D', 'E'} else 1
                max_retrieval = 2 if mode in {'D', 'E'} else 1
                if (run['calls']['model'] > max_calls or len(actual_calls) > max_calls
                        or run['calls']['retrieval'] > max_retrieval
                        or outcome['candidate_count'] > settings['candidate_limit'] * max_retrieval
                        or outcome['final_evidence_count'] > settings['final_evidence_limit']):
                    raise ValueError(f'{case["sample_id"]}/{mode} exceeded frozen budget')
                final_ids = set(outcome['final_evidence_ids'])
                metrics = score_case(case['sample_id'], case['question_type'], case['answer'],
                                     sorted({item['document_id'] for item in case['evidence_list']}),
                                     {**run, 'retrieved_evidence_ids': list(final_ids)}, source_by_chunk)
                row = {'sample_id': case['sample_id'], 'question_type': case['question_type'],
                       'question': case['query'], 'mode': mode, 'status': run['status'],
                       'metrics': metrics, 'calls': run['calls'], 'run': run,
                       'retrievals': attach_source_ids(outcome['retrievals'], source_by_online),
                       'candidate_count': outcome['candidate_count'],
                       'final_ranking': [{**item, 'source_document_id': source_by_online.get(item['document_id'])}
                                         for item in outcome['final_ranking']],
                       'selection_audit': outcome['selection_audit'],
                       'final_evidence_ids': outcome['final_evidence_ids'],
                       'final_evidence_count': outcome['final_evidence_count'],
                       'final_evidence': [{**item, 'source_document_id': source_by_online.get(item['document_id'])}
                                          for item in outcome['final_evidence']],
                       'all_mapped_facts_in_context': (all(item['chunk_id'] in final_ids for item in mapping['facts'])
                                                       if mapping['status'] == 'mapped' else None),
                       'fact_mapping_status': mapping['status'],
                       'fact_mapping_failures': [{**item, 'source_document_id': source_by_online.get(item['document_id'])}
                                                 for item in mapping.get('failures', [])],
                       'llm_calls': [dict(item) for item in actual_calls],
                       'elapsed_seconds': outcome['elapsed_seconds']}
                rows.append(row)
                print(f"{len(rows)}/{len(cases) * 5} {case['sample_id']}/{mode}: {run['status']}", flush=True)



def run(root: Path, settings: dict, output: Path) -> dict:
    if output.exists() or output.with_suffix('.md').exists():
        raise FileExistsError('A–E result or report exists; choose a new --output path')
    answer_settings = json.loads((root / 'evaluation/configs/agent_answer_dev_v1.json').read_text())
    rerank_settings = json.loads((root / 'evaluation/configs/rerank_dev_v1.json').read_text())
    validate_config(settings, answer_settings, rerank_settings)
    articles, questions, _ = load_inputs(root, settings)
    cases = validate_subset(root, settings, questions, articles)
    if not all(os.getenv(key) for key in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL')):
        raise ValueError('LLM_BASE_URL, LLM_API_KEY and LLM_MODEL required')
    if os.getenv('LLM_MODEL') != settings['requested_model']:
        raise ValueError('effective LLM_MODEL differs from frozen requested model')
    effective_llm = {'base_url': os.getenv('LLM_BASE_URL'), 'requested_model': os.getenv('LLM_MODEL'),
                     'declared_route_members': settings['declared_route_members'],
                     'route_members_source': settings['route_members_source']}
    config_sha256 = hashlib.sha256(json.dumps({'settings': settings, 'effective_llm': effective_llm},
                                               sort_keys=True).encode()).hexdigest()
    index = EvaluationIndex(os.getenv('QDRANT_URL', 'http://127.0.0.1:6333'),
                            f'docscope_ablation_{uuid.uuid4().hex}')
    if index._request('GET', '/')['version'] != settings['qdrant_version']:
        raise ValueError('Qdrant version differs from frozen configuration')
    calls = []
    rows = []
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    failure = None
    try:
        embedder, reranker = LocalEmbedder(), LocalReranker()
        reranker.score('warmup', ['warmup'])
        with patch('openai.OpenAI', _observed_client(calls)):
            _evaluate_rows(root, settings, articles, cases, embedder, reranker, index, calls, rows)
    except Exception as error:
        failure = error
    finally:
        try:
            index._request('DELETE', f'/collections/{index.collection}')
        except Exception as error:
            if failure is None:
                failure = RuntimeError(f'Qdrant cleanup failed: {type(error).__name__}')
    if failure is not None:
        path = output.with_name(output.stem + '.failed.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'status': 'failed', 'error_type': type(failure).__name__,
                                    'error': str(failure), 'completed_rows': len(rows),
                                    'rows': rows, 'calls': calls, 'config_sha256': config_sha256},
                                   ensure_ascii=False, indent=2) + '\n')
        raise RuntimeError(f'A–E run failed: {failure}') from failure
    if len(rows) != len(cases) * 5 or {(item['sample_id'], item['mode']) for item in rows} != {
            (case['sample_id'], mode) for case in cases for mode in MODE_NAMES}:
        raise ValueError('A–E result is missing or duplicating a frozen case')
    summary = summarize_comparison(rows, [case['sample_id'] for case in cases])
    result = {'config': settings, 'config_sha256': config_sha256, 'effective_llm': effective_llm,
              'run_window_utc': {'start': started_at, 'end': datetime.now(timezone.utc).isoformat()},
              'corpus_count': len(articles), 'sample_count': len(cases), 'scope': 'fixed_dev_subset_not_full_benchmark',
              'summary': summary, 'rows': rows, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    output.with_suffix('.md').write_text(render_report(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('evaluation/configs/ablation_dev_v1.json'))
    parser.add_argument('--output', type=Path, default=Path('evaluation/results/ablation_dev_v1.json'))
    args = parser.parse_args()
    result = run(Path.cwd(), json.loads(args.config.read_text()), args.output)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
