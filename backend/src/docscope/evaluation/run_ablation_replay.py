"""Replay frozen A–E retrieval queries without another LLM call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from docscope.api import create_app
from docscope.embedding import LocalEmbedder
from docscope.evaluation.ablation import validate_config
from docscope.evaluation.run_ablation import EvalRetriever, attach_source_ids, select_final_points
from docscope.evaluation.run_agent_answer import _upload, validate_subset
from docscope.evaluation.run_dense import EvaluationIndex, load_inputs
from docscope.reranking import LocalReranker


def select_source_rows(source_result: dict, sample_ids: list[str], frozen_ids: set[str]) -> list[dict]:
    """Select only saved D/E queries from the frozen dev subset."""
    if not sample_ids or len(set(sample_ids)) != len(sample_ids) or not set(sample_ids) <= frozen_ids:
        raise ValueError('replay sample IDs must be unique frozen dev IDs')
    selected = []
    for sample_id in sample_ids:
        for mode in ('D', 'E'):
            matches = [row for row in source_result['rows']
                       if row['sample_id'] == sample_id and row['mode'] == mode]
            if len(matches) != 1 or not 1 <= len(matches[0]['retrievals']) <= 2:
                raise ValueError('saved D/E retrieval queries are missing or duplicated')
            row = matches[0]
            if (row['retrievals'][0]['query'] != row['question']
                    or any(not item.get('query') for item in row['retrievals'])):
                raise ValueError('saved retrieval queries are invalid')
            selected.append(row)
    return selected


def replay_case(source_row: dict, search, scorer, final_limit: int,
                source_by_online: dict[str, str], support_articles: set[str]) -> dict:
    """Track each labeled source from Qdrant candidates through final evidence."""
    rounds = []
    for recorded in source_row['retrievals']:
        query = recorded['query']
        result = search(query, source_row['mode'])
        rounds.append({'query': query, **result})
    selected, ranking, selection_audit = select_final_points(source_row['question'], rounds, scorer, final_limit)
    retrievals = attach_source_ids([{
        'query': item['query'], 'candidate_count': item['candidate_count'],
        'elapsed_seconds': item['elapsed_seconds'], 'candidates': item['candidates'],
        'evidence_ids': [point['payload']['chunk_id'] for point in item['points']]}
        for item in rounds], source_by_online)
    final_ids = {point['payload']['chunk_id'] for point in selected}
    selected_docs = {source_by_online.get(point['payload']['document_id']) for point in selected}
    paths = []
    for source in sorted(support_articles):
        candidate_rounds = [number for number, item in enumerate(retrievals, 1)
                            if any(candidate['source_document_id'] == source for candidate in item['candidates'])]
        round_selected_rounds = [number for number, item in enumerate(rounds, 1)
                                 if any(source_by_online.get(point['payload']['document_id']) == source
                                        for point in item['points'])]
        if source in selected_docs:
            stage = 'retained'
        elif round_selected_rounds:
            stage = 'dropped_final_merge'
        elif candidate_rounds:
            stage = 'dropped_per_round_rerank'
        else:
            stage = 'not_in_rrf_candidates'
        paths.append({'source_document_id': source, 'candidate_rounds': candidate_rounds,
                      'round_selected_rounds': round_selected_rounds,
                      'final_selected': source in selected_docs, 'stage': stage})
    first = {point['payload']['chunk_id'] for point in rounds[0]['points']} if rounds else set()
    second = {point['payload']['chunk_id'] for point in rounds[1]['points']} if len(rounds) > 1 else set()
    new = second - first
    return {'sample_id': source_row['sample_id'], 'mode': source_row['mode'],
            'source_run_status': source_row['status'], 'question': source_row['question'],
            'retrievals': retrievals,
            'final_ranking': [{**item, 'source_document_id': source_by_online.get(item['document_id'])}
                              for item in ranking],
            'final_evidence_ids': [point['payload']['chunk_id'] for point in selected],
            'selection_audit': selection_audit,
            'support_article_path': paths, 'second_hop_new_count': len(new),
            'second_hop_new_retained_count': len(new & final_ids)}


def run_replay(root: Path, source_path: Path, output: Path, sample_ids: list[str]) -> dict:
    """Reindex frozen public articles and replay recorded queries without Magpie."""
    if output.exists():
        raise FileExistsError('retrieval replay result exists; choose a new --output path')
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    settings = source['config']
    answer_settings = json.loads((root / 'evaluation/configs/agent_answer_dev_v1.json').read_text())
    rerank_settings = json.loads((root / 'evaluation/configs/rerank_dev_v1.json').read_text())
    validate_config(settings, answer_settings, rerank_settings)
    articles, questions, _ = load_inputs(root, settings)
    cases = validate_subset(root, settings, questions, articles)
    selected = select_source_rows(source, sample_ids, {case['sample_id'] for case in cases})
    case_by_id = {case['sample_id']: case for case in cases}
    index = EvaluationIndex(os.getenv('QDRANT_URL', 'http://127.0.0.1:6333'),
                            f'docscope_ablation_replay_{uuid.uuid4().hex}')
    if index._request('GET', '/')['version'] != settings['qdrant_version']:
        raise ValueError('Qdrant version differs from frozen ablation configuration')
    started = datetime.now(timezone.utc).isoformat()
    rows = []
    failure = None
    try:
        embedder, reranker = LocalEmbedder(), LocalReranker()
        reranker.score('warmup', ['warmup'])
        with tempfile.TemporaryDirectory() as data_dir, TestClient(create_app(Path(data_dir), embedder, index)) as client:
            online_by_source = {}
            for position, article in enumerate(articles, 1):
                online, _ = _upload(client, index, article['document_id'] + '.md', article['body'])
                online_by_source[article['document_id']] = online
                if position % 50 == 0:
                    print(f'indexed {position}/{len(articles)} replay articles', flush=True)
            source_by_online = {online: source_id for source_id, online in online_by_source.items()}
            retrieval = EvalRetriever(embedder, index, reranker,
                                      settings['final_evidence_limit'], settings['candidate_limit'])
            for source_row in selected:
                support = {item['document_id'] for item in case_by_id[source_row['sample_id']]['evidence_list']}
                row = replay_case(source_row, retrieval.search, reranker,
                                  settings['final_evidence_limit'], source_by_online, support)
                rows.append(row)
                print(f"replayed {row['sample_id']}/{row['mode']}", flush=True)
    except Exception as error:
        failure = error
    finally:
        try:
            index._request('DELETE', f'/collections/{index.collection}')
        except Exception as error:
            if failure is None:
                failure = RuntimeError(f'Qdrant replay cleanup failed: {type(error).__name__}')
    if failure is not None:
        raise RuntimeError(f'retrieval replay failed: {failure}') from failure
    result = {'kind': 'retrieval_only_replay_not_original_run',
              'source_result': str(source_path),
              'source_result_sha256': hashlib.sha256(source_bytes).hexdigest(),
              'source_config_sha256': source['config_sha256'],
              'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
              'corpus_count': len(articles), 'qdrant_version': settings['qdrant_version'],
              'embedding_model': settings['embedding_model'], 'embedding_revision': settings['embedding_revision'],
              'reranker_model': settings['reranker_model'], 'reranker_revision': settings['reranker_revision'],
              'no_llm_calls': True,
              'identity_note': 'online document and Chunk IDs are new; compare frozen source_document_id and text',
              'summary': {'source_stage_counts': dict(Counter(path['stage'] for row in rows
                                                               for path in row['support_article_path'])),
                          'second_hop_new_count': sum(row['second_hop_new_count'] for row in rows),
                          'second_hop_new_retained_count': sum(row['second_hop_new_retained_count'] for row in rows)},
              'rows': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample-id', action='append', required=True)
    args = parser.parse_args()
    result = run_replay(Path.cwd(), args.source, args.output, args.sample_id)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
