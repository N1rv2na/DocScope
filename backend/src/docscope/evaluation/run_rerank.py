"""Run DOCS-010 Dense/Hybrid/Rerank ablation on the frozen dev split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docscope.evaluation.run_hybrid import run
from docscope.reranking import LocalReranker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("evaluation/configs/rerank_dev_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/rerank_dev_v1.json"))
    args = parser.parse_args()
    result = run(Path.cwd(), json.loads(args.config.read_text()), args.output, reranker=LocalReranker())
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
