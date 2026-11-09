"""DOCS-025: retrieval stopping does not override answer or request safety."""

import unittest

from docscope.agent_run import AgentRunner


class AgentStopBudgetTest(unittest.TestCase):
    def test_exhausted_model_budget_does_not_generate_from_existing_evidence(self):
        generated = []
        runner = AgentRunner(
            retrieve=lambda query: [{"chunk_id": "first", "chunk_text": "Known fact."}],
            generate=lambda question, evidence: generated.append(evidence),
            validate=lambda raw, evidence: raw,
            sources_current=lambda evidence: True,
            extra_model_requests=0,
        )
        state = runner._initial_state("What is known?")
        state["calls"]["model"] = state["model_request_budget"]
        result = runner.graph.invoke(state)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "model_budget_exhausted")
        self.assertEqual(result["calls"], {"retrieval": 1, "model": 2})
        self.assertEqual(result["retrieved_evidence_ids"], ["first"])
        self.assertEqual(generated, [])

    def test_stopped_retrieval_does_not_publish_changed_source(self):
        runner = AgentRunner(
            retrieve=lambda query: [{"chunk_id": "first", "chunk_text": "Known fact."}],
            generate=lambda question, evidence: {
                "status": "answered", "answer": "Known fact.", "citation_ids": ["first"]},
            validate=lambda raw, evidence: {
                "status": "answered", "answer": raw["answer"],
                "citations": [{"chunk_id": "first"}]},
            sources_current=lambda evidence: False,
            plan_next=lambda question, evidence: {
                "evidence_status": "missing", "missing_fact": "unknown detail",
                "next_query": None, "bridge_text": None, "source_chunk_id": None},
            extra_model_requests=0,
        )
        result = runner.run("What is known?")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "source_changed")
        self.assertEqual(result["retrieval_stop_reason"], "missing_fact_unresolved")
        self.assertEqual(result["answer"], None)
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["calls"], {"retrieval": 1, "model": 2})


if __name__ == "__main__":
    unittest.main()
