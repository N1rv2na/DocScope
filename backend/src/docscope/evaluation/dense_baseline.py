"""Offline Dense baseline scoring. Labels never cross into the online index."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict


def normalized(text: str) -> str:
    return " ".join(re.split(r"\s+", unicodedata.normalize("NFKC", text).casefold())).strip()


def map_facts(evidence: list[dict], chunks_by_document: dict[str, list[dict]]) -> dict:
    if not evidence:
        return {"status": "null_query", "facts": []}
    mapped = []
    failures = []
    for item in evidence:
        candidates = [chunk["chunk_id"] for chunk in chunks_by_document.get(item["document_id"], [])
                      if normalized(item["fact"]) in normalized(chunk["chunk_text"])]
        if len(candidates) == 1:
            mapped.append({"document_id": item["document_id"], "chunk_id": candidates[0]})
        else:
            failures.append({"document_id": item["document_id"], "reason": "no_chunk" if not candidates else "ambiguous_chunks"})
    return {"status": "mapped" if not failures else "unmapped", "facts": mapped, "failures": failures}


def score_retrieval(retrieved: list[dict], mapping: dict, relevant_document_ids: list[str], k: int) -> dict:
    if not relevant_document_ids:
        return {"article_recall": None, "all_articles": None, "fact_coverage": None, "all_facts": None}
    top = retrieved[:k]
    found_documents = {item["document_id"] for item in top}
    relevant = set(relevant_document_ids)
    articles = len(found_documents & relevant)
    result = {"article_recall": articles / len(relevant), "all_articles": articles == len(relevant),
              "fact_coverage": None, "all_facts": None}
    if mapping["status"] == "mapped":
        facts = mapping["facts"]
        found_chunks = {(item["document_id"], item["chunk_id"]) for item in top}
        hits = sum((item["document_id"], item["chunk_id"]) in found_chunks for item in facts)
        result.update(fact_coverage=hits / len(facts), all_facts=hits == len(facts))
    return result


def fixed_subset(rows: list[dict], per_type: int) -> list[str]:
    by_type = defaultdict(list)
    for row in rows:
        by_type[row["question_type"]].append(row["sample_id"])
    return sorted(sample_id for kind in sorted(by_type) for sample_id in sorted(
        by_type[kind], key=lambda value: hashlib.sha256(value.encode()).hexdigest())[:per_type])
