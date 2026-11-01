"""Offline DOCS-015 metrics; never pass labels into an online Agent request."""

from __future__ import annotations

import re
import unicodedata


ANSWERABLE = frozenset({'inference_query', 'comparison_query', 'temporal_query'})
UNANSWERABLE = frozenset({'null_query', 'self_no_evidence'})


def _tokens(value: str) -> list[str]:
    return re.findall(r'[a-z0-9]+', unicodedata.normalize('NFKC', value).casefold())


def _f1(expected: str, actual: str) -> float:
    from collections import Counter

    gold, prediction = Counter(_tokens(expected)), Counter(_tokens(actual))
    overlap = sum((gold & prediction).values())
    return 2 * overlap / (sum(gold.values()) + sum(prediction.values())) if gold and prediction else 0.0


def score_case(sample_id: str, question_type: str, expected_answer: str | None,
               relevant_document_ids: list[str], response: dict,
               source_document_by_chunk_id: dict[str, str]) -> dict:
    if question_type not in ANSWERABLE | UNANSWERABLE:
        raise ValueError('unsupported question type')
    if (question_type in ANSWERABLE) != bool(relevant_document_ids):
        raise ValueError('answerable cases require supporting articles; unanswerable cases cannot have them')
    status = response['status']
    if status not in {'answered', 'insufficient_evidence', 'error'}:
        raise ValueError('invalid Agent result status')
    retrieved_ids = set(response['retrieved_evidence_ids'])
    retrieved_documents = {source_document_by_chunk_id[item] for item in retrieved_ids
                           if item in source_document_by_chunk_id}
    relevant = set(relevant_document_ids)
    citation_ids = [item['chunk_id'] for item in response['citations']]
    answer = response.get('answer')
    return {
        'sample_id': sample_id, 'question_type': question_type, 'status': status,
        'appropriate_abstention': status == 'insufficient_evidence' if question_type in UNANSWERABLE else None,
        'false_abstention': status == 'insufficient_evidence' if question_type in ANSWERABLE else None,
        'article_recall': len(retrieved_documents & relevant) / len(relevant) if relevant else None,
        'all_support_articles_retrieved': relevant <= retrieved_documents if relevant else None,
        'exact_match': _tokens(answer) == _tokens(expected_answer) if status == 'answered' and expected_answer is not None else None,
        'token_f1': _f1(expected_answer, answer) if status == 'answered' and expected_answer is not None else None,
        'citation_id_legal': [item in retrieved_ids for item in citation_ids],
    }


def _ratio(values: list[bool]) -> dict:
    count = sum(values)
    return {'numerator': count, 'denominator': len(values),
            'rate': count / len(values) if values else None}


def summarize(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row['question_type'] in ANSWERABLE]
    unanswerable = [row for row in rows if row['question_type'] in UNANSWERABLE]
    legal = [flag for row in rows for flag in row['citation_id_legal']]
    retrieved = [row['all_support_articles_retrieved'] for row in answerable]
    return {
        'fixed_subset_count': len(rows),
        'answerable_count': len(answerable), 'unanswerable_count': len(unanswerable),
        'error_count': sum(row['status'] == 'error' for row in rows),
        'appropriate_abstention': _ratio([row['appropriate_abstention'] for row in unanswerable]),
        'false_abstention': _ratio([row['false_abstention'] for row in answerable]),
        'retrieval_all_articles': _ratio(retrieved),
        'citation_id_legal': _ratio(legal),
        'exact_match': _ratio([row['exact_match'] for row in answerable if row['exact_match'] is not None]),
        'mean_token_f1': (sum(row['token_f1'] for row in answerable if row['token_f1'] is not None)
                          / sum(row['token_f1'] is not None for row in answerable)
                          if any(row['token_f1'] is not None for row in answerable) else None),
    }


def review_support(citations: list[dict]) -> dict:
    statuses = [item['support'] for item in citations]
    if any(status not in {'supported', 'unsupported', 'uncertain', 'pending'} for status in statuses):
        raise ValueError('invalid citation support review status')
    pending = sum(status in {'pending', 'uncertain'} for status in statuses)
    numerator = statuses.count('supported')
    return {'numerator': numerator, 'denominator': len(statuses), 'pending': pending,
            'rate': numerator / len(statuses) if statuses and not pending else None}
