"""Paired D/E evidence selection from saved Top-20 candidates, without retrieval or LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from docscope.evaluation.dense_baseline import map_facts, normalized
from docscope.evaluation.run_ablation import select_final_points
from docscope.evaluation.run_agent_answer import validate_subset
from docscope.evaluation.run_dense import load_inputs
from docscope.markdown import markdown_chunks
from docscope.reranking import LocalReranker, RERANKER_MODEL, RERANKER_REVISION


DEPTHS = (5, 8, 10)


def _ranked_candidates(retrieval: dict) -> list[dict]:
    candidates = retrieval.get('candidates')
    if (not isinstance(candidates, list) or len(candidates) != retrieval.get('candidate_count')
            or len(candidates) > 20):
        raise ValueError('saved Top-20 candidates are incomplete')
    required = {'chunk_id', 'document_id', 'source_document_id', 'document_title',
                'chunk_text', 'qdrant_rank', 'qdrant_score', 'rerank_score', 'selected_rank'}
    if (any(not isinstance(item, dict) or not required <= item.keys()
            or not isinstance(item['chunk_text'], str) or not item['chunk_text']
            or not isinstance(item['rerank_score'], (int, float))
            or not math.isfinite(item['rerank_score']) for item in candidates)
            or [item['qdrant_rank'] for item in candidates] != list(range(1, len(candidates) + 1))
            or len({item['chunk_id'] for item in candidates}) != len(candidates)):
        raise ValueError('saved Top-20 candidates lack complete ranks, scores or text')
    ranked = sorted(candidates, key=lambda item: -item['rerank_score'])
    original = retrieval.get('evidence_ids')
    if ([item['chunk_id'] for item in ranked[:5]] != original
            or any(item['selected_rank'] != (rank if rank <= 5 else None)
                   for rank, item in enumerate(ranked, 1))):
        raise ValueError('saved Top-5 round selection differs from candidate scores')
    return ranked


def _point(candidate: dict) -> dict:
    return {'score': candidate['qdrant_score'],
            'payload': {key: candidate[key] for key in
                        ('chunk_id', 'document_id', 'document_title', 'chunk_text')}}


def _loss_stage(matches: list[str], kept_ids: set[str], selected_ids: set[str]) -> str:
    if any(chunk_id in selected_ids for chunk_id in matches):
        return 'retained_final'
    if any(chunk_id in kept_ids for chunk_id in matches):
        return 'dropped_final_merge'
    return 'dropped_per_round_rerank' if matches else 'not_in_rrf_top20'


def _score(selected: list[dict], relevant: set[str], case: dict, mapping: dict,
           chunks_by_source: dict[str, list[dict]], all_candidates: list[dict],
           kept_ids: set[str]) -> tuple[dict, list[dict], list[dict]]:
    selected_ids = {item['chunk_id'] for item in selected}
    found = {item['source_document_id'] for item in selected} & relevant
    metrics = {'article_recall': len(found) / len(relevant) if relevant else None,
               'all_support_articles': found == relevant if relevant else None,
               'fact_coverage': None, 'all_facts': None}
    article_paths = []
    for source in sorted(relevant):
        matches = list(dict.fromkeys(item['chunk_id'] for item in all_candidates
                                     if item['source_document_id'] == source))
        article_paths.append({'source_document_id': source, 'candidate_chunk_ids': matches,
                              'stage': _loss_stage(matches, kept_ids, selected_ids)})
    paths = []
    if mapping['status'] == 'mapped':
        for index, fact in enumerate(case['evidence_list']):
            mapped_id = mapping['facts'][index]['chunk_id']
            mapped_text = next(item['chunk_text'] for item in chunks_by_source[fact['document_id']]
                               if item['chunk_id'] == mapped_id)
            matches = list(dict.fromkeys(item['chunk_id'] for item in all_candidates
                                         if item['source_document_id'] == fact['document_id']
                                         and normalized(item['chunk_text']) == normalized(mapped_text)))
            paths.append({'fact_index': index, 'source_document_id': fact['document_id'],
                          'candidate_chunk_ids': matches,
                          'stage': _loss_stage(matches, kept_ids, selected_ids)})
        found_count = sum(item['stage'] == 'retained_final' for item in paths)
        metrics['fact_coverage'] = found_count / len(paths)
        metrics['all_facts'] = found_count == len(paths)
    return metrics, paths, article_paths


def compare_depth_row(row: dict, case: dict, chunks_by_source: dict[str, list[dict]],
                      scorer, depths: tuple[int, ...] = DEPTHS,
                      final_evidence_limit: int = 5) -> dict:
    """Re-select saved rounds; consult labels only after the same scorer/policy selects evidence."""
    if (row.get('mode') not in {'D', 'E'} or tuple(depths) != DEPTHS
            or type(final_evidence_limit) is not int or final_evidence_limit != 5):
        raise ValueError('paired depth experiment requires D/E, depths 5/8/10 and final limit 5')
    retrievals = row.get('retrievals')
    if not isinstance(retrievals, list) or len(retrievals) > 2:
        raise ValueError('saved D/E retrieval rounds are invalid')
    ranked_rounds = [_ranked_candidates(item) for item in retrievals]
    all_candidates = [item for ranked in ranked_rounds for item in ranked]
    by_id = {item['chunk_id']: item for item in all_candidates}
    if any(not item.get('source_document_id') for item in all_candidates):
        raise ValueError('saved candidate source ID is missing')

    # Freeze the first-hop planner input and every query: no planner is called here.
    selections = {}
    for depth in depths:
        kept = [ranked[:depth] for ranked in ranked_rounds]
        rounds = [{'points': [_point(item) for item in group]} for group in kept]
        selected_points, ranking, audit = select_final_points(
            row['question'], rounds, scorer, final_evidence_limit,
            policy='reserve_second_hop_one')
        selected_ids = [item['payload']['chunk_id'] for item in selected_points]
        if depth == 5 and selected_ids != row.get('final_evidence_ids'):
            raise ValueError(f"Top-5 final evidence differs from saved row {row['sample_id']}/{row['mode']}")
        first = {item['chunk_id'] for item in kept[0]} if kept else set()
        second = {item['chunk_id'] for item in kept[1]} if len(kept) > 1 else set()
        kept_ids = first | second
        if len(kept_ids) > 20 or len(selected_ids) > final_evidence_limit:
            raise ValueError('paired depth experiment exceeded frozen evidence bounds')
        if len(kept) <= 1:
            ranking = [{'chunk_id': item['chunk_id'], 'document_id': item['document_id'],
                        'source_document_id': item['source_document_id'],
                        'rerank_score': item['rerank_score'],
                        'selected_rank': rank if rank <= final_evidence_limit else None,
                        'from_rounds': [1]} for rank, item in enumerate(kept[0] if kept else [], 1)]
        selections[str(depth)] = {
            'selected_ids': selected_ids,
            'selected_evidence': [{**{key: by_id[chunk_id][key] for key in
                                     ('chunk_id', 'source_document_id', 'document_title', 'chunk_text')},
                                   'final_rank': rank} for rank, chunk_id in enumerate(selected_ids, 1)],
            'final_ranking': ranking, 'selection_audit': audit,
            'per_round_retained_ids': [[item['chunk_id'] for item in group] for group in kept],
            'per_round_retained_count': [len(group) for group in kept],
            'pre_dedup_count': sum(len(group) for group in kept),
            'unique_union_count': len(kept_ids),
            'second_hop_new_count': len(second - first),
            'second_hop_new_retained_count': len((second - first) & set(selected_ids)),
        }

    mapping = map_facts(case['evidence_list'], chunks_by_source)
    relevant = {item['document_id'] for item in case['evidence_list']}
    for selection in selections.values():
        selected = [by_id[chunk_id] for chunk_id in selection['selected_ids']]
        kept_ids = {chunk_id for group in selection['per_round_retained_ids'] for chunk_id in group}
        selection['metrics'], selection['required_fact_paths'], selection['support_article_paths'] = _score(
            selected, relevant, case, mapping, chunks_by_source, all_candidates, kept_ids)
    return {'sample_id': row['sample_id'], 'mode': row['mode'], 'question': row['question'],
            'queries': [item['query'] for item in retrievals],
            'rrf_candidate_ids': [[item['chunk_id'] for item in item['candidates']]
                                  for item in retrievals],
            'original_first_round_evidence_ids': retrievals[0]['evidence_ids'] if retrievals else [],
            'support_article_count': len(relevant), 'fact_mapping_status': mapping['status'],
            'fact_mapping_failures': mapping.get('failures', []), 'depths': selections}


def _paired_summary(rows: list[dict]) -> dict:
    summary = {}
    for mode in ('overall', 'D', 'E'):
        group = rows if mode == 'overall' else [row for row in rows if row['mode'] == mode]
        by_depth = {}
        for depth in map(str, DEPTHS):
            selections = [row['depths'][depth] for row in group]
            def aggregate(metric):
                values = [selection['metrics'][metric] for selection in selections
                          if selection['metrics'][metric] is not None]
                return {'sum': sum(values), 'denominator': len(values),
                        'mean': sum(values) / len(values) if values else None}
            by_depth[depth] = {
                'article_recall': aggregate('article_recall'),
                'all_support_articles': aggregate('all_support_articles'),
                'fact_coverage': aggregate('fact_coverage'),
                'all_facts': aggregate('all_facts'),
                'per_round_retained_total': sum(sum(item['per_round_retained_count']) for item in selections),
                'pre_dedup_total': sum(item['pre_dedup_count'] for item in selections),
                'unique_union_total': sum(item['unique_union_count'] for item in selections),
                'second_hop_new_total': sum(item['second_hop_new_count'] for item in selections),
                'second_hop_new_retained_total': sum(item['second_hop_new_retained_count'] for item in selections),
                'loss_stages': dict(Counter(path['stage'] for item in selections
                                            for path in item['required_fact_paths'])),
                'support_article_loss_stages': dict(Counter(path['stage'] for item in selections
                                                            for path in item['support_article_paths']))}
        pairs = {}
        for old, new in (('5', '8'), ('5', '10'), ('8', '10')):
            compared = {}
            for metric in ('article_recall', 'all_support_articles', 'fact_coverage', 'all_facts'):
                values = [(row['depths'][old]['metrics'][metric], row['depths'][new]['metrics'][metric])
                          for row in group]
                known = [(a, b) for a, b in values if a is not None and b is not None]
                compared[metric] = {'improved': sum(b > a for a, b in known),
                                    'regressed': sum(b < a for a, b in known),
                                    'unchanged': sum(b == a for a, b in known),
                                    'not_evaluable': len(values) - len(known)}
            compared['selected_ids_changed'] = sum(row['depths'][old]['selected_ids'] != row['depths'][new]['selected_ids']
                                                   for row in group)
            pairs[f'{old}_to_{new}'] = compared
        summary[mode] = {'row_count': len(group), 'by_depth': by_depth, 'paired': pairs}
    return summary


def render_report(result: dict) -> str:
    lines = ['# D/E 逐轮候选 Top-5/8/10 配对实验', '',
             f"来源：`{result['source_result']}`；SHA-256 `{result['source_result_sha256']}`。",
             '固定原始问题、逐轮查询、每轮 RRF Top-20 候选及其原始 Cross-Encoder 排名；'
             '仅改变逐轮保留深度。最终仍最多 5 条，固定 `reserve_second_hop_one`。',
             f"本地最终重排模型：`{result['reranker_model']}` @ `{result['reranker_revision']}`。",
             '首轮 Planner 输入始终是历史 Top-5；本实验未调用 Planner、Answer LLM、Qdrant 或封存 test。',
             'Top-5 必须逐题复现来源结果，否则命令失败且不会生成成功产物。', '',
             '| 组 | K | 文章 Recall 均值 | 全支持文章 | 事实覆盖均值 | 全事实 | 第二跳新增最终保留/新增 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for mode, group in result['summary'].items():
        for depth, item in group['by_depth'].items():
            def fmt(metric):
                value = item[metric]
                return f"{value['mean']:.3f} (n={value['denominator']})" if value['mean'] is not None else '不可计'
            lines.append(f"| {mode} | {depth} | {fmt('article_recall')} | {fmt('all_support_articles')} | "
                         f"{fmt('fact_coverage')} | {fmt('all_facts')} | "
                         f"{item['second_hop_new_retained_total']}/{item['second_hop_new_total']} |")
    lines += ['', '## 同题配对变化', '',
              '| 组 | K 对比 | 文章 Recall 改善/退化/不变/不可计 | 事实覆盖 改善/退化/不变/不可计 | 最终 ID 变化 |',
              '| --- | --- | ---: | ---: | ---: |']
    for mode, group in result['summary'].items():
        for pair, item in group['paired'].items():
            def counts(metric):
                row = item[metric]
                return '/'.join(str(row[key]) for key in ('improved', 'regressed', 'unchanged', 'not_evaluable'))
            lines.append(f"| {mode} | {pair} | {counts('article_recall')} | {counts('fact_coverage')} | "
                         f"{item['selected_ids_changed']} |")
    lines += ['', '## 候选保留与流失', '',
              '计数按 D/E 分组，`pre_dedup` 是两轮保留数之和；`unique_union` 是按 Chunk ID 去重后的数量。',
              '| 组 | K | 每轮保留总数 | pre_dedup | unique_union | 支持文章未入 RRF/单轮丢失/最终丢失/保留 | 事实未入 RRF/单轮丢失/最终丢失/保留 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for mode, group in result['summary'].items():
        for depth, item in group['by_depth'].items():
            stages = item['loss_stages']
            stage_counts = '/'.join(str(stages.get(key, 0)) for key in
                                    ('not_in_rrf_top20', 'dropped_per_round_rerank',
                                     'dropped_final_merge', 'retained_final'))
            article_stages = item['support_article_loss_stages']
            article_counts = '/'.join(str(article_stages.get(key, 0)) for key in
                                      ('not_in_rrf_top20', 'dropped_per_round_rerank',
                                       'dropped_final_merge', 'retained_final'))
            lines.append(f"| {mode} | {depth} | {item['per_round_retained_total']} | "
                         f"{item['pre_dedup_total']} | {item['unique_union_total']} | "
                         f"{article_counts} | {stage_counts} |")
    lines += ['', '## 逐题证据变化', '',
              '完整的最终 Chunk ID、正文、得分及逐轮排名见同名 JSON；下表只展示逐题指标与最终 ID 是否变化。',
              '| 样本 | 组 | 检索轮数 | 文章 Recall 5/8/10 | 事实覆盖 5/8/10 | 最终 ID 变化 5→8 / 5→10 |',
              '| --- | --- | ---: | --- | --- | --- |']
    for row in result['rows']:
        def values(metric):
            return '/'.join(f"{value:.3f}" if value is not None else '—'
                            for value in (row['depths'][str(depth)]['metrics'][metric] for depth in DEPTHS))
        original = row['depths']['5']['selected_ids']
        changed = ' / '.join('是' if row['depths'][str(depth)]['selected_ids'] != original else '否'
                             for depth in (8, 10))
        lines.append(f"| {row['sample_id']} | {row['mode']} | {len(row['queries'])} | "
                     f"{values('article_recall')} | {values('fact_coverage')} | {changed} |")
    lines += ['', '## 恢复与退化样本', '']
    examples = []
    for row in result['rows']:
        start = row['depths']['5']
        for depth in ('8', '10'):
            end = row['depths'][depth]
            old_metrics, new_metrics = start['metrics'], end['metrics']
            if (old_metrics['article_recall'] is not None
                    and old_metrics['article_recall'] != new_metrics['article_recall']):
                transitions = ', '.join(
                    f"文章 {before['source_document_id']}: {before['stage']}→{after['stage']}"
                    for before, after in zip(start['support_article_paths'], end['support_article_paths'])
                    if before['stage'] != after['stage'])
                examples.append(f"- `{row['sample_id']}` {row['mode']}，Top-5→{depth} 文章 Recall："
                                f"{old_metrics['article_recall']:.3f}→{new_metrics['article_recall']:.3f}；"
                                f"{transitions or '最终选择变化'}。")
            if (old_metrics['fact_coverage'] is not None
                    and old_metrics['fact_coverage'] != new_metrics['fact_coverage']):
                transitions = ', '.join(
                    f"事实 {before['fact_index']}: {before['stage']}→{after['stage']}"
                    for before, after in zip(start['required_fact_paths'], end['required_fact_paths'])
                    if before['stage'] != after['stage'])
                examples.append(f"- `{row['sample_id']}` {row['mode']}，Top-5→{depth} 事实覆盖："
                                f"{old_metrics['fact_coverage']:.3f}→{new_metrics['fact_coverage']:.3f}；"
                                f"{transitions or '最终选择变化'}。")
    lines.extend(examples or ['- 本次没有文章级或可映射事实级的改善、退化样本。'])
    overall = result['summary']['overall']['paired']
    if any(overall[pair][metric]['regressed'] for pair in ('5_to_8', '5_to_10')
           for metric in ('article_recall', 'fact_coverage')):
        verdict = '扩大候选池出现覆盖退化；现阶段保持默认 Top-5，不建议直接切换到 Top-8 或 Top-10。'
    elif any(overall[pair][metric]['improved'] for pair in ('5_to_8', '5_to_10')
             for metric in ('article_recall', 'fact_coverage')):
        verdict = '扩大候选池在部分样本上改善覆盖；仍需检查逐题退化与真实 Answer 效果，暂保持默认 Top-5。'
    else:
        verdict = '扩大候选池未改善本子集的最终覆盖；保持默认 Top-5。'
    lines += ['', '## 判断与限制', '', verdict, '',
              '本文是冻结 dev 子集上的检索与证据选择离线比较；仅 12 道官方 dev 题加 2 道自建无证据题，'
              '不代表完整 Benchmark。文章覆盖与仅对唯一映射事实计算的事实覆盖分开报告。',
              '候选池扩大后可能恢复单轮丢失的证据，也可能仍被最终 Top-5 排除；'
              '逐题 JSON 包含全部排名和流失阶段，不能把进入候选池直接算作最终覆盖。',
              '没有新的 Answer 或引用结果，不能据此声称回答质量改善；Planner 查询保持历史轨迹，'
              '也不能据此声称动态规划能力改善。下一步只在覆盖收益明确且退化可接受时，'
              '另行授权真实 Answer 对照；否则保持默认 Top-5。', '']
    return '\n'.join(lines)


def run_experiment(root: Path, config_path: Path, output: Path, scorer=None) -> dict:
    if output.exists() or output.with_suffix('.md').exists():
        raise FileExistsError('paired depth output exists; choose a new path')
    config = json.loads(config_path.read_text())
    if (config.get('per_round_keep_k_values') != list(DEPTHS) or config.get('candidate_limit') != 20
            or config.get('final_evidence_limit') != 5
            or config.get('final_merge_policy') != 'reserve_second_hop_one'
            or config.get('split') != 'dev' or config.get('modes') != ['D', 'E']
            or config.get('reranker_model') != RERANKER_MODEL
            or config.get('reranker_revision') != RERANKER_REVISION):
        raise ValueError('paired depth configuration differs from frozen retrieval and selection settings')
    source_path = root / config['source_result']
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != config['source_result_sha256']:
        raise ValueError('source result checksum differs from paired depth config')
    source = json.loads(source_bytes)
    source_config = source['config']
    if (source_config.get('split') != 'dev'
            or source_config.get('candidate_limit') != config['candidate_limit']
            or source_config.get('final_evidence_limit') != config['final_evidence_limit']
            or source_config.get('final_merge_policy') != config['final_merge_policy']
            or source_config.get('reranker_model') != config['reranker_model']
            or source_config.get('reranker_revision') != config['reranker_revision']):
        raise ValueError('source result has incompatible frozen settings')
    articles, questions, _ = load_inputs(root, source_config)
    cases = validate_subset(root, source_config, questions, articles)
    by_case = {case['sample_id']: case for case in cases}
    chunks = {article['document_id']: [
        {'chunk_id': f"{article['document_id']}:{n}", 'chunk_text': item['chunk_text']}
        for n, item in enumerate(markdown_chunks(article['body']))]
        for article in articles}
    source_rows = [item for item in source['rows'] if item['mode'] in {'D', 'E'}]
    expected = {(case['sample_id'], mode) for case in cases for mode in ('D', 'E')}
    if {(item['sample_id'], item['mode']) for item in source_rows} != expected or len(source_rows) != len(expected):
        raise ValueError('source result is missing or duplicates frozen D/E cases')
    reranker = scorer or LocalReranker()
    rows = [compare_depth_row(item, by_case[item['sample_id']], chunks, reranker)
            for item in source_rows]
    result = {'kind': 'retrieval_only_paired_per_round_depth',
              'source_result': config['source_result'], 'source_result_sha256': source_hash,
              'config': config, 'no_llm_calls': True, 'no_retrieval_calls': True,
              'reranker_model': RERANKER_MODEL, 'reranker_revision': RERANKER_REVISION,
              'summary': _paired_summary(rows), 'rows': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    output.with_suffix('.md').write_text(render_report(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run_experiment(Path.cwd(), args.config, args.output)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
