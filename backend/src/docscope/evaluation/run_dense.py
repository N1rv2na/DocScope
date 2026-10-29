"""Reproducible DOCS-008 Dense baseline over the pinned MultiHop-RAG corpus."""

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

from docscope.api import create_app
from docscope.embedding import LocalEmbedder, EMBEDDING_MODEL, EMBEDDING_REVISION
from docscope.evaluation.dense_baseline import fixed_subset, map_facts, score_retrieval
from docscope.evaluation.multihop import adapt_corpus, adapt_questions, build_manifest
from docscope.qdrant_index import QdrantIndex, QdrantError


class EvaluationIndex(QdrantIndex):
    """Retry brief local transport failures during long offline runs only."""

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(4):
            try:
                return super()._request(method, path, body)
            except QdrantError as error:
                if error.status_code is not None or attempt == 3:
                    raise
                time.sleep(attempt + 1)
        raise AssertionError("unreachable")


def load_inputs(root: Path, settings: dict) -> tuple[list[dict], list[dict], list[str]]:
    config = json.loads((root / settings["dataset_config"]).read_text())
    corpus = root / "evaluation/datasets/raw/corpus.json"
    questions = root / "evaluation/datasets/raw/MultiHopRAG.json"
    manifest = json.loads((root / settings["manifest"]).read_text())
    if build_manifest(corpus, questions, config) != manifest:
        raise ValueError("frozen dataset manifest differs from source bytes")
    articles = adapt_corpus(json.loads(corpus.read_text()), config["source_revision"])
    offline = adapt_questions(json.loads(questions.read_text()), config["source_revision"],
                              {article["url"]: article["document_id"] for article in articles})
    selected = set(manifest["dev_ids"] + manifest["test_ids"]) if settings["split"] == "all" else set(manifest[settings["split"] + "_ids"])
    return articles, sorted((row for row in offline if row["sample_id"] in selected), key=lambda row: row["sample_id"]), sorted(selected)


def scroll_chunks(index: QdrantIndex, document_id: str) -> list[dict]:
    chunks = []
    offset = None
    while True:
        body = {"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
                "limit": 256, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = index._request("POST", f"/collections/{index.collection}/points/scroll", body)["result"]
        chunks.extend({"chunk_id": point["payload"]["chunk_id"], "chunk_text": point["payload"]["chunk_text"]}
                      for point in result["points"])
        offset = result.get("next_page_offset")
        if offset is None:
            return chunks


def offline_dense_query(embedder: LocalEmbedder, index: QdrantIndex, question: str,
                        ready_ids: list[str] | None, limit: int) -> list[dict]:
    """Use the same local encoder and Qdrant Dense query for frozen long questions."""
    points = index.query(embedder.encode([question])[0], ready_ids, limit)
    return [{"document_id": point["payload"]["document_id"],
             "chunk_id": point["payload"]["chunk_id"], "rank": rank}
            for rank, point in enumerate(points, 1)]


def run(root: Path, settings: dict, output: Path) -> dict:
    articles, questions, question_ids = load_inputs(root, settings)
    if len(questions) != len(question_ids):
        raise ValueError("missing question from frozen split")
    if settings["embedding_model"] != EMBEDDING_MODEL or settings["embedding_revision"] != EMBEDDING_REVISION:
        raise ValueError("embedding configuration differs from implementation")
    manifest = json.loads((root / settings["manifest"]).read_text())
    subset = set(fixed_subset([row for row in questions if row["sample_id"] in manifest["dev_ids"]], settings["answer_subset_per_type"]))
    if sorted(subset) != settings["answer_subset_ids"]:
        raise ValueError("fixed answer subset differs from frozen IDs")
    if not os.getenv("LLM_BASE_URL") or not os.getenv("LLM_MODEL") or not os.getenv("LLM_API_KEY"):
        raise ValueError("LLM configuration required for the frozen answer subset")
    effective_llm = {"base_url": os.getenv("LLM_BASE_URL"), "requested_model": os.getenv("LLM_MODEL")}
    run_config_sha256 = hashlib.sha256(json.dumps({"settings": settings, "effective_llm": effective_llm}, sort_keys=True).encode()).hexdigest()
    index = EvaluationIndex(os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), f"docscope_dense_eval_{uuid.uuid4().hex}")
    if index._request("GET", "/")["version"] != settings["qdrant_version"]:
        raise ValueError("Qdrant version differs from frozen configuration")
    calls = []
    import openai
    original_client = openai.OpenAI

    def observed_client(*args, **kwargs):
        client = original_client(*args, **kwargs)
        original_create = client.chat.completions.create

        def create(**request):
            start = time.monotonic()
            record = {"requested_model": request["model"]}
            try:
                response = original_create(**request)
                record.update(actual_model=response.model, usage=response.usage.model_dump() if response.usage else None)
                return response
            except Exception as error:
                record["error_type"] = type(error).__name__
                raise
            finally:
                record["seconds"] = round(time.monotonic() - start, 3)
                calls.append(record)

        client.chat.completions.create = create
        return client

    rows = []
    current_sample_id = None
    run_error = None
    started = time.monotonic()
    try:
        embedder = LocalEmbedder()
        with tempfile.TemporaryDirectory() as data_dir, patch("openai.OpenAI", observed_client), TestClient(
            create_app(Path(data_dir), embedder, index)
        ) as client:
            document_id_by_offline_id = {}
            chunks_by_document = {}
            source_chunk_by_online_id = {}
            for position, article in enumerate(articles, 1):
                # Only allowlisted article body crosses the online API boundary.
                filename = f"{article['document_id']}.md"
                response = client.post("/api/documents", files={"file": (filename, article["body"].encode(), "text/markdown")})
                if response.status_code != 201 or response.json().get("status") != "ready":
                    raise RuntimeError(f"corpus ingestion failed at article {position}: HTTP {response.status_code}")
                online_id = response.json()["document_id"]
                document_id_by_offline_id[article["document_id"]] = online_id
                chunks_by_document[online_id] = scroll_chunks(index, online_id)
                for chunk in chunks_by_document[online_id]:
                    source_chunk_by_online_id[chunk["chunk_id"]] = hashlib.sha256(
                        (article["document_id"] + "\0" + chunk["chunk_text"]).encode()).hexdigest()[:20]
                if position % 50 == 0:
                    print(f"indexed {position}/{len(articles)} articles", flush=True)
            source_document_by_online_id = {value: key for key, value in document_id_by_offline_id.items()}
            dev_ids = set(manifest["dev_ids"])
            for position, question in enumerate(questions, 1):
                current_sample_id = question["sample_id"]
                evidence = [{"document_id": document_id_by_offline_id[item["document_id"]], "fact": item["fact"]}
                            for item in question["evidence_list"]]
                mapping = map_facts(evidence, chunks_by_document)
                for fact in mapping["facts"]:
                    fact["source_document_id"] = source_document_by_online_id[fact["document_id"]]
                    fact["source_chunk_id"] = source_chunk_by_online_id[fact["chunk_id"]]
                start = time.monotonic()
                retrieved = offline_dense_query(embedder, index, question["query"], None, max(settings["retrieval_k"]))
                latency = round(time.monotonic() - start, 3)
                row = {"sample_id": question["sample_id"], "split": "dev" if question["sample_id"] in dev_ids else "test",
                       "question_type": question["question_type"],
                       "retrieval_seconds": latency,
                       "mapping": mapping, "retrieved": [{**item,
                           "source_document_id": source_document_by_online_id[item["document_id"]],
                           "source_chunk_id": source_chunk_by_online_id[item["chunk_id"]]} for item in retrieved],
                       "metrics": {str(k): score_retrieval(retrieved, mapping, [item["document_id"] for item in evidence], k)
                                   for k in settings["retrieval_k"]}}
                if question["sample_id"] in subset:
                    before = len(calls)
                    answer = client.post("/api/chat", json={"question": question["query"]})
                    row["answer_run"] = {"http_status": answer.status_code, "response": answer.json(), "llm_calls": calls[before:]}
                rows.append(row)
                if position % 100 == 0:
                    print(f"retrieved {position}/{len(questions)} questions", flush=True)
    except Exception as error:
        run_error = error
    finally:
        try:
            index._request("DELETE", f"/collections/{index.collection}")
        except Exception as error:
            if run_error is None:
                run_error = RuntimeError(f"Qdrant collection cleanup failed: {type(error).__name__}")
    if run_error is not None:
        failure = {"status": "failed", "error_type": type(run_error).__name__,
                   "error": str(run_error), "sample_id": current_sample_id,
                   "completed_questions": len(rows), "config": settings,
                   "rows": rows, "llm_calls": calls,
                   "effective_llm": effective_llm, "config_sha256": run_config_sha256}
        failure_path = output.with_name(output.stem + ".failed.json")
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
        raise RuntimeError(f"Dense baseline failed at {current_sample_id}: {run_error}") from run_error
    counts = Counter(row["mapping"]["status"] for row in rows)
    summary = {"question_count": len(rows), "answer_subset_count": len(subset),
               "answer_subset_types": dict(Counter(row["question_type"] for row in rows if row["sample_id"] in subset)),
               "answer_http_statuses": dict(Counter(row["answer_run"]["http_status"] for row in rows if "answer_run" in row)),
               "mapping_counts": dict(counts), "billed_cost_cny": None,
               "billed_cost_note": "Gateway did not expose a billable amount; token usage is recorded per call.",
               "llm_calls": len(calls), "elapsed_seconds": round(time.monotonic() - started, 3)}
    summary["prompt_tokens"] = sum((call.get("usage") or {}).get("prompt_tokens") or 0 for call in calls)
    summary["completion_tokens"] = sum((call.get("usage") or {}).get("completion_tokens") or 0 for call in calls)
    for k in settings["retrieval_k"]:
        for field in ("article_recall", "all_articles", "fact_coverage", "all_facts"):
            values = [row["metrics"][str(k)][field] for row in rows if row["metrics"][str(k)][field] is not None]
            summary[f"{field}@{k}"] = {"mean": sum(values) / len(values) if values else None, "denominator": len(values)}
    summary["by_split"] = {}
    for split in ("dev", "test"):
        split_rows = [row for row in rows if row["split"] == split]
        split_metrics = {"question_count": len(split_rows),
                         "mapping_counts": dict(Counter(row["mapping"]["status"] for row in split_rows))}
        for k in settings["retrieval_k"]:
            for field in ("article_recall", "all_articles", "fact_coverage", "all_facts"):
                values = [row["metrics"][str(k)][field] for row in split_rows if row["metrics"][str(k)][field] is not None]
                split_metrics[f"{field}@{k}"] = {"mean": sum(values) / len(values) if values else None, "denominator": len(values)}
        summary["by_split"][split] = split_metrics
    result = {"config": settings, "config_sha256": run_config_sha256,
              "question_ids": question_ids, "answer_subset_ids": sorted(subset), "corpus_count": len(articles),
              "source_to_online_document_id": document_id_by_offline_id,
              "effective_llm": effective_llm,
              "summary": summary, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("evaluation/configs/dense_baseline_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/dense_baseline_all_v1.json"))
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    result = run(Path.cwd(), settings, args.output)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
