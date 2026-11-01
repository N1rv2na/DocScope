"""Run the frozen DOCS-015 dev subset; labels stay in this offline process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from docscope.agent_run import MAX_MODEL_ATTEMPTS, MAX_RETRIEVAL_ROUNDS
from docscope.api import create_app
from docscope.embedding import EMBEDDING_MODEL, EMBEDDING_REVISION, LocalEmbedder
from docscope.evaluation.agent_answer import review_support, score_case, summarize
from docscope.evaluation.run_dense import EvaluationIndex, load_inputs, scroll_chunks


def validate_subset(root: Path, settings: dict, questions: list[dict], articles: list[dict]) -> list[dict]:
    manifest = json.loads((root / settings['manifest']).read_text())
    dense = json.loads((root / 'evaluation/configs/dense_baseline_v1.json').read_text())
    ids = settings['answer_subset_ids']
    if settings['split'] != 'dev' or ids != dense['answer_subset_ids'] or len(ids) != 12:
        raise ValueError('answer subset must be the frozen 12 dev IDs')
    if not set(ids) <= set(manifest['dev_ids']) or set(ids) & set(manifest['test_ids']):
        raise ValueError('answer subset includes an ID outside dev')
    selected = {row['sample_id']: row for row in questions if row['sample_id'] in ids}
    if len(selected) != len(ids) or Counter(selected[item]['question_type'] for item in ids) != {
            'inference_query': 3, 'comparison_query': 3, 'temporal_query': 3, 'null_query': 3}:
        raise ValueError('frozen dev sample is missing or not stratified')
    if len(settings['self_no_evidence']) != 2:
        raise ValueError('exactly two self-authored no-evidence cases are frozen')
    bodies = '\n'.join(article['body'] for article in articles).casefold()
    cases = [selected[item] for item in ids]
    for item in settings['self_no_evidence']:
        if (not item['marker'] or item['marker'].casefold() in bodies or
                item['marker'].casefold() not in item['question'].casefold()):
            raise ValueError('self no-evidence marker is missing or present in indexed corpus')
        cases.append({'sample_id': item['sample_id'], 'question_type': 'self_no_evidence',
                      'query': item['question'], 'answer': None, 'evidence_list': []})
    if len({item['sample_id'] for item in cases}) != len(cases):
        raise ValueError('duplicate frozen sample ID')
    if any(not item['query'].strip() or len(item['query']) > 500 for item in cases):
        raise ValueError('question exceeds Agent API limits')
    if (settings['embedding_model'] != EMBEDDING_MODEL or settings['embedding_revision'] != EMBEDDING_REVISION or
            settings['max_retrieval_rounds'] != MAX_RETRIEVAL_ROUNDS or
            settings['max_model_attempts'] != MAX_MODEL_ATTEMPTS):
        raise ValueError('frozen model or Agent budget differs from implementation')
    return cases


def evaluate_case(client, case: dict, source_document_by_online_id: dict[str, str],
                  source_document_by_chunk_id: dict[str, str], calls: list[dict],
                  max_retrieval_rounds: int, max_model_attempts: int) -> dict:
    start_call = len(calls)
    started = time.monotonic()
    # This is the complete online request. All labels remain on the offline side.
    response = client.post('/api/agent/run', json={'question': case['query']})
    run = response.json()
    if not isinstance(run, dict) or not all(key in run for key in
                                             ('status', 'answer', 'citations', 'retrieved_evidence_ids', 'calls', 'trace')):
        raise ValueError('Agent returned an incomplete result')
    budget = run['calls']
    if (not isinstance(budget, dict) or not isinstance(budget.get('retrieval'), int) or
            not isinstance(budget.get('model'), int) or budget['retrieval'] > max_retrieval_rounds or
            budget['model'] > max_model_attempts or min(budget.values()) < 0):
        raise ValueError('Agent run exceeded or omitted the frozen budget')
    if len(calls) - start_call > max_model_attempts:
        raise ValueError('observed model calls exceeded the frozen budget')
    if response.status_code >= 400 and run['status'] != 'error':
        raise ValueError('Agent HTTP error did not provide an error result')
    relevant = sorted({item['document_id'] for item in case['evidence_list']})
    metrics = score_case(case['sample_id'], case['question_type'], case['answer'], relevant,
                         run, source_document_by_chunk_id)
    citations = [{**item, 'source_document_id': source_document_by_online_id.get(item['document_id'])}
                 for item in run['citations']]
    return {'sample_id': case['sample_id'], 'question_type': case['question_type'],
            'question': case['query'], 'expected_answer_offline': case['answer'],
            'relevant_document_ids_offline': relevant, 'http_status': response.status_code,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'metrics': metrics, 'run': {**run, 'citations': citations},
            'llm_calls': [dict(item) for item in calls[start_call:]]}


def review_template(rows: list[dict], dependency: dict | None) -> dict:
    citations = []
    for row in rows:
        for citation in row['run']['citations']:
            citations.append({'sample_id': row['sample_id'], 'chunk_id': citation['chunk_id'],
                              'source_document_id': citation.get('source_document_id'),
                              'question': row['question'], 'answer': row['run']['answer'],
                              'chunk_text': citation['chunk_text'], 'support': 'pending',
                              'reviewer': None, 'rationale': None})
    return {'protocol': 'Review every cited Chunk against the answer; supported means the cited text directly supports the answer claim. Record reviewer and rationale. Uncertain stays unresolved.',
            'citations': citations, 'summary': review_support(citations),
            'dependency': dependency}


def _observed_client(calls: list[dict]):
    import openai
    original = openai.OpenAI

    def construct(*args, **kwargs):
        client = original(*args, **kwargs)
        create_original = client.chat.completions.create

        def create(**request):
            record = {'requested_model': request.get('model'), 'actual_model': None,
                      'actual_channel': None, 'channel_status': 'not_exposed_by_gateway',
                      'billed_cost_cny': None, 'usage': None, 'finish_reason': None}
            started = time.monotonic()
            try:
                response = create_original(**request)
                record['actual_model'] = response.model
                record['usage'] = response.usage.model_dump() if response.usage else None
                record['finish_reason'] = response.choices[0].finish_reason if response.choices else None
                return response
            except Exception as error:
                record['error_type'] = type(error).__name__
                raise
            finally:
                record['seconds'] = round(time.monotonic() - started, 3)
                calls.append(record)

        client.chat.completions.create = create
        return client

    return construct


def _upload(client, index, filename: str, content: str) -> tuple[str, list[dict]]:
    response = client.post('/api/documents', files={'file': (filename, content.encode(), 'text/markdown')})
    if response.status_code != 201 or response.json().get('status') != 'ready':
        raise RuntimeError(f'upload failed for {filename}: HTTP {response.status_code}')
    online_id = response.json()['document_id']
    return online_id, scroll_chunks(index, online_id)


def _dependency_run(root: Path, settings: dict, embedder, calls: list[dict]) -> dict:
    sample = json.loads((root / settings['dependency_sample']).read_text())
    directory = (root / settings['dependency_sample']).parent
    index = EvaluationIndex(os.getenv('QDRANT_URL', 'http://127.0.0.1:6333'),
                            f'docscope_agent_dependency_{uuid.uuid4().hex}')
    prior_error = None
    try:
        with tempfile.TemporaryDirectory() as data_dir, TestClient(create_app(Path(data_dir), embedder, index)) as client:
            chunks = {}
            online_by_filename = {}
            for name in (sample['first_source'], sample['second_source']):
                online, points = _upload(client, index, name, (directory / name).read_text())
                online_by_filename[name] = online
                chunks.update({point['chunk_id']: {'filename': name, **point} for point in points})
            for position in range(settings['dependency_distractors']):
                name = f'neutral-{position}.md'
                content = f'# Neutral Archive {position}\n\nArchived shipping inventory note {position}; no telemetry audit export or relay operator details.'
                _, points = _upload(client, index, name, content)
                chunks.update({point['chunk_id']: {'filename': name, **point} for point in points})
            start = len(calls)
            response = client.post('/api/agent/run', json={'question': sample['question']})
            run = response.json()
            if not isinstance(run, dict) or 'trace' not in run or 'calls' not in run:
                raise ValueError('dependency Agent response malformed')
            if run['calls']['retrieval'] > settings['max_retrieval_rounds'] or run['calls']['model'] > settings['max_model_attempts']:
                raise ValueError('dependency run exceeded budget')
            if len(calls) - start > settings['max_model_attempts']:
                raise ValueError('observed dependency model calls exceeded budget')
            retrieve = [step for step in run['trace'] if step['stage'] == 'retrieve' and step['status'] == 'completed']
            plans = [step for step in run['trace'] if step['stage'] == 'plan_next' and step['status'] == 'completed']
            next_plan = next((step for step in plans if step.get('next_query')), None)
            first_source = chunks.get(next_plan.get('source_chunk_id')) if next_plan else None
            retrieved_first_source = next((chunks[item] for item in retrieve[0].get('evidence_ids', [])
                                           if item in chunks and chunks[item]['filename'] == sample['first_source']
                                           and sample['bridge_text'].casefold() in chunks[item]['chunk_text'].casefold()), None) if retrieve else None
            fact_source = first_source or retrieved_first_source
            second_new = [chunks[item] for item in retrieve[1].get('new_evidence_ids', []) if item in chunks] if len(retrieve) > 1 else []
            observed = bool(next_plan and len(retrieve) > 1 and first_source and
                            sample['bridge_text'].casefold() in next_plan.get('next_query', '').casefold() and
                            first_source['filename'] == sample['first_source'] and
                            any(item['filename'] == sample['second_source'] for item in second_new))
            return {'sample_id': sample['sample_id'], 'question': sample['question'],
                    'expected_bridge_offline': sample['bridge_text'], 'expected_answer_offline': sample['answer'],
                    'expected_sources_offline': [sample['first_source'], sample['second_source']],
                    'first_hop_fact': {'bridge_text': next_plan.get('bridge_text') if next_plan else sample['bridge_text'],
                                       'source_chunk_id': fact_source['chunk_id'],
                                       'source_filename': fact_source['filename'],
                                       'source_chunk_text': fact_source['chunk_text'],
                                       'origin': 'planner_output' if next_plan else 'offline_match_in_first_retrieval',
                                       'planner_used_bridge': bool(next_plan)} if fact_source else None,
                    'second_hop_query': next_plan.get('next_query') if next_plan else None,
                    'second_hop_new_sources': second_new,
                    'dependency_observed': observed, 'http_status': response.status_code,
                    'run': run, 'llm_calls': [dict(item) for item in calls[start:]],
                    'human_review': {'bridge_fact': 'pending', 'second_query': 'pending',
                                     'support_source': 'pending', 'reviewer': None, 'rationale': None}}
    except Exception as error:
        prior_error = error
        raise
    finally:
        try:
            index._request('DELETE', f'/collections/{index.collection}')
        except Exception:
            if prior_error is None:
                raise


def run(root: Path, settings: dict, output: Path) -> dict:
    review_path = output.with_name(output.stem + '.review.json')
    if output.exists() or review_path.exists():
        raise FileExistsError('evaluation result or human review exists; choose a new --output path')
    articles, questions, _ = load_inputs(root, settings)
    cases = validate_subset(root, settings, questions, articles)
    if not all(os.getenv(key) for key in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL')):
        raise ValueError('LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL required')
    if os.getenv('LLM_MODEL') != settings['requested_model']:
        raise ValueError('effective LLM_MODEL differs from frozen route configuration')
    effective_llm = {'base_url': os.getenv('LLM_BASE_URL'), 'requested_model': os.getenv('LLM_MODEL'),
                     'declared_route_members': settings['declared_route_members'],
                     'route_members_source': settings['route_members_source']}
    config_hash = hashlib.sha256(json.dumps({'settings': settings, 'effective_llm': effective_llm}, sort_keys=True).encode()).hexdigest()
    index = EvaluationIndex(os.getenv('QDRANT_URL', 'http://127.0.0.1:6333'),
                            f'docscope_agent_answer_{uuid.uuid4().hex}')
    if index._request('GET', '/')['version'] != settings['qdrant_version']:
        raise ValueError('Qdrant version differs from frozen configuration')
    calls = []
    rows = []
    dependency = None
    current_sample = None
    started = time.monotonic()
    prior_error = None
    try:
        embedder = LocalEmbedder()
        with tempfile.TemporaryDirectory() as data_dir, patch('openai.OpenAI', _observed_client(calls)), TestClient(
                create_app(Path(data_dir), embedder, index)) as client:
            online_to_source = {}
            chunk_to_source = {}
            for position, article in enumerate(articles, 1):
                online, chunks = _upload(client, index, article['document_id'] + '.md', article['body'])
                online_to_source[online] = article['document_id']
                chunk_to_source.update({chunk['chunk_id']: article['document_id'] for chunk in chunks})
                if position % 50 == 0:
                    print(f'indexed {position}/{len(articles)} articles', flush=True)
            for case in cases:
                current_sample = case['sample_id']
                row = evaluate_case(client, case, online_to_source, chunk_to_source, calls,
                                    settings['max_retrieval_rounds'], settings['max_model_attempts'])
                rows.append(row)
                print(f"evaluated {len(rows)}/{len(cases)} {current_sample}: {row['run']['status']}", flush=True)
            current_sample = 'dependency'
            dependency = _dependency_run(root, settings, embedder, calls)
    except Exception as error:
        prior_error = error
    finally:
        try:
            index._request('DELETE', f'/collections/{index.collection}')
        except Exception as error:
            if prior_error is None:
                prior_error = RuntimeError(f'Qdrant cleanup failed: {type(error).__name__}')
    if prior_error is not None:
        failure = {'status': 'failed', 'error_type': type(prior_error).__name__, 'error': str(prior_error),
                   'current_sample': current_sample, 'completed_cases': len(rows), 'rows': rows,
                   'dependency': dependency, 'llm_calls': calls, 'effective_llm': effective_llm,
                   'config_sha256': config_hash}
        failure_path = output.with_name(output.stem + '.failed.json')
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
        raise RuntimeError(f'Agent evaluation failed at {current_sample}: {prior_error}') from prior_error
    summary = summarize([row['metrics'] for row in rows])
    summary.update({'corpus_count': len(articles), 'dependency_sample_count': 1,
                    'llm_call_count': len(calls),
                    'prompt_tokens': sum((item.get('usage') or {}).get('prompt_tokens') or 0 for item in calls),
                    'completion_tokens': sum((item.get('usage') or {}).get('completion_tokens') or 0 for item in calls),
                    'usage_available_calls': sum(item.get('usage') is not None for item in calls),
                    'usage_unavailable_calls': sum(item.get('usage') is None for item in calls),
                    'token_count_scope': 'sum_of_calls_with_reported_usage_only',
                    'billed_cost_cny': None,
                    'cost_note': 'Gateway did not expose billed cost; null is unknown, not zero.',
                    'elapsed_seconds': round(time.monotonic() - started, 3),
                    'scope': 'fixed_dev_subset_only_not_full_answer_benchmark'})
    review = review_template(rows, dependency)
    summary['citation_support'] = review['summary']
    result = {'config': settings, 'config_sha256': config_hash, 'effective_llm': effective_llm,
              'summary': summary, 'rows': rows, 'dependency': dependency,
              'citation_support_review': review}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('evaluation/configs/agent_answer_dev_v1.json'))
    parser.add_argument('--output', type=Path, default=Path('evaluation/results/agent_answer_dev_v1.json'))
    args = parser.parse_args()
    result = run(Path.cwd(), json.loads(args.config.read_text()), args.output)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
