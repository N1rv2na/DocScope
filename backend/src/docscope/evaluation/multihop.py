"""Pinned MultiHop-RAG input validation and leak-free split manifests.

Only ``adapt_corpus`` returns records intended for an online index. Reference
answers and evidence facts are kept in the separate offline question records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


QUESTION_TYPES = frozenset({"inference_query", "comparison_query", "temporal_query", "null_query"})
NEAR_DUPLICATE_THRESHOLD = 0.8
CHAR_DUPLICATE_THRESHOLD = 0.9


class DataContractError(ValueError):
    """The supplied data cannot satisfy the frozen evaluation contract."""


def _text(row: dict, key: str, location: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"{location}: missing or empty {key}")
    return value.strip()


def _revision(revision: str) -> None:
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise DataContractError("source_revision must be a 40-character lowercase commit hash")


def _question_key(query: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", query).casefold()))


def _id(prefix: str, revision: str, value: str) -> str:
    digest = hashlib.sha256(f"{revision}\0{value}".encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def adapt_corpus(rows: list[dict], revision: str) -> list[dict]:
    """Return online-safe article records with provenance and no label fields."""
    _revision(revision)
    if not isinstance(rows, list) or not rows:
        raise DataContractError("corpus must be a non-empty JSON array")
    online = []
    seen_urls = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DataContractError(f"corpus[{index}] must be an object")
        location = f"corpus[{index}]"
        url = _text(row, "url", location)
        if url in seen_urls:
            raise DataContractError(f"{location}: duplicate article URL {url}")
        seen_urls.add(url)
        online.append({
            "document_id": _id("mhr-doc", revision, url),
            "url": url,
            "title": _text(row, "title", location),
            "source": _text(row, "source", location),
            "published_at": _text(row, "published_at", location),
            "body": _text(row, "body", location),
        })
    return online


def adapt_questions(rows: list[dict], revision: str, document_ids_by_url: dict[str, str]) -> list[dict]:
    """Validate and retain labels strictly in offline question records."""
    _revision(revision)
    if not isinstance(rows, list) or not rows:
        raise DataContractError("questions must be a non-empty JSON array")
    offline = []
    seen_keys = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DataContractError(f"questions[{index}] must be an object")
        location = f"questions[{index}]"
        query = _text(row, "query", location)
        normalized = _question_key(query)
        if not normalized:
            raise DataContractError(f"{location}: query contains no supported words")
        if normalized in seen_keys:
            raise DataContractError(f"{location}: duplicate question after normalization")
        seen_keys.add(normalized)
        kind = _text(row, "question_type", location)
        if kind not in QUESTION_TYPES:
            raise DataContractError(f"{location}: unsupported question_type {kind}")
        answer = _text(row, "answer", location)
        evidence = row.get("evidence_list")
        if not isinstance(evidence, list):
            raise DataContractError(f"{location}: evidence_list must be an array")
        if kind == "null_query" and evidence:
            raise DataContractError(f"{location}: null_query must have empty evidence_list")
        if kind != "null_query" and not evidence:
            raise DataContractError(f"{location}: answerable question requires evidence_list")
        offline_evidence = []
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise DataContractError(f"{location}.evidence_list[{evidence_index}] must be an object")
            item_location = f"{location}.evidence_list[{evidence_index}]"
            url = _text(item, "url", item_location)
            fact = _text(item, "fact", item_location)
            if url not in document_ids_by_url:
                raise DataContractError(f"{item_location}: unknown article URL {url}")
            offline_evidence.append({"document_id": document_ids_by_url[url], "url": url, "fact": fact})
        offline.append({
            "sample_id": _id("mhr-q", revision, normalized),
            "query": query,
            "question_type": kind,
            "answer": answer,
            "evidence_list": offline_evidence,
        })
    return offline


def _trigrams(query: str) -> set[tuple[str, str, str]]:
    words = _question_key(query).split()
    return set(zip(words, words[1:], words[2:]))


def _char_trigrams(query: str) -> set[str]:
    text = _question_key(query)
    return {text[i:i + 3] for i in range(max(len(text) - 2, 0))}


def _similar(left: set, right: set, threshold: float) -> bool:
    if not left or not right or min(len(left), len(right)) < threshold * max(len(left), len(right)):
        return False
    overlap = len(left & right)
    return overlap / (len(left) + len(right) - overlap) >= threshold


def split_questions(rows: list[dict], seed: int, test_fraction: float) -> dict:
    """Group near duplicates, then deterministically stratify by question type."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise DataContractError("seed must be an integer")
    if not isinstance(test_fraction, (float, int)) or isinstance(test_fraction, bool) or not 0 < test_fraction < 1:
        raise DataContractError("test_fraction must be between 0 and 1")
    if not rows:
        raise DataContractError("cannot split zero questions")
    ids = [row["sample_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise DataContractError("duplicate sample_id in questions")
    parents = list(range(len(rows)))

    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    trigrams = [_trigrams(row["query"]) for row in rows]
    char_trigrams = [_char_trigrams(row["query"]) for row in rows]
    near_pairs = 0
    near_pair_ids = []
    for i, left in enumerate(trigrams):
        for j in range(i + 1, len(rows)):
            if _similar(left, trigrams[j], NEAR_DUPLICATE_THRESHOLD) or _similar(char_trigrams[i], char_trigrams[j], CHAR_DUPLICATE_THRESHOLD):
                if rows[i]["question_type"] != rows[j]["question_type"]:
                    raise DataContractError("near duplicate questions span question types; review source labels")
                near_pairs += 1
                near_pair_ids.append(sorted((ids[i], ids[j])))
                parents[root(j)] = root(i)

    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[root(index)].append(row)
    by_type = defaultdict(list)
    for group in groups.values():
        by_type[group[0]["question_type"]].append(sorted(row["sample_id"] for row in group))

    dev_ids, test_ids, counts = [], [], {}
    for kind in sorted(by_type):
        kind_groups = by_type[kind]
        if len(kind_groups) < 2:
            raise DataContractError(f"question_type {kind} has fewer than two independent groups")
        kind_groups.sort(key=lambda group: hashlib.sha256(f"{seed}\0{group[0]}".encode()).hexdigest())
        target = max(1, round(sum(map(len, kind_groups)) * test_fraction))
        kind_test, kind_dev = [], []
        for position, group in enumerate(kind_groups):
            if len(kind_test) < target and position < len(kind_groups) - 1:
                kind_test.extend(group)
            else:
                kind_dev.extend(group)
        if not kind_test or not kind_dev:
            raise DataContractError(f"question_type {kind} cannot form both splits")
        test_ids.extend(kind_test)
        dev_ids.extend(kind_dev)
        counts[kind] = {"dev": len(kind_dev), "test": len(kind_test)}

    return {
        "dev_ids": sorted(dev_ids),
        "test_ids": sorted(test_ids),
        "counts_by_type": counts,
        "audit": {
            "exact_duplicate_pairs": 0,
            "near_duplicate_pairs": near_pairs,
            "near_duplicate_pair_ids": sorted(near_pair_ids),
            "near_duplicate_groups": sum(len(group) > 1 for group in groups.values()),
            "cross_split_near_duplicate_pairs": 0,
            "near_duplicate_rule": "NFKC casefold word trigrams Jaccard >= 0.8 OR character trigrams Jaccard >= 0.9; connected components stay in one split",
        },
    }


def _parse_array(path: Path, raw: bytes) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DataContractError(f"cannot parse JSON at {path}: {error}") from error
    if not isinstance(data, list):
        raise DataContractError(f"{path}: expected a JSON array")
    return data


def _check_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise DataContractError("config must be an object")
    _revision(config.get("source_revision"))
    for name in ("corpus_sha256", "questions_sha256"):
        if not isinstance(config.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", config[name]):
            raise DataContractError(f"config {name} must be a SHA-256 hex digest")
    if not isinstance(config.get("seed"), int) or isinstance(config["seed"], bool):
        raise DataContractError("config seed must be an integer")
    fraction = config.get("test_fraction")
    if not isinstance(fraction, (float, int)) or isinstance(fraction, bool) or not 0 < fraction < 1:
        raise DataContractError("config test_fraction must be between 0 and 1")


def build_manifest(corpus_path: Path, questions_path: Path, config: dict) -> dict:
    """Check pinned bytes and build a raw-text-free, reproducible split artifact."""
    _check_config(config)
    corpus_path, questions_path = Path(corpus_path), Path(questions_path)
    pinned_bytes = {}
    for path, key in ((corpus_path, "corpus_sha256"), (questions_path, "questions_sha256")):
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise DataContractError(f"cannot read {path}: {error}") from error
        actual = hashlib.sha256(raw).hexdigest()
        if actual != config[key]:
            raise DataContractError(f"{path}: SHA-256 mismatch for pinned source version")
        pinned_bytes[key] = raw
    online = adapt_corpus(_parse_array(corpus_path, pinned_bytes["corpus_sha256"]), config["source_revision"])
    offline = adapt_questions(
        _parse_array(questions_path, pinned_bytes["questions_sha256"]),
        config["source_revision"],
        {article["url"]: article["document_id"] for article in online},
    )
    result = split_questions(offline, config["seed"], config["test_fraction"])
    return {
        "format_version": 1,
        "source": "yixuantt/MultiHopRAG",
        "source_revision": config["source_revision"],
        "corpus_sha256": config["corpus_sha256"],
        "questions_sha256": config["questions_sha256"],
        "seed": config["seed"],
        "test_fraction": config["test_fraction"],
        "corpus_count": len(online),
        "question_count": len(offline),
        **result,
    }


def verify_manifest(manifest: dict, corpus_path: Path, questions_path: Path, config: dict) -> None:
    if manifest != build_manifest(corpus_path, questions_path, config):
        raise DataContractError("frozen manifest differs from regenerated data split")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify a pinned MultiHop-RAG split")
    parser.add_argument("mode", choices=("freeze", "verify"))
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        generated = build_manifest(args.corpus, args.questions, config)
        if args.mode == "verify":
            current = json.loads(args.manifest.read_text(encoding="utf-8"))
            if current != generated:
                raise DataContractError("frozen manifest differs from regenerated data split")
        elif args.manifest.exists():
            current = json.loads(args.manifest.read_text(encoding="utf-8"))
            if current != generated:
                raise DataContractError("existing manifest differs; refusing to overwrite frozen split")
        else:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(generated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (DataContractError, OSError, UnicodeError, json.JSONDecodeError, KeyError) as error:
        print(f"MultiHop-RAG contract error: {error}", file=sys.stderr)
        return 2
    print(f"{args.mode}: {generated['question_count']} questions, {len(generated['dev_ids'])} dev, {len(generated['test_ids'])} sealed test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
