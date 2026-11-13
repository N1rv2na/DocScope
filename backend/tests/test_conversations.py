"""DOCS-017 session API, durable history, and per-turn graph isolation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from backend.tests.test_agent_stream import events
from backend.tests.test_markdown_dense_api import FakeEmbedder
from backend.tests.test_second_hop import SAMPLE, SAMPLE_DIR, TwoHopIndex, EvidencePlanner
from docscope.api import create_app


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = TwoHopIndex()
        self.llm = EvidencePlanner()
        self.client = self.make_client()
        for name in (SAMPLE['first_source'], SAMPLE['second_source']):
            response = self.client.post('/api/documents', files={
                'file': (name, (SAMPLE_DIR / name).read_bytes())})
            self.assertEqual(response.status_code, 201)

    def make_client(self):
        client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                       index=self.index, llm=self.llm))
        self.addCleanup(client.close)
        return client

    def create(self):
        response = self.client.post('/api/conversations')
        self.assertEqual(response.status_code, 201)
        return response.json()['thread_id']

    def run_turn(self, thread_id, question=SAMPLE['question']):
        response = self.client.post(f'/api/conversations/{thread_id}/runs/stream',
                                    json={'question': question})
        self.assertEqual(response.status_code, 200)
        wire = events(response)
        self.assertEqual(wire[-1][0], 'done')
        self.assertTrue(all(payload['thread_id'] == thread_id for _, payload in wire))
        return wire

    def test_create_switch_reopen_and_isolated_history(self):
        first, second = self.create(), self.create()
        self.run_turn(first)
        self.assertEqual(self.client.get(f'/api/conversations/{second}').json()['messages'], [])
        self.run_turn(second)
        self.assertEqual(len(self.client.get(f'/api/conversations/{first}').json()['messages']), 2)
        self.assertEqual(len(self.client.get(f'/api/conversations/{second}').json()['messages']), 2)
        reopened = self.make_client()
        first_history = reopened.get(f'/api/conversations/{first}').json()['messages']
        self.assertEqual([item['role'] for item in first_history], ['user', 'assistant'])
        self.assertEqual(first_history[0]['content'], SAMPLE['question'])
        self.assertEqual(first_history[1]['content'], SAMPLE['answer'])
        self.assertEqual(len(first_history[1]['citations']), 2)
        self.assertEqual(len(reopened.get('/api/conversations').json()['conversations']), 2)
        reopened_wire = events(reopened.post(f'/api/conversations/{first}/runs/stream',
                                            json={'question': SAMPLE['question']}))
        self.assertEqual(reopened_wire[-1][0], 'done')
        self.assertEqual([payload['round'] for kind, payload in reopened_wire
                          if kind == 'stage' and payload['stage'] == 'retrieve'], [1, 2])

    def test_new_turn_resets_graph_retrieval_state(self):
        thread = self.create()
        first = self.run_turn(thread)
        next_question = 'Who runs the relay used by the telemetry console?'
        second = self.run_turn(thread, next_question)
        for wire in (first, second):
            retrieves = [payload for kind, payload in wire if kind == 'stage' and payload['stage'] == 'retrieve']
            self.assertEqual([step['round'] for step in retrieves], [1, 2])
            self.assertEqual(wire[-1][1]['status'], 'answered')
        history = self.client.get(f'/api/conversations/{thread}').json()['messages']
        self.assertEqual(len(history), 4)
        self.assertEqual(history[2]['content'], next_question)
        self.assertEqual(second[0][1]['query'], next_question)
        with SqliteSaver.from_conn_string(str(self.root / 'checkpoints.sqlite3')) as saver:
            state = saver.get_tuple({'configurable': {'thread_id': thread}})
        self.assertIsNotNone(state)
        self.assertEqual(state.checkpoint['channel_values']['retrieval_round'], 2)
        self.assertEqual(state.checkpoint['channel_values']['run_id'], second[-1][1]['run_id'])

    def test_document_removal_does_not_reuse_previous_turn_evidence(self):
        thread = self.create()
        self.run_turn(thread)
        for item in self.client.get('/api/documents').json()['documents']:
            self.assertEqual(self.client.delete(f"/api/documents/{item['document_id']}").status_code, 200)
        wire = self.run_turn(thread)
        self.assertEqual(wire[-1][1]['status'], 'insufficient_evidence')
        self.assertEqual(wire[-1][1]['stop_reason'], 'no_evidence')
        history = self.client.get(f'/api/conversations/{thread}').json()['messages']
        self.assertEqual(history[-1]['citations'], [])

    def test_delete_cleans_messages_checkpoint_and_rejects_unknown_session(self):
        thread = self.create()
        self.run_turn(thread)
        self.assertEqual(self.client.delete(f'/api/conversations/{thread}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/conversations/{thread}').status_code, 404)
        self.assertEqual(self.client.post(f'/api/conversations/{thread}/runs/stream',
                                          json={'question': SAMPLE['question']}).status_code, 404)
        with SqliteSaver.from_conn_string(str(self.root / 'checkpoints.sqlite3')) as saver:
            self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': thread}}))
        self.assertEqual(self.client.delete(f'/api/conversations/{thread}').status_code, 404)

    def test_failed_turn_does_not_publish_history(self):
        thread = self.create()
        self.llm.proposal = lambda *_: (_ for _ in ()).throw(RuntimeError('unavailable'))
        wire = events(self.client.post(f'/api/conversations/{thread}/runs/stream',
                                       json={'question': SAMPLE['question']}))
        self.assertEqual(wire[-1][0], 'error')
        self.assertEqual(self.client.get(f'/api/conversations/{thread}').json()['messages'], [])

    def test_empty_session_delete_and_interrupted_cleanup_can_be_retried(self):
        empty = self.create()
        self.assertEqual(self.client.delete(f'/api/conversations/{empty}').status_code, 200)
        thread = self.create()
        self.run_turn(thread)
        with patch('langgraph.checkpoint.sqlite.SqliteSaver.delete_thread', side_effect=OSError('disk')):
            self.assertEqual(self.client.delete(f'/api/conversations/{thread}').status_code, 503)
        self.assertEqual(self.client.get(f'/api/conversations/{thread}').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/conversations/{thread}').status_code, 200)
        with SqliteSaver.from_conn_string(str(self.root / 'checkpoints.sqlite3')) as saver:
            self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': thread}}))

    def test_pending_delete_finishes_on_reopen(self):
        thread = self.create()
        self.run_turn(thread)
        with patch('langgraph.checkpoint.sqlite.SqliteSaver.delete_thread', side_effect=OSError('disk')):
            self.assertEqual(self.client.delete(f'/api/conversations/{thread}').status_code, 503)
        reopened = self.make_client()
        self.assertEqual(reopened.get('/api/conversations').json()['conversations'], [])
        with SqliteSaver.from_conn_string(str(self.root / 'checkpoints.sqlite3')) as saver:
            self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': thread}}))


if __name__ == '__main__':
    unittest.main()
