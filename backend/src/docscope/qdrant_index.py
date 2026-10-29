"""Direct Qdrant REST calls for the M1 Dense-only collection."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class QdrantError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class QdrantIndex:
    def __init__(self, url: str, collection: str = "docscope_chunks"):
        self.url = url.rstrip("/")
        self.collection = collection

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(300).decode("utf-8", errors="replace")
            raise QdrantError(f"Qdrant HTTP {error.code}: {detail}", error.code) from error
        except urllib.error.URLError as error:
            raise QdrantError(f"Qdrant unavailable: {error.reason}") from error

    def ensure(self) -> None:
        from docscope.embedding import EMBEDDING_DIMENSION

        version = self._request("GET", "/").get("version")
        if version != "1.20.0":
            raise QdrantError(f"Qdrant 1.20.0 required, received {version!r}")
        try:
            self._request("GET", f"/collections/{self.collection}")
        except QdrantError as error:
            if error.status_code != 404:
                raise
            self._request("PUT", f"/collections/{self.collection}", {
                "vectors": {"dense": {"size": EMBEDDING_DIMENSION, "distance": "Cosine"}},
            })

    def upsert(self, points: list[dict]) -> None:
        self._request("PUT", f"/collections/{self.collection}/points?wait=true", {"points": points})

    def query(self, vector: list[float], ready_ids: list[str], limit: int) -> list[dict]:
        response = self._request("POST", f"/collections/{self.collection}/points/query", {
            "query": vector,
            "using": "dense",
            "filter": {"must": [{"key": "document_id", "match": {"any": ready_ids}}]},
            "limit": limit,
            "with_payload": True,
        })
        return response["result"]["points"]

    def delete_document(self, document_id: str) -> None:
        try:
            self._request("POST", f"/collections/{self.collection}/points/delete?wait=true", {
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
            })
        except QdrantError as error:
            if error.status_code != 404:
                raise

    def get_point(self, chunk_id: str) -> dict | None:
        try:
            return self._request("GET", f"/collections/{self.collection}/points/{chunk_id}")["result"]
        except QdrantError as error:
            if error.status_code != 404:
                raise
            return None
