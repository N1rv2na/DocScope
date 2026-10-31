"""DOCS-014 SSE event contract through the real FastAPI and LangGraph path."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_markdown_dense_api import FakeEmbedder
from backend.tests.test_second_hop import SAMPLE, SAMPLE_DIR, TwoHopIndex, EvidencePlanner
from docscope.api import create_app
from docscope.agent_run import AgentRunner


def events(response):
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "),
                       json.loads(lines[1].removeprefix("data: "))))
    return result


class ConfigurablePlanner(EvidencePlanner):
    forced_answer = None

    def generate(self, question, evidence):
        if self.forced_answer is not None:
            return self.forced_answer
        return super().generate(question, evidence)


class AgentStreamTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.llm = ConfigurablePlanner()
        self.client = TestClient(create_app(data_dir=Path(self.temp.name),
                                            embedder=FakeEmbedder(), index=TwoHopIndex(), llm=self.llm))
        self.addCleanup(self.client.close)
        for name in (SAMPLE["first_source"], SAMPLE["second_source"]):
            response = self.client.post("/api/documents", files={
                "file": (name, (SAMPLE_DIR / name).read_bytes())})
            self.assertEqual(response.status_code, 201)

    def test_two_hop_stream_has_correlated_stages_answer_citations_and_one_done(self):
        response = self.client.post("/api/agent/run/stream", json={"question": SAMPLE["question"]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        wire = events(response)
        types = [kind for kind, _ in wire]
        self.assertEqual(types.count("done"), 1)
        self.assertEqual(types.count("error"), 0)
        self.assertEqual(types.count("stage"), 5)
        self.assertGreater(types.count("token"), 0)
        self.assertEqual(types.count("citation"), 2)
        ids = {(payload["run_id"], payload["message_id"]) for _, payload in wire}
        self.assertEqual(len(ids), 1)
        self.assertTrue(all(ids.pop()))
        stages = [payload for kind, payload in wire if kind == "stage"]
        self.assertEqual([step["stage"] for step in stages],
                         ["plan", "retrieve", "plan_next", "retrieve", "answer"])
        self.assertEqual([step["round"] for step in stages if step["stage"] == "retrieve"], [1, 2])
        self.assertEqual(stages[2]["source_chunk_id"], stages[1]["evidence_ids"][0])
        answer = "".join(payload["text"] for kind, payload in wire if kind == "token")
        self.assertEqual(answer, SAMPLE["answer"])
        done = wire[-1][1]
        self.assertEqual(done["status"], "answered")
        self.assertEqual(done["stop_reason"], "answered")
        cited = [payload["citation"]["chunk_id"] for kind, payload in wire if kind == "citation"]
        self.assertEqual(done["citation_ids"], cited)
        self.assertEqual(done["answer"], answer)
        stored = json.loads((Path(self.temp.name) / "runs" / f"{done['run_id']}.json").read_text())
        self.assertEqual(stored["status"], "answered")

    def test_model_failure_ends_with_error_and_no_done_or_token(self):
        self.llm.proposal = lambda question, evidence: (_ for _ in ()).throw(RuntimeError("unavailable"))
        wire = events(self.client.post("/api/agent/run/stream", json={"question": SAMPLE["question"]}))
        self.assertEqual([kind for kind, _ in wire][-1], "error")
        self.assertNotIn("done", [kind for kind, _ in wire])
        self.assertNotIn("token", [kind for kind, _ in wire])
        self.assertEqual(wire[-1][1]["error_code"], "planner_failed")
        self.assertEqual(len({(payload["run_id"], payload["message_id"]) for _, payload in wire}), 1)

    def test_no_evidence_stream_has_refusal_done_without_answer_or_citation(self):
        documents = self.client.get('/api/documents').json()['documents']
        for item in documents:
            self.assertEqual(self.client.delete(f"/api/documents/{item['document_id']}").status_code, 200)
        wire = events(self.client.post('/api/agent/run/stream', json={'question': SAMPLE['question']}))
        self.assertEqual([kind for kind, _ in wire], ['stage', 'stage', 'done'])
        self.assertEqual(wire[-1][1]['status'], 'insufficient_evidence')
        self.assertEqual(wire[-1][1]['stop_reason'], 'no_evidence')
        self.assertIsNone(wire[-1][1]['answer'])
        self.assertEqual(wire[-1][1]['citation_ids'], [])

    def test_stage_is_yielded_before_answer_generation(self):
        generated = []

        def generate(question, evidence):
            generated.append(True)
            return {'status': 'answered', 'answer': 'Supported.', 'citation_ids': ['chunk-1']}

        runner = AgentRunner(
            retrieve=lambda query: [{'chunk_id': 'chunk-1', 'chunk_text': 'Supported.'}],
            generate=generate,
            validate=lambda raw, evidence: {'status': 'answered', 'answer': raw['answer'],
                                             'citations': [{'chunk_id': 'chunk-1'}]},
            sources_current=lambda evidence: True,
            plan_next=lambda question, evidence: {'evidence_status': 'sufficient', 'missing_fact': None,
                                                   'next_query': None, 'bridge_text': None,
                                                   'source_chunk_id': None},
        )
        stream = runner.stream_run('What is supported?', 'fixed-run')
        self.assertEqual(next(stream)[1]['stage'], 'plan')
        self.assertEqual(next(stream)[1]['stage'], 'retrieve')
        self.assertEqual(next(stream)[1]['stage'], 'plan_next')
        self.assertEqual(generated, [])
        self.assertEqual(next(stream)[1]['stage'], 'answer')
        self.assertEqual(generated, [True])
        self.assertEqual(next(stream)[1]['status'], 'answered')

    def test_persistence_failure_sends_only_error_terminal(self):
        with patch('docscope.agent_run.RunStore.save', side_effect=OSError('disk full')):
            wire = events(self.client.post('/api/agent/run/stream', json={'question': SAMPLE['question']}))
        self.assertEqual(wire[-1][0], 'error')
        self.assertEqual(wire[-1][1]['error_code'], 'run_record_failed')
        self.assertNotIn('done', [kind for kind, _ in wire])
        self.assertNotIn('token', [kind for kind, _ in wire])

    def test_invalid_answer_citation_sends_error_without_publishing_text(self):
        self.llm.forced_answer = {
            'status': 'answered', 'answer': 'Unsupported.', 'citation_ids': ['fabricated']}
        wire = events(self.client.post('/api/agent/run/stream', json={'question': SAMPLE['question']}))
        self.assertEqual(wire[-1][0], 'error')
        self.assertEqual(wire[-1][1]['error_code'], 'model_output_invalid')
        self.assertNotIn('token', [kind for kind, _ in wire])
        self.assertNotIn('citation', [kind for kind, _ in wire])


if __name__ == "__main__":
    unittest.main()
