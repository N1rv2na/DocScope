"""DOCS-009 paired Dense/Hybrid retrieval evaluation on the frozen dev split."""

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

from fastapi.testclient import TestClient

from docscope.api import create_app
from docscope.embedding import EMBEDDING_MODEL, EMBEDDING_REVISION, LocalEmbedder
from docscope.evaluation.dense_baseline import map_facts, score_retrieval
from docscope.evaluation.run_dense import EvaluationIndex, load_inputs, scroll_chunks
from docscope.reranking import MAX_RERANK_CANDIDATES, RERANKER_MODEL, RERANKER_REVISION, rerank_points


METRIC_FIELDS = ("article_recall", "all_articles", "fact_coverage", "all_facts")


def summarize(rows: list[dict], ks: list[int]) -> dict:
    result = {"question_count": len(rows),
              "mapping_counts": dict(Counter(row["mapping_status"] for row in rows))}
    modes = ("dense", "hybrid", "rerank") if rows and "rerank" in rows[0] else ("dense", "hybrid")
    for mode in modes:
        values = [row[mode]["retrieval_seconds"] for row in rows]
        ordered = sorted(values)
        result[mode] = {
            "retrieval_seconds_mean": sum(values) / len(values) if values else None,
            "retrieval_seconds_median": ordered[len(ordered) // 2] if ordered else None,
        }
        if mode == "rerank":
            result[mode]["rerank_seconds_mean"] = sum(row[mode]["rerank_seconds"] for row in rows) / len(rows)
        for k in ks:
            for field in METRIC_FIELDS:
                scored = [row[mode]["metrics"][str(k)][field] for row in rows
                          if row[mode]["metrics"][str(k)][field] is not None]
                result[mode][f"{field}@{k}"] = {
                    "mean": sum(scored) / len(scored) if scored else None,
                    "denominator": len(scored),
                }
    return result


def run(root: Path, settings: dict, output: Path, reranker=None) -> dict:
    if settings["split"] != "dev":
        raise ValueError("DOCS-009 selects parameters on dev only; test is sealed")
    if (settings["embedding_model"], settings["embedding_revision"]) != (EMBEDDING_MODEL, EMBEDDING_REVISION):
        raise ValueError("embedding configuration differs from implementation")
    if (settings["bm25_field"], settings["bm25_tokenizer"], settings["bm25_lowercase"], settings["fusion"]) != (
        "chunk_text", "word", True, "qdrant_rrf"
    ):
        raise ValueError("BM25/RRF configuration differs from implementation")
    ks = settings["retrieval_k"]
    candidate_limit = settings["candidate_limit"]
    if (not isinstance(ks, list) or not ks or any(type(k) is not int or k < 1 or k > 10 for k in ks)
            or type(candidate_limit) is not int or not max(ks) <= candidate_limit <= 50):
        raise ValueError("invalid retrieval limits")
    rerank_limit = None
    if reranker is not None:
        if (settings.get("reranker_model"), settings.get("reranker_revision")) != (
            RERANKER_MODEL, RERANKER_REVISION
        ):
            raise ValueError("reranker configuration differs from implementation")
        rerank_limit = settings.get("rerank_limit")
        if (type(rerank_limit) is not int or not max(ks) <= rerank_limit <= MAX_RERANK_CANDIDATES
                or candidate_limit < rerank_limit):
            raise ValueError("invalid rerank candidate limit")
    articles, questions, question_ids = load_inputs(root, settings)
    if len(questions) != len(question_ids):
        raise ValueError("frozen dev question count differs from manifest")
    index = EvaluationIndex(os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
                            f"docscope_hybrid_eval_{uuid.uuid4().hex}")
    if index._request("GET", "/")["version"] != settings["qdrant_version"]:
        raise ValueError("Qdrant version differs from configuration")
    rows: list[dict] = []
    current_sample_id = None
    started = time.monotonic()
    run_error = None
    try:
        embedder = LocalEmbedder()
        with tempfile.TemporaryDirectory() as data_dir, TestClient(
            create_app(Path(data_dir), embedder, index)
        ) as client:
            online_by_source = {}
            chunks_by_document = {}
            source_chunk_by_online = {}
            for position, article in enumerate(articles, 1):
                filename = f"{article['document_id']}.md"
                response = client.post("/api/documents", files={
                    "file": (filename, article["body"].encode("utf-8"), "text/markdown")})
                if response.status_code != 201 or response.json().get("status") != "ready":
                    raise RuntimeError(f"corpus ingestion failed at article {position}: HTTP {response.status_code}")
                online_id = response.json()["document_id"]
                online_by_source[article["document_id"]] = online_id
                chunks_by_document[online_id] = scroll_chunks(index, online_id)
                for chunk in chunks_by_document[online_id]:
                    source_chunk_by_online[chunk["chunk_id"]] = hashlib.sha256(
                        (article["document_id"] + "\0" + chunk["chunk_text"]).encode()).hexdigest()[:20]
                if position % 50 == 0:
                    print(f"indexed {position}/{len(articles)} articles", flush=True)
            source_by_online = {value: key for key, value in online_by_source.items()}
            for position, question in enumerate(questions, 1):
                current_sample_id = question["sample_id"]
                evidence = [{"document_id": online_by_source[item["document_id"]], "fact": item["fact"]}
                            for item in question["evidence_list"]]
                mapping = map_facts(evidence, chunks_by_document)
                start = time.monotonic()
                vector = embedder.encode([question["query"]])[0]
                embedding_seconds = time.monotonic() - start
                row = {"sample_id": current_sample_id, "question_type": question["question_type"],
                       "mapping_status": mapping["status"],
                       "mapped_facts": [{"source_document_id": source_by_online[item["document_id"]],
                                         "source_chunk_id": source_chunk_by_online[item["chunk_id"]]}
                                        for item in mapping["facts"]],
                       "mapping_failures": [{"source_document_id": source_by_online[item["document_id"]],
                                             "reason": item["reason"]}
                                            for item in mapping.get("failures", [])],
                       "embedding_seconds": round(embedding_seconds, 4)}
                query_order = ("dense", "hybrid") if position % 2 else ("hybrid", "dense")
                row["query_order"] = list(query_order)
                hybrid_points = []
                hybrid_seconds = 0.0
                for mode in query_order:
                    query_start = time.monotonic()
                    points = (index.query(vector, None, max(ks)) if mode == "dense"
                              else index.query_hybrid(vector, question["query"], None,
                                                      rerank_limit if rerank_limit is not None else max(ks), candidate_limit))
                    seconds = embedding_seconds + time.monotonic() - query_start
                    retrieved = [{"document_id": point["payload"]["document_id"],
                                  "chunk_id": point["payload"]["chunk_id"]} for point in points[:max(ks)]]
                    row[mode] = {
                        "retrieval_seconds": round(seconds, 4),
                        "retrieved": [{"source_document_id": source_by_online[item["document_id"]],
                                       "source_chunk_id": source_chunk_by_online[item["chunk_id"]]}
                                      for item in retrieved],
                        "metrics": {str(k): score_retrieval(retrieved, mapping,
                            [item["document_id"] for item in evidence], k) for k in ks},
                    }
                    if mode == "hybrid":
                        hybrid_points, hybrid_seconds = points, seconds
                if reranker is not None:
                    rerank_start = time.monotonic()
                    ranked_points = rerank_points(question["query"], hybrid_points, reranker, max(ks))
                    rerank_seconds = time.monotonic() - rerank_start
                    ranked = [{"document_id": point["payload"]["document_id"],
                               "chunk_id": point["payload"]["chunk_id"]} for point in ranked_points]
                    row["rerank"] = {
                        "retrieval_seconds": round(hybrid_seconds + rerank_seconds, 4),
                        "rerank_seconds": round(rerank_seconds, 4),
                        "candidate_count": len(hybrid_points),
                        "retrieved": [{"source_document_id": source_by_online[point["payload"]["document_id"]],
                                       "source_chunk_id": source_chunk_by_online[point["payload"]["chunk_id"]],
                                       "rrf_rank": point["rrf_rank"], "rrf_score": point["rrf_score"],
                                       "rerank_score": point["rerank_score"]} for point in ranked_points],
                        "metrics": {str(k): score_retrieval(ranked, mapping,
                            [item["document_id"] for item in evidence], k) for k in ks},
                    }
                rows.append(row)
                if position % 100 == 0:
                    print(f"retrieved {position}/{len(questions)} dev questions", flush=True)
    except Exception as error:
        run_error = error
    finally:
        try:
            index._request("DELETE", f"/collections/{index.collection}")
        except Exception as error:
            if run_error is None:
                run_error = RuntimeError(f"Qdrant collection cleanup failed: {type(error).__name__}")
    if run_error is not None:
        failure = {"status": "failed", "error_type": type(run_error).__name__, "error": str(run_error),
                   "sample_id": current_sample_id, "completed_questions": len(rows), "config": settings, "rows": rows}
        path = output.with_name(output.stem + ".failed.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
        raise RuntimeError(f"Hybrid dev evaluation failed at {current_sample_id}: {run_error}") from run_error
    result = {"config": settings, "config_sha256": hashlib.sha256(
        json.dumps(settings, sort_keys=True).encode()).hexdigest(),
        "question_ids": question_ids, "corpus_count": len(articles),
        "summary": summarize(rows, ks), "elapsed_seconds": round(time.monotonic() - started, 3),
        "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("evaluation/configs/hybrid_dev_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/hybrid_dev_v1.json"))
    args = parser.parse_args()
    result = run(Path.cwd(), json.loads(args.config.read_text()), args.output)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
