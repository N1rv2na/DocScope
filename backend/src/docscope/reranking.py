"""Bounded local Cross-Encoder scoring over Qdrant RRF candidates."""

from __future__ import annotations

import math
from threading import Lock

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
MAX_RERANK_CANDIDATES = 20


class LocalReranker:
    def __init__(self):
        self._model = None
        self._load_lock = Lock()

    def score(self, question: str, texts: list[str]) -> list[float]:
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    self._model = CrossEncoder(RERANKER_MODEL, revision=RERANKER_REVISION, device="cpu")
        values = self._model.predict([(question, text) for text in texts], batch_size=16,
                                     show_progress_bar=False)
        return [float(value) for value in values]


def rerank_points(question: str, candidates: list[dict], scorer: LocalReranker,
                  limit: int) -> list[dict]:
    """Keep Chunk identity and prior RRF scores separate from Cross-Encoder scores."""
    if not 1 <= limit <= 10 or len(candidates) > MAX_RERANK_CANDIDATES:
        raise ValueError("invalid rerank candidate or result limit")
    if not candidates:
        return []
    scores = scorer.score(question, [point["payload"]["chunk_text"] for point in candidates])
    if len(scores) != len(candidates):
        raise ValueError("reranker score count differs from candidate count")
    ranked = []
    for prior_rank, (point, value) in enumerate(zip(candidates, scores), 1):
        score = float(value)
        if not math.isfinite(score):
            raise ValueError("reranker scores must be finite")
        ranked.append({**point, "rrf_rank": point.get("rrf_rank", prior_rank), "rrf_score": point["score"],
                       "rerank_score": score})
    return sorted(ranked, key=lambda point: -point["rerank_score"])[:limit]
