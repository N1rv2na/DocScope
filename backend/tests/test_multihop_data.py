import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docscope.evaluation.multihop import (
    DataContractError,
    adapt_corpus,
    adapt_questions,
    build_manifest,
    split_questions,
    verify_manifest,
)


REVISION = "a" * 40


def article(url, **extra):
    return {
        "url": url,
        "title": "A public article",
        "body": "A fictional city hosted a fictional event.",
        "source": "Example News",
        "published_at": "2023-01-01T00:00:00+00:00",
        "answer": "private label",
        "fact": "private fact",
        **extra,
    }


def question(text, kind="inference_query", evidence=True):
    return {
        "query": text,
        "question_type": kind,
        "answer": "A reference answer",
        "evidence_list": [{"url": "https://example.test/a", "fact": "A fictional city"}] if evidence else [],
    }


class MultiHopDataTest(unittest.TestCase):
    def setUp(self):
        self.corpus = [article("https://example.test/a")]
        self.questions = [
            question("Which fictional city hosted the annual technology event in 2023 according to the article, including the venue, schedule, organizers, invited guests, transportation, local attractions, opening presentations, closing ceremony, and plans for the following year?"),
            question("Which fictional city hosted the annual technology event in 2024 according to the article, including the venue, schedule, organizers, invited guests, transportation, local attractions, opening presentations, closing ceremony, and plans for the following year?"),
            question("Who founded the fictional city described in the article?"),
            question("Did both articles describe the same city?", "comparison_query"),
            question("Did the articles describe different cities?", "comparison_query"),
            question("What is the missing answer?", "null_query", evidence=False),
            question("Which fact is unavailable?", "null_query", evidence=False),
        ]

    def write_inputs(self, directory):
        corpus_path = directory / "corpus.json"
        questions_path = directory / "MultiHopRAG.json"
        corpus_path.write_text(json.dumps(self.corpus), encoding="utf-8")
        questions_path.write_text(json.dumps(self.questions), encoding="utf-8")
        config = {
            "source_revision": REVISION,
            "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
            "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
            "seed": 20261025,
            "test_fraction": 0.5,
        }
        return corpus_path, questions_path, config

    def test_online_articles_use_allowlist_and_labels_stay_offline(self):
        online = adapt_corpus(self.corpus, REVISION)
        self.assertEqual(
            set(online[0]),
            {"document_id", "url", "title", "source", "published_at", "body"},
        )
        self.assertNotIn("private label", json.dumps(online))
        self.assertNotIn("private fact", json.dumps(online))
        offline = adapt_questions(self.questions, REVISION, {online[0]["url"]: online[0]["document_id"]})
        self.assertEqual(offline[0]["answer"], "A reference answer")
        self.assertEqual(offline[0]["evidence_list"][0]["fact"], "A fictional city")

    def test_ids_and_split_are_stable_under_row_reordering(self):
        online = adapt_corpus(self.corpus, REVISION)
        document_ids = {row["url"]: row["document_id"] for row in online}
        first = adapt_questions(self.questions, REVISION, document_ids)
        second = adapt_questions(list(reversed(self.questions)), REVISION, document_ids)
        self.assertEqual({row["sample_id"] for row in first}, {row["sample_id"] for row in second})
        self.assertEqual(split_questions(first, seed=17, test_fraction=0.5), split_questions(second, seed=17, test_fraction=0.5))

    def test_near_duplicate_questions_cannot_cross_split(self):
        online = adapt_corpus(self.corpus, REVISION)
        rows = adapt_questions(self.questions, REVISION, {online[0]["url"]: online[0]["document_id"]})
        split = split_questions(rows, seed=17, test_fraction=0.5)
        near_ids = {rows[0]["sample_id"], rows[1]["sample_id"]}
        self.assertTrue(near_ids <= set(split["dev_ids"]) or near_ids <= set(split["test_ids"]))
        self.assertGreaterEqual(split["audit"]["near_duplicate_pairs"], 1)
        self.assertEqual(split["audit"]["cross_split_near_duplicate_pairs"], 0)
        self.assertEqual(len(split["audit"]["near_duplicate_pair_ids"]), split["audit"]["near_duplicate_pairs"])
        self.assertIn(sorted(near_ids), split["audit"]["near_duplicate_pair_ids"])

    def test_short_questions_with_one_changed_token_are_grouped(self):
        online = adapt_corpus(self.corpus, REVISION)
        first = "Which company shipped version 2023 of the device and reported the launch date and price?"
        second = first.replace("2023", "2024")
        rows = adapt_questions(
            self.questions + [question(first), question(second)],
            REVISION,
            {online[0]["url"]: online[0]["document_id"]},
        )
        split = split_questions(rows, seed=17, test_fraction=0.5)
        near_ids = {rows[-2]["sample_id"], rows[-1]["sample_id"]}
        self.assertTrue(near_ids <= set(split["dev_ids"]) or near_ids <= set(split["test_ids"]))
        self.assertGreaterEqual(split["audit"]["near_duplicate_pairs"], 2)

    def test_exact_duplicate_question_is_rejected(self):
        online = adapt_corpus(self.corpus, REVISION)
        rows = self.questions + [question(self.questions[0]["query"].upper())]
        with self.assertRaisesRegex(DataContractError, "duplicate question"):
            adapt_questions(rows, REVISION, {online[0]["url"]: online[0]["document_id"]})

    def test_missing_fields_and_broken_evidence_reference_fail(self):
        with self.assertRaisesRegex(DataContractError, "body"):
            adapt_corpus([{key: value for key, value in self.corpus[0].items() if key != "body"}], REVISION)
        online = adapt_corpus(self.corpus, REVISION)
        ids = {online[0]["url"]: online[0]["document_id"]}
        with self.assertRaisesRegex(DataContractError, "answer"):
            adapt_questions([{"query": "Question?", "question_type": "inference_query", "evidence_list": []}], REVISION, ids)
        with self.assertRaisesRegex(DataContractError, "unknown article"):
            adapt_questions([question("Question?", evidence=True) | {"evidence_list": [{"url": "https://example.test/missing", "fact": "x"}]}], REVISION, ids)

    def test_null_query_requires_empty_evidence(self):
        online = adapt_corpus(self.corpus, REVISION)
        with self.assertRaisesRegex(DataContractError, "null_query"):
            adapt_questions([question("No answer?", kind="null_query")], REVISION, {online[0]["url"]: online[0]["document_id"]})

    def test_unknown_question_type_is_rejected(self):
        online = adapt_corpus(self.corpus, REVISION)
        with self.assertRaisesRegex(DataContractError, "unsupported question_type"):
            adapt_questions([question("Unknown kind?", kind="invented_query")], REVISION, {online[0]["url"]: online[0]["document_id"]})

    def test_checksum_mismatch_fails_before_manifest_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus_path, questions_path, config = self.write_inputs(Path(temp))
            config["questions_sha256"] = "0" * 64
            with self.assertRaisesRegex(DataContractError, "SHA-256 mismatch"):
                build_manifest(corpus_path, questions_path, config)

    def test_missing_split_configuration_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus_path, questions_path, config = self.write_inputs(Path(temp))
            del config["seed"]
            with self.assertRaisesRegex(DataContractError, "seed"):
                build_manifest(corpus_path, questions_path, config)

    def test_manifest_parses_the_same_bytes_that_were_hashed(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus_path, questions_path, config = self.write_inputs(Path(temp))
            original_read_bytes = Path.read_bytes

            def change_file_after_read(path):
                data = original_read_bytes(path)
                if path == questions_path:
                    questions_path.write_text(json.dumps(self.questions + [question("A new question added later?")]), encoding="utf-8")
                return data

            with patch.object(Path, "read_bytes", change_file_after_read):
                manifest = build_manifest(corpus_path, questions_path, config)
            self.assertEqual(manifest["question_count"], len(self.questions))

    def test_manifest_reproduces_and_seals_split_without_raw_text(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus_path, questions_path, config = self.write_inputs(Path(temp))
            first = build_manifest(corpus_path, questions_path, config)
            second = build_manifest(corpus_path, questions_path, config)
            self.assertEqual(first, second)
            self.assertEqual(first["question_count"], len(self.questions))
            self.assertEqual(len(first["dev_ids"]) + len(first["test_ids"]), len(self.questions))
            self.assertNotIn("A reference answer", json.dumps(first))
            self.assertNotIn("A fictional city", json.dumps(first))
            verify_manifest(first, corpus_path, questions_path, config)
            tampered = dict(first, test_ids=[])
            with self.assertRaisesRegex(DataContractError, "manifest differs"):
                verify_manifest(tampered, corpus_path, questions_path, config)
            new_version = dict(config, source_revision="b" * 40)
            with self.assertRaisesRegex(DataContractError, "manifest differs"):
                verify_manifest(first, corpus_path, questions_path, new_version)


if __name__ == "__main__":
    unittest.main()
