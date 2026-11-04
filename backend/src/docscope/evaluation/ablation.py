"""Frozen DOCS-016 comparison rules; online models never receive labels."""

from __future__ import annotations

import json
import statistics
from collections import Counter

from docscope.agent_run import MAX_MODEL_ATTEMPTS, MAX_RETRIEVAL_ROUNDS
from docscope.embedding import EMBEDDING_MODEL, EMBEDDING_REVISION
from docscope.llm import MAX_MODEL_INPUT_BYTES
from docscope.reranking import RERANKER_MODEL, RERANKER_REVISION, rank_points


MODE_NAMES = ('A', 'B', 'C', 'D', 'E')
ONE_SHOT_INSTRUCTIONS = (
    'Given only the user question, propose one additional retrieval query for a second search. '
    'The first search will use the original question. Do not assume you have seen any retrieved evidence. '
    'Return only a JSON object with exactly one key: second_query. '
    'The value must be a nonempty query distinct from the original question, at most 500 characters. '
    'Do not return an answer or citations.'
)


def validate_config(settings: dict, answer_settings: dict, rerank_settings: dict) -> None:
    if (settings.get('split') != 'dev' or settings.get('answer_subset_ids') != answer_settings['answer_subset_ids']
            or settings.get('self_no_evidence') != answer_settings['self_no_evidence']
            or settings.get('manifest') != answer_settings['manifest']
            or settings.get('dataset_config') != answer_settings['dataset_config']):
        raise ValueError('ablation subset must equal the frozen DOCS-015 dev subset')
    if settings.get('modes') != list(MODE_NAMES):
        raise ValueError('ablation must include exactly A–E')
    if (settings.get('embedding_model'), settings.get('embedding_revision')) != (EMBEDDING_MODEL, EMBEDDING_REVISION):
        raise ValueError('embedding model differs from frozen implementation')
    if (settings.get('reranker_model'), settings.get('reranker_revision')) != (RERANKER_MODEL, RERANKER_REVISION):
        raise ValueError('reranker model differs from frozen implementation')
    for field in ('qdrant_version', 'bm25_field', 'bm25_tokenizer', 'bm25_lowercase', 'fusion',
                  'candidate_limit', 'rerank_limit'):
        if settings.get(field) != rerank_settings.get(field):
            raise ValueError(f'{field} differs from frozen C retrieval configuration')
    if settings.get('final_evidence_limit') != 5 or settings.get('candidate_limit') != 20 or settings.get('rerank_limit') != 20:
        raise ValueError('retrieval and evidence budgets must be frozen at 5/20/20')
    if settings.get('final_merge_policy', 'original_query_top_k') not in {
            'original_query_top_k', 'reserve_second_hop_one'}:
        raise ValueError('invalid final merge policy')
    if settings.get('max_retrieval_rounds') != MAX_RETRIEVAL_ROUNDS:
        raise ValueError('D/E retrieval budget differs from the Agent')
    if settings.get('max_model_attempts') != MAX_MODEL_ATTEMPTS:
        raise ValueError('D/E model budget differs from the Agent')
    if (settings.get('requested_model') != answer_settings['requested_model']
            or settings.get('declared_route_members') != answer_settings['declared_route_members']
            or settings.get('route_members_source') != answer_settings['route_members_source']):
        raise ValueError('LLM route group differs from DOCS-015 frozen configuration')
    if ((settings.get('answer_max_tokens'), settings.get('planning_max_tokens')) not in {(400, 2048), (2048, 4096)}
            or settings.get('llm_response_format') != 'json_object'
            or settings.get('llm_temperature') != 'provider_default_unset'
            or settings.get('llm_max_input_bytes') != MAX_MODEL_INPUT_BYTES):
        raise ValueError('LLM request parameters differ from current responder')


def plan_queries(question: str, complete_json, max_tokens: int = 2048) -> list[str]:
    """D plans once from the original question; no retrieval has happened yet."""
    proposal = complete_json('plan_once', ONE_SHOT_INSTRUCTIONS,
                             json.dumps({'question': question}), max_tokens)
    if not isinstance(proposal, dict) or set(proposal) != {'second_query'}:
        raise ValueError('one-shot planner must return exactly second_query')
    second = proposal['second_query']
    if (not isinstance(second, str) or not second.strip() or len(second) > 500
            or ' '.join(second.casefold().split()) == ' '.join(question.casefold().split())):
        raise ValueError('one-shot second query must be bounded and distinct')
    return [question, second.strip()]


def rank_final_context(question: str, points: list[dict], scorer) -> list[dict]:
    """Score the deduplicated D/E union once, retaining discarded points for audit."""
    unique = list({point['payload']['chunk_id']: point for point in points}.values())
    if len(unique) > 20:
        raise ValueError('final D/E candidate union exceeds the frozen limit')
    return rank_points(question, unique, scorer)


def choose_final_ids(global_order: list[str], first_ids: set[str], second_order: list[str],
                     limit: int, policy: str) -> tuple[list[str], dict]:
    """Apply a final evidence rule to one already scored, deduplicated union."""
    if not 1 <= limit <= 5 or policy not in {'original_query_top_k', 'reserve_second_hop_one'}:
        raise ValueError('invalid final merge policy or evidence limit')
    novel = list(dict.fromkeys(chunk_id for chunk_id in second_order if chunk_id not in first_ids))
    reserved = novel[0] if novel and policy == 'reserve_second_hop_one' else None
    if reserved is not None and reserved not in global_order:
        raise ValueError('reserved Chunk is absent from the scored union')
    chosen = {reserved} if reserved else set()
    for chunk_id in global_order:
        if len(chosen) >= limit:
            break
        chosen.add(chunk_id)
    selected = [chunk_id for chunk_id in global_order if chunk_id in chosen]
    return selected, {'policy': policy, 'reserved_chunk_id': reserved,
                      'reserved_round_rank': second_order.index(reserved) + 1 if reserved else None,
                      'reserved_global_rank': global_order.index(reserved) + 1 if reserved else None,
                      'second_hop_new_count': len(novel),
                      'second_hop_new_retained_count': len(set(novel) & chosen)}


def final_context(question: str, points: list[dict], scorer, limit: int) -> list[dict]:
    """D/E rerank the deduplicated union into the same final context size."""
    if not 1 <= limit <= 10:
        raise ValueError('invalid final context limit')
    return rank_final_context(question, points, scorer)[:limit]


def _ratio(flags: list[bool]) -> dict:
    return {'numerator': sum(flags), 'denominator': len(flags),
            'rate': sum(flags) / len(flags) if flags else None}


def _mode_summary(rows: list[dict]) -> dict:
    answerable = [item for item in rows if item['question_type'] not in {'null_query', 'self_no_evidence'}]
    unanswerable = [item for item in rows if item['question_type'] in {'null_query', 'self_no_evidence'}]
    calls = [call for item in rows for call in item['llm_calls']]
    durations = [item['elapsed_seconds'] for item in rows if item.get('elapsed_seconds') is not None]
    actual_models = Counter(call.get('actual_model') or 'unknown' for call in calls)
    actual_channels = Counter(call.get('actual_channel') or 'unknown' for call in calls)
    known_costs = [call.get('billed_cost_cny') for call in calls]
    usage = [call.get('usage') for call in calls]
    return {
        'question_count': len(rows),
        'status_counts': dict(Counter(item['status'] for item in rows)),
        'answer_exact_match': _ratio([item['metrics'].get('exact_match') is True for item in answerable]),
        'appropriate_abstention': _ratio([item['status'] == 'insufficient_evidence' for item in unanswerable]),
        'false_abstention': _ratio([item['status'] == 'insufficient_evidence' for item in answerable]),
        'all_support_articles_retrieved': _ratio([item['metrics'].get('all_support_articles_retrieved') is True for item in answerable]),
        'all_mapped_facts_in_context': _ratio([item['all_mapped_facts_in_context'] for item in answerable
                                              if item.get('all_mapped_facts_in_context') is not None]),
        'citation_id_legal': _ratio([flag for item in rows for flag in item['metrics']['citation_id_legal']]),
        'llm_call_count': len(calls),
        'retrieval_call_count': sum(item['calls']['retrieval'] for item in rows),
        'candidate_count': sum(item['candidate_count'] for item in rows),
        'final_evidence_count': sum(item['final_evidence_count'] for item in rows),
        'latency_seconds_mean': statistics.mean(durations) if durations else None,
        'latency_seconds_median': statistics.median(durations) if durations else None,
        'prompt_tokens_known': sum((item or {}).get('prompt_tokens') or 0 for item in usage),
        'completion_tokens_known': sum((item or {}).get('completion_tokens') or 0 for item in usage),
        'usage_available_calls': sum(item is not None for item in usage),
        'usage_unavailable_calls': sum(item is None for item in usage),
        'billed_cost_cny': sum(known_costs) if known_costs and all(item is not None for item in known_costs) else None,
        'actual_models': dict(actual_models), 'actual_channels': dict(actual_channels),
    }


def summarize_comparison(rows: list[dict], sample_ids: list[str]) -> dict:
    actual = {mode: _mode_summary([item for item in rows if item['mode'] == mode])
              for mode in MODE_NAMES if any(item['mode'] == mode for item in rows)}
    paired = {(item['sample_id'], item['mode']): item for item in rows}
    matched_ids = []
    for sample_id in sample_ids:
        d, e = paired.get((sample_id, 'D')), paired.get((sample_id, 'E'))
        if (d and e and d['status'] != 'error' and e['status'] != 'error'
                and d['calls'] == e['calls'] and d['candidate_count'] == e['candidate_count']
                and d['final_evidence_count'] == e['final_evidence_count']):
            matched_ids.append(sample_id)
    matched = {mode: _mode_summary([paired[(sample_id, mode)] for sample_id in matched_ids])
               for mode in ('D', 'E')}
    calls = [call for item in rows for call in item['llm_calls']]
    actual_channels = {call.get('actual_channel') for call in calls}
    actual_models = {call.get('actual_model') for call in calls}
    return {'actual': actual,
            'matched_de': {'sample_ids': matched_ids, 'pair_count': len(matched_ids),
                           'excluded_sample_ids': [item for item in sample_ids if item not in matched_ids],
                           'D': matched['D'], 'E': matched['E'],
                           'rule': 'both terminal runs non-error, exact observed retrieval/model calls, candidate count and final evidence count'},
            'strict_same_channel_control': bool(calls and None not in actual_channels and
                                                None not in actual_models and
                                                len(actual_channels) == len(actual_models) == 1),
            'channel_identifiability': 'known' if calls and all(call.get('actual_channel') for call in calls) else 'unknown_or_partial'}


def render_report(result: dict) -> str:
    """Render only measured values; an unknown bill or route stays unknown."""
    summary = result['summary']
    actual = summary['actual']
    matched = summary['matched_de']
    rows = result['rows']

    def fraction(metric: dict) -> str:
        return f"{metric['numerator']}/{metric['denominator']}"

    def number(value) -> str:
        return '未知' if value is None else str(round(value, 3))

    lines = [
        '# DOCS-016 A–E 固定 dev 子集消融报告',
        '',
        f"本轮使用同一 {result['corpus_count']} 篇文章和 {result['sample_count']} 道固定子集题；"
        '结果仅针对该子集，**非全量 Benchmark**，封存 test 未参与。',
        f"运行窗口 UTC：{result['run_window_utc']['start']} — {result['run_window_utc']['end']}。",
        f"请求路由：`{result['config']['requested_model']}`；组成员来自所有者声明，运行时未验证组配置。",
        f"D/E 最终合并策略：`{result['config'].get('final_merge_policy', 'original_query_top_k')}`；"
        '第二跳预留仅影响 D/E 的最终证据选择。',
        f"离线评测调用上限：回答 {result['config']['answer_max_tokens']} Tokens，"
        f"规划 {result['config']['planning_max_tokens']} Tokens；五组共享该配置。"
        '若与线上默认值不同，本报告仅代表离线配置。',
        '',
        '## 实际运行配置与结果',
        '',
        'A=Dense；B=Hybrid RRF；C=Hybrid+Cross-Encoder；D=C+问题阶段一次规划；E=C+证据驱动迭代。',
        '所有组使用同一文章切分、Embedding、LLM 请求配置及最终最多 5 条证据。'
        'D/E 各最多两轮检索、两次模型调用，单轮检索最多 20 个待重排候选。',
        '',
        '| 组 | 题数 | 答案 EM/可回答题 | 适当拒答 | 错误拒答 | 全部支持文章 | 全部映射事实 | 引用 ID 合法 | 错误 | 检索/LLM 调用 | 平均延迟 s | 已知输入/输出 Token | 账单 CNY |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for mode in MODE_NAMES:
        if mode not in actual:
            continue
        data = actual[mode]
        lines.append(f"| {mode} | {data['question_count']} | {fraction(data['answer_exact_match'])} | "
                     f"{fraction(data['appropriate_abstention'])} | {fraction(data['false_abstention'])} | "
                     f"{fraction(data['all_support_articles_retrieved'])} | "
                     f"{fraction(data['all_mapped_facts_in_context'])} | {fraction(data['citation_id_legal'])} | "
                     f"{data['status_counts'].get('error', 0)} | {data['retrieval_call_count']}/{data['llm_call_count']} | "
                     f"{number(data['latency_seconds_mean'])} | "
                     f"{data['prompt_tokens_known']}/{data['completion_tokens_known']} | {number(data['billed_cost_cny'])} |")
    lines += [
        '',
        '答案 EM 的分母包含所有可回答题；错误和拒答计为未命中。`null_query` 与自建无证据题不进入支持文章/事实分母。'
        '事实映射失败单列，不作为事实检索漏检。答案 EM 是严格字符串匹配，长答案即使语义正确也可能未命中；'
        '不能把 EM 当作人工事实准确率。Token 合计仅覆盖网关报告用量的调用；未知账单不等于零费用。',
        '',
        '## D/E 相近预算对照',
        '',
        f"两组采用相同**最大**预算：每题最多 {result['config']['max_retrieval_rounds']} 轮检索、"
        f"{result['config']['max_model_attempts']} 次模型调用，"
        f"每轮最多 {result['config']['candidate_limit']} 个重排候选，"
        f"最终最多 {result['config']['final_evidence_limit']} 条证据。",
        f"实际预算完全匹配且两组均非错误的题目：{matched['pair_count']}/{result['sample_count']}。"
        '匹配规则要求检索/模型调用数、已得候选数和最终证据数均相同。',
        f"匹配题 ID：{', '.join(matched['sample_ids']) if matched['sample_ids'] else '无'}。",
        f"匹配题答案 EM：D {fraction(matched['D']['answer_exact_match'])}，"
        f"E {fraction(matched['E']['answer_exact_match'])}。样本量不足时不推断动态策略净增益。",
        '',
        '| 样本 ID | D 检索/模型/候选/证据 | E 检索/模型/候选/证据 | 匹配 |',
        '| --- | --- | --- | --- |',
    ]
    by_id = {(row['sample_id'], row['mode']): row for row in rows}
    for sample_id in result['config']['answer_subset_ids'] + [item['sample_id'] for item in result['config']['self_no_evidence']]:
        d, e = by_id.get((sample_id, 'D')), by_id.get((sample_id, 'E'))
        if not d or not e:
            continue
        budget = lambda row: f"{row['calls']['retrieval']}/{row['calls']['model']}/{row['candidate_count']}/{row['final_evidence_count']}"
        lines.append(f"| {sample_id} | {budget(d)} | {budget(e)} | {'是' if sample_id in matched['sample_ids'] else '否'} |")
    lines += ['', '## 路由、费用与失败分析', '']
    for mode in MODE_NAMES:
        if mode not in actual:
            continue
        data = actual[mode]
        lines.append(f"- {mode}：实际响应模型 {json.dumps(data['actual_models'], ensure_ascii=False)}；"
                     f"实际渠道 {json.dumps(data['actual_channels'], ensure_ascii=False)}；"
                     f"用量可得 {data['usage_available_calls']}/{data['llm_call_count']} 次。")
    if not summary['strict_same_channel_control']:
        lines.append('- 渠道未知或实际模型/渠道未全程一致，**不能宣称严格同渠道控制的质量差异**。')
    else:
        lines.append('- 记录内的实际模型和渠道一致；仍需结合固定子集规模解释观察差异。')
    errors = [row for row in rows if row['status'] == 'error']
    lines.append(f'- 错误题次：{len(errors)}/{len(rows)}。')
    if 'A' in actual:
        baseline = actual['A']['answer_exact_match']['numerator']
        denominator = actual['A']['answer_exact_match']['denominator']
        changes = [f"{mode} {actual[mode]['answer_exact_match']['numerator'] - baseline:+d}/{denominator}"
                   for mode in ('B', 'C', 'D', 'E') if mode in actual]
        lines.append('- 相对 A 的答案 EM 命中数变化：' + '；'.join(changes) + '。'
                     '仅是固定子集观察值，不能据此声称严格同渠道的因果效果。')
    all_calls = [call for row in rows for call in row['llm_calls']]
    truncated = sum(call.get('finish_reason') == 'length' for call in all_calls)
    error_types = Counter(call.get('error_type') for call in all_calls if call.get('error_type'))
    lines.append(f'- 模型输出截断：{truncated}/{len(all_calls)} 次；请求异常类型：'
                 f'{json.dumps(dict(error_types), ensure_ascii=False)}。错误题次保留在全部题目分母中。')
    lines.append('- 全部映射事实在最终上下文中的覆盖率见上表；覆盖不足可能限制回答质量，'
                 '但该指标不能单独定位每道题的失败原因。')
    for row in errors:
        lines.append(f"  - {row['sample_id']}/{row['mode']}：{row['run'].get('error_code', 'unknown')}。")
    lines += [
        '',
        '逐题 JSON 保留每组查询轨迹、来源 Chunk、引用、预算、耗时、实际响应模型与已知 Token。'
        '本报告只陈述观察结果；未测得提升时保留退化或无差异，不能据此宣称 Agent 优于基线。',
        '',
    ]
    return '\n'.join(lines)
