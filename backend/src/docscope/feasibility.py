"""DOCS-002 one-shot capability probes; this is not the retrieval service."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from docscope.embedding import EMBEDDING_MODEL, EMBEDDING_REVISION

QDRANT_VERSION = "1.20.0"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
EXAMPLE_CHUNKS = [
    "The Orion telescope was built in 2022 by the North Observatory.",
    "The Vega telescope was built in 2020 by the South Observatory.",
    "The North Observatory reported a new mirror coating in 2024.",
]
EXAMPLE_QUERY = "Orion"


class FeasibilityError(RuntimeError):
    """A capability check did not meet its contract."""


def classify_connection_error(error: Exception) -> str:
    if isinstance(error, (ConnectionError, TimeoutError, socket.timeout)):
        return "environment_unreachable"
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, (OSError, TimeoutError)):
        return "environment_unreachable"
    try:
        from openai import APIConnectionError, APITimeoutError
        if isinstance(error, (APIConnectionError, APITimeoutError)):
            return "environment_unreachable"
    except ImportError:
        pass
    return "call_failed"


def validate_llm_base_url(base: str) -> None:
    parsed = urlsplit(base)
    if parsed.username or parsed.password:
        raise FeasibilityError("LLM_BASE_URL must not embed credentials")
    if not parsed.hostname or parsed.query or parsed.fragment:
        raise FeasibilityError("LLM_BASE_URL must be an absolute endpoint without query or fragment")
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    if parsed.scheme != "https":
        raise FeasibilityError("remote LLM endpoints require HTTPS; HTTP is only allowed on loopback")


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_cny_per_million: Decimal,
    output_cny_per_million: Decimal,
    development_calls: int,
    evaluation_calls: int,
    spent_cny: Decimal = Decimal(0),
) -> dict[str, str]:
    """Estimate spend from observed usage and user-supplied CNY tariffs."""
    input_cny_per_million = Decimal(input_cny_per_million)
    output_cny_per_million = Decimal(output_cny_per_million)
    spent_cny = Decimal(spent_cny)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (prompt_tokens, completion_tokens)):
        raise FeasibilityError("measured token counts must be integers")
    if prompt_tokens < 0 or completion_tokens < 0 or prompt_tokens + completion_tokens == 0:
        raise FeasibilityError("measured token usage is required")
    if not all(value.is_finite() for value in (input_cny_per_million, output_cny_per_million, spent_cny)):
        raise FeasibilityError("CNY values must be finite")
    if min(input_cny_per_million, output_cny_per_million, spent_cny) < 0:
        raise FeasibilityError("CNY values cannot be negative")
    if development_calls < 0 or evaluation_calls < 0:
        raise FeasibilityError("call counts cannot be negative")
    sample = (Decimal(prompt_tokens) * input_cny_per_million + Decimal(completion_tokens) * output_cny_per_million) / Decimal(1000000)
    projected = sample * (development_calls + evaluation_calls)
    return {
        "sample_cost_cny": f"{sample:.5f}",
        "projected_cost_cny": f"{projected:.5f}",
        "spent_cny": f"{spent_cny:.5f}",
        "estimated_total_cny": f"{spent_cny + projected:.5f}",
    }


def validate_qdrant_results(dense: list[int], bm25: list[int], fused: list[int], allowed: set[int]) -> None:
    if not dense or not bm25 or not fused:
        raise FeasibilityError("Dense, BM25 and RRF must each return at least one chunk")
    if not (set(dense) | set(bm25) | set(fused)) <= allowed:
        raise FeasibilityError("query returned unknown chunk ID")
    if not set(fused) <= set(dense) | set(bm25):
        raise FeasibilityError("fusion returned a chunk absent from both prefetches")


def _request(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(500).decode("utf-8", errors="replace")
        raise FeasibilityError(f"Qdrant HTTP {error.code}: {detail}") from error


def _encode_chunks():
    from sentence_transformers import SentenceTransformer

    start = time.perf_counter()
    model = SentenceTransformer(EMBEDDING_MODEL, revision=EMBEDDING_REVISION, device="cpu")
    loaded = time.perf_counter() - start
    start = time.perf_counter()
    vectors = model.encode(EXAMPLE_CHUNKS + [EXAMPLE_QUERY], normalize_embeddings=True).tolist()
    elapsed = time.perf_counter() - start
    return vectors, {"model": EMBEDDING_MODEL, "revision": EMBEDDING_REVISION, "dimension": len(vectors[0]), "load_seconds": round(loaded, 3), "encode_seconds": round(elapsed, 3)}


def probe_local() -> dict:
    from sentence_transformers import CrossEncoder

    vectors, embedding = _encode_chunks()
    start = time.perf_counter()
    reranker = CrossEncoder(RERANKER_MODEL, revision=RERANKER_REVISION, device="cpu")
    loaded = time.perf_counter() - start
    start = time.perf_counter()
    scores = reranker.predict([(EXAMPLE_QUERY, chunk) for chunk in EXAMPLE_CHUNKS])
    elapsed = time.perf_counter() - start
    if len(vectors) != 4 or len(scores) != 3:
        raise FeasibilityError("local models returned unexpected output shape")
    return {
        "status": "passed",
        "embedding": embedding,
        "reranker": {"model": RERANKER_MODEL, "revision": RERANKER_REVISION, "load_seconds": round(loaded, 3), "predict_seconds": round(elapsed, 3), "scores": [round(float(value), 4) for value in scores]},
        "versions": {name: importlib.metadata.version(name) for name in ("sentence-transformers", "transformers", "torch")},
    }


def _points(response: dict) -> list[dict]:
    return response["result"]["points"]


def probe_qdrant(base: str) -> dict:
    start = time.perf_counter()
    version = _request(base, "GET", "/").get("version")
    if version != QDRANT_VERSION:
        raise FeasibilityError(f"Qdrant server version {version!r}; expected {QDRANT_VERSION}")
    vectors, embedding = _encode_chunks()
    collection = "docscope_m0_" + uuid.uuid4().hex[:12]
    created = False
    primary_error = None
    try:
        _request(base, "PUT", f"/collections/{collection}", {"vectors": {"dense": {"size": embedding["dimension"], "distance": "Cosine"}}})
        created = True
        _request(base, "PUT", f"/collections/{collection}/index?wait=true", {
            "field_name": "chunk_text",
            "field_schema": {"type": "text", "tokenizer": "word", "lowercase": True, "scoring": {"type": "bm25"}},
        })
        _request(base, "PUT", f"/collections/{collection}/points?wait=true", {"points": [
            {"id": index + 1, "vector": {"dense": vector}, "payload": {"chunk_id": index + 1, "chunk_text": chunk}}
            for index, (vector, chunk) in enumerate(zip(vectors[:3], EXAMPLE_CHUNKS))
        ]})
        endpoint = f"/collections/{collection}/points/query"
        dense = _points(_request(base, "POST", endpoint, {"query": vectors[3], "using": "dense", "limit": 3, "with_payload": True}))
        bm25 = _points(_request(base, "POST", endpoint, {"query": {"text": EXAMPLE_QUERY}, "using": "chunk_text", "limit": 3, "with_payload": True}))
        fused = _points(_request(base, "POST", endpoint, {
            "prefetch": [
                {"query": vectors[3], "using": "dense", "limit": 3},
                {"query": {"text": EXAMPLE_QUERY}, "using": "chunk_text", "limit": 3},
            ],
            "query": {"fusion": "rrf"}, "limit": 3, "with_payload": True,
        }))
        ids = [[int(point["payload"]["chunk_id"]) for point in points] for points in (dense, bm25, fused)]
        validate_qdrant_results(ids[0], ids[1], ids[2], {1, 2, 3})
        if ids[1] != [1]:
            raise FeasibilityError(f"BM25 should retrieve only the known Orion chunk, got {ids[1]}")
        return {"status": "passed", "server_version": version, "client": "Python urllib REST", "embedding": embedding, "dense_ids": ids[0], "bm25_ids": ids[1], "rrf_ids": ids[2], "elapsed_seconds": round(time.perf_counter() - start, 3)}
    except Exception as error:
        primary_error = error
        raise
    finally:
        if created:
            try:
                _request(base, "DELETE", f"/collections/{collection}")
            except Exception as cleanup_error:
                if primary_error is None:
                    raise
                print(f"warning: temporary Qdrant collection cleanup failed: {cleanup_error}", file=sys.stderr)


def probe_llm(scenario: str = "short") -> dict:
    from openai import OpenAI

    base = os.getenv("LLM_BASE_URL", "http://127.0.0.1:3425/v1")
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "group/docscope-free")
    if not key:
        raise FeasibilityError("LLM_API_KEY must be set in the local environment")
    validate_llm_base_url(base)
    client = OpenAI(base_url=base, api_key=key, timeout=30, max_retries=0)
    start = time.perf_counter()
    if scenario == "evidence":
        prompt = (
            "Use only the following evidence. Answer the question in one sentence with [1] and [2] citations. "
            "Question: Which observatory built Orion, and what did it report in 2024? "
            "[1] The Orion telescope was built in 2022 by the North Observatory. "
            "[2] The North Observatory reported a new mirror coating in 2024."
        )
        limit = 160
    else:
        prompt = "Reply with one short word: ready"
        limit = 32
    response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=limit)
    elapsed = time.perf_counter() - start
    if not response.choices or not response.choices[0].message.content:
        raise FeasibilityError("LLM returned no text")
    if response.usage is None or response.usage.prompt_tokens is None or response.usage.completion_tokens is None:
        raise FeasibilityError("LLM response has no token usage; cost cannot be projected")
    return {"status": "passed", "scenario": scenario, "configured_model": model, "response_model": response.model,
            "prompt_tokens": response.usage.prompt_tokens, "completion_tokens": response.usage.completion_tokens,
            "elapsed_seconds": round(elapsed, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("local")
    qdrant = sub.add_parser("qdrant")
    qdrant.add_argument("--url", default="http://127.0.0.1:6333")
    llm = sub.add_parser("llm")
    llm.add_argument("--scenario", choices=("short", "evidence"), default="short")
    cost = sub.add_parser("cost")
    cost.add_argument("--usage-json", type=Path, required=True)
    cost.add_argument("--input-cny-per-million", type=Decimal, required=True)
    cost.add_argument("--output-cny-per-million", type=Decimal, required=True)
    cost.add_argument("--development-calls", type=int, required=True)
    cost.add_argument("--evaluation-calls", type=int, required=True)
    cost.add_argument("--spent-cny", type=Decimal, default=Decimal(0))
    args = parser.parse_args()
    try:
        if args.command == "local":
            result = probe_local()
        elif args.command == "qdrant":
            result = probe_qdrant(args.url)
        elif args.command == "llm":
            result = probe_llm(args.scenario)
        else:
            usage = json.loads(args.usage_json.read_text(encoding="utf-8"))
            result = estimate_cost(usage["prompt_tokens"], usage["completion_tokens"],
                                   args.input_cny_per_million, args.output_cny_per_million,
                                   args.development_calls, args.evaluation_calls, args.spent_cny)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": classify_connection_error(error), "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
