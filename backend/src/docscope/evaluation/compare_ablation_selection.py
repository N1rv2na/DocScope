"""Model-free comparison of two DOCS-016 final evidence rules on saved candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from docscope.evaluation.ablation import choose_final_ids, validate_config
from docscope.evaluation.dense_baseline import map_facts, normalized
from docscope.evaluation.run_agent_answer import validate_subset
from docscope.evaluation.run_dense import load_inputs
from docscope.markdown import markdown_chunks


def compare_selection_row(row: dict, case: dict, chunks_by_source: dict[str, list[dict]],
                          limit: int) -> dict:
    """Choose evidence before consulting offline labels, then score both selections."""
    retrievals = row['retrievals']
    if len(retrievals) != 2 or row['mode'] not in {'D', 'E'}:
        raise ValueError('comparison requires saved two-round D/E retrievals')
    ranking = row['final_ranking']
    global_order = [item['chunk_id'] for item in ranking]
    first = set(retrievals[0]['evidence_ids'])
    second = retrievals[1]['evidence_ids']
    if len(global_order) != len(set(global_order)) or set(global_order) != first | set(second):
        raise ValueError('saved scored union differs from selected round evidence')
    selected_by_policy = {}
    for label, policy in (('old', 'original_query_top_k'), ('new', 'reserve_second_hop_one')):
        selected_by_policy[label] = choose_final_ids(global_order, first, second, limit, policy)
    if selected_by_policy['old'][0] != row['final_evidence_ids']:
        raise ValueError('saved old final evidence differs from scored order')

    # Ground truth is used only after both selections have been calculated.
    mapping = map_facts(case['evidence_list'], chunks_by_source)
    relevant = {item['document_id'] for item in case['evidence_list']}
    by_id = {item['chunk_id']: item for item in ranking}
    compared = {}
    for label, (selected_ids, audit) in selected_by_policy.items():
        selected = [by_id[chunk_id] for chunk_id in selected_ids]
        found_sources = {item['source_document_id'] for item in selected} & relevant
        facts_found = None
        facts_total = None
        if mapping['status'] == 'mapped':
            facts_total = len(mapping['facts'])
            facts_found = sum(any(item['source_document_id'] == fact['document_id']
                                  and normalized(fact['fact']) in normalized(item['chunk_text'])
                                  for item in selected)
                              for fact in case['evidence_list'])
        compared[label] = {'selected_ids': selected_ids, 'audit': audit,
                           'source_articles_found': len(found_sources),
                           'all_support_articles': found_sources == relevant if relevant else None,
                           'facts_found': facts_found, 'facts_total': facts_total}
    return {'sample_id': row['sample_id'], 'mode': row['mode'], 'question': row['question'],
            'support_article_count': len(relevant), 'fact_mapping_status': mapping['status'],
            'fact_mapping_failures': mapping.get('failures', []), **compared}


def render_comparison(result: dict) -> str:
    lines = ['# DOCS-016 第二跳预留名额：同候选检索对照', '',
             f"来源：`{result['source_replay']}`；{len(result['rows'])} 条 D/E 重放记录。",
             '旧规则和新规则使用同一批已保存候选及 Cross-Encoder 分数；未重新检索或调用 LLM。',
             '此产物仅评估最终上下文选择，**不包含新答案、引用支持性或答案质量结论**。',
             '', '| 样本 | 组 | 旧/新支持文章 | 旧/新事实片段 | 第二跳新增最终保留 旧→新 | 预留 Chunk |',
             '| --- | --- | --- | --- | --- | --- |']
    for row in result['rows']:
        def facts(label):
            item = row[label]
            return (f"{item['facts_found']}/{item['facts_total']}" if item['facts_found'] is not None
                    else '不可计（事实映射失败）')
        old, new = row['old'], row['new']
        lines.append(f"| {row['sample_id']} | {row['mode']} | "
                     f"{old['source_articles_found']}/{row['support_article_count']} → "
                     f"{new['source_articles_found']}/{row['support_article_count']} | "
                     f"{facts('old')} → {facts('new')} | "
                     f"{old['audit']['second_hop_new_retained_count']} → "
                     f"{new['audit']['second_hop_new_retained_count']} | "
                     f"{new['audit']['reserved_chunk_id'] or '无'} |")
    lines += ['', f"总计：第二跳新增最终保留 {result['summary']['old_new_retained']} → "
              f"{result['summary']['new_new_retained']} / {result['summary']['second_hop_new_count']}。",
              '文章级覆盖与事实片段级覆盖分开报告；事实无法唯一映射到单个 Chunk 的题目不计作片段漏检。',
              '这是已授权本地重放的候选对照，在线 UUID 与原始 v2 不同；不能倒填原始 v2 的分数。',
              '正式回答效果须另行获授权，对同批候选运行两种选择规则并审查答案与引用。', '']
    return '\n'.join(lines)


def run_comparison(root: Path, replay_path: Path, config_path: Path, output: Path) -> dict:
    if output.exists() or output.with_suffix('.md').exists():
        raise FileExistsError('selection comparison exists; choose a new output path')
    replay_bytes = replay_path.read_bytes()
    replay = json.loads(replay_bytes)
    if replay.get('kind') != 'retrieval_only_replay_not_original_run' or not replay.get('no_llm_calls'):
        raise ValueError('input must be a retrieval-only replay')
    if hashlib.sha256((root / replay['source_result']).read_bytes()).hexdigest() != replay['source_result_sha256']:
        raise ValueError('original v2 result hash differs from replay provenance')
    settings = json.loads(config_path.read_text())
    answer = json.loads((root / 'evaluation/configs/agent_answer_dev_v1.json').read_text())
    rerank = json.loads((root / 'evaluation/configs/rerank_dev_v1.json').read_text())
    validate_config(settings, answer, rerank)
    if settings['final_merge_policy'] != 'reserve_second_hop_one':
        raise ValueError('new comparison config must reserve one second-hop Chunk')
    articles, questions, _ = load_inputs(root, settings)
    cases = validate_subset(root, settings, questions, articles)
    by_case = {case['sample_id']: case for case in cases}
    by_source = {article['document_id']: [
        {'chunk_id': f"{article['document_id']}:{n}", 'chunk_text': chunk['chunk_text']}
        for n, chunk in enumerate(markdown_chunks(article['body']))]
        for article in articles}
    rows = []
    seen = set()
    for row in replay['rows']:
        pair = (row['sample_id'], row['mode'])
        if pair in seen or row['sample_id'] not in by_case:
            raise ValueError('replay row is duplicated or outside the frozen dev subset')
        seen.add(pair)
        rows.append(compare_selection_row(row, by_case[row['sample_id']], by_source,
                                          settings['final_evidence_limit']))
    summary = {'second_hop_new_count': sum(row['old']['audit']['second_hop_new_count'] for row in rows),
               'old_new_retained': sum(row['old']['audit']['second_hop_new_retained_count'] for row in rows),
               'new_new_retained': sum(row['new']['audit']['second_hop_new_retained_count'] for row in rows),
               'fact_mapping_status_counts': dict(Counter(row['fact_mapping_status'] for row in rows))}
    result = {'kind': 'retrieval_only_same_candidate_selection_comparison',
              'source_replay': str(replay_path), 'source_replay_sha256': hashlib.sha256(replay_bytes).hexdigest(),
              'config': settings, 'no_llm_calls': True, 'summary': summary, 'rows': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    output.with_suffix('.md').write_text(render_comparison(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('evaluation/configs/ablation_dev_v3.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run_comparison(Path.cwd(), args.replay, args.config, args.output)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
