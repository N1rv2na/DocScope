"""DOCS-019 summary persistence, bounded context, and failure recovery."""

import tempfile
import unittest
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_agent_stream import events
from backend.tests.test_markdown_dense_api import FakeEmbedder, SAMPLE
from backend.tests.test_memory_rewrite import TopicIndex, RewritingResponder
from docscope.api import create_app
from docscope.conversations import ConversationStore
from docscope.memory import summary_batch, validated_summary
from docscope.llm import OpenAIResponder


class SummaryResponder(RewritingResponder):
    def __init__(self):
        super().__init__()
        self.summary_calls = []
        self.fail_summary = False
        self.invalid_summary = False

    def summarize(self, prior_summary, older_turns):
        self.summary_calls.append((prior_summary, older_turns))
        if self.fail_summary:
            raise TimeoutError("summary unavailable")
        if self.invalid_summary:
            return {"memory_summary": "X" * 1300}
        return {"memory_summary": "Earlier topic: Orion telescope; later topic: Vega telescope."}

    def rewrite_question(self, question, window, summary=None):
        self.windows.append((question, window, summary))
        if question == "When was the first telescope we discussed built?":
            return {"standalone_question": "When was the Orion telescope built?" if summary
                    else "When was the first telescope we discussed built?"}
        return super().rewrite_question(question, window)


class SummaryMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = TopicIndex()
        self.responder = SummaryResponder()
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                            index=self.index, llm=self.responder))
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post('/api/documents', files={
            'file': ('telescopes.md', SAMPLE, 'text/markdown')}).status_code, 201)
        self.thread = self.client.post('/api/conversations').json()['thread_id']
        self.store = ConversationStore(self.root)

    def seed(self, count=6):
        for number in range(count):
            subject = "Orion" if number == 0 else "Vega"
            self.store.append_turn(self.thread,
                                   f"Which observatory built the {subject} telescope? " + "a" * 280,
                                   {'run_id': f'run-{number}', 'status': 'answered',
                                    'answer': 'North Observatory' if number == 0 else 'South Observatory',
                                    'citations': []})

    def turn(self, question):
        return events(self.client.post(f'/api/conversations/{self.thread}/runs/stream',
                                       json={'question': question}))

    def test_summary_is_saved_incrementally_and_used_only_for_rewrite(self):
        self.seed()
        wire = self.turn("When was the first telescope we discussed built?")
        self.assertEqual(wire[-1][0], 'done')
        self.assertEqual(wire[-1][1]['answer'], '2022')
        self.assertEqual(self.index.queries[-1], "When was the Orion telescope built?")
        self.assertEqual(self.responder.windows[-1][2],
                         "Earlier topic: Orion telescope; later topic: Vega telescope.")
        self.assertEqual(len(wire[-1][1]['citation_ids']), 1)
        saved = self.store.get_summary(self.thread)
        self.assertEqual(saved['summarized_turns'], 2)
        self.assertEqual(ConversationStore(self.root).get_summary(self.thread), saved)
        self.assertEqual(len(self.responder.summary_calls), 1)
        self.assertEqual(len(self.responder.summary_calls[0][1]), 2)
        self.assertTrue(all('answer' not in item for item in self.responder.summary_calls[0][1]))
        self.assertEqual(self.turn("Which observatory built the Vega telescope?")[-1][0], 'done')
        self.assertEqual(len(self.responder.summary_calls), 1)
        self.assertEqual(self.store.get_summary(self.thread)['summarized_turns'], 2)

    def test_failure_keeps_messages_and_falls_back_to_window_then_retries(self):
        self.seed()
        self.responder.fail_summary = True
        before = self.client.get(f'/api/conversations/{self.thread}').json()['messages']
        wire = self.turn("Which observatory built the Vega telescope?")
        self.assertEqual(wire[0][1]['stage'], 'summary')
        self.assertEqual(wire[0][1]['status'], 'failed')
        self.assertEqual(wire[-1][0], 'done')
        self.assertIsNone(self.store.get_summary(self.thread))
        self.assertEqual(len(self.client.get(f'/api/conversations/{self.thread}').json()['messages']),
                         len(before) + 2)
        self.responder.fail_summary = False
        self.turn("When was the first telescope we discussed built?")
        self.assertIsNotNone(self.store.get_summary(self.thread))
        self.assertEqual(self.store.get_summary(self.thread)['summarized_turns'], 3)

    def test_invalid_summary_never_persists_and_deletion_clears_state(self):
        self.seed()
        self.responder.invalid_summary = True
        wire = self.turn("Which observatory built the Vega telescope?")
        self.assertEqual(wire[0][1]['status'], 'failed')
        self.assertIsNone(self.store.get_summary(self.thread))
        self.responder.invalid_summary = False
        self.turn("Which observatory built the Vega telescope?")
        self.assertIsNotNone(self.store.get_summary(self.thread))
        self.assertEqual(self.client.delete(f'/api/conversations/{self.thread}').status_code, 200)
        self.assertIsNone(self.store.get_summary(self.thread))
        self.assertIsNone(self.store.get(self.thread))
        with sqlite3.connect(self.root / 'conversations.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM conversation_summaries').fetchone()[0], 0)

    def test_saved_summary_is_used_when_recent_window_is_empty(self):
        self.store.append_turn(self.thread, '😀' * 500, {
            'run_id': 'long-run', 'status': 'answered', 'answer': '😀' * 1000,
            'citations': []})
        self.store.save_summary(self.thread, 'Earlier topic: Orion telescope.', 1)
        wire = self.turn('When was the first telescope we discussed built?')
        self.assertEqual(wire[-1][0], 'done')
        self.assertEqual(self.index.queries[-1], 'When was the Orion telescope built?')
        self.assertEqual(self.responder.windows[-1][1], [])
        self.assertEqual(self.responder.windows[-1][2], 'Earlier topic: Orion telescope.')

    def test_new_older_questions_incrementally_update_the_saved_summary(self):
        self.seed()
        self.turn('Which observatory built the Vega telescope?')
        first = self.store.get_summary(self.thread)
        for number in range(4):
            self.store.append_turn(self.thread, 'When was the Vega telescope built? ' + 'b' * 300,
                                   {'run_id': f'additional-{number}', 'status': 'answered',
                                    'answer': '2020', 'citations': []})
        self.turn('When was the first telescope we discussed built?')
        second = self.store.get_summary(self.thread)
        self.assertEqual(len(self.responder.summary_calls), 2)
        self.assertEqual(self.responder.summary_calls[1][0], first['summary_text'])
        self.assertGreater(second['summarized_turns'], first['summarized_turns'])


class SummaryBoundsTest(unittest.TestCase):
    def test_threshold_and_batch_bound(self):
        messages = [message for _ in range(30) for message in (
            {'role': 'user', 'content': 'Q' * 500},
            {'role': 'assistant', 'content': 'A' * 1000})]
        batch = summary_batch(messages, 0, None)
        self.assertTrue(batch)
        self.assertLess(len(batch), 26)
        self.assertTrue(all(set(item) == {'question'} for item in batch))
        self.assertIsNone(summary_batch(messages[:8], 0, None))
        short = [message for _ in range(45) for message in (
            {'role': 'user', 'content': 'Q'}, {'role': 'assistant', 'content': 'A'})]
        self.assertIsNone(summary_batch(short, 0, None))

    def test_summary_must_be_short_json(self):
        self.assertEqual(validated_summary({'memory_summary': 'Earlier topic: Orion.'}),
                         'Earlier topic: Orion.')
        for invalid in ({'memory_summary': 'X' * 1300}, {'memory_summary': ''},
                        {'memory_summary': 'Okay', 'answer': '2022'}, {'memory_summary': 42}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validated_summary(invalid)

    def test_summary_model_request_contains_questions_but_no_historical_answers(self):
        captured = []
        client = SimpleNamespace(close=lambda: None)
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: (
            captured.append(kwargs) or SimpleNamespace(
                model='test-model', usage=None,
                choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                    content='{"memory_summary":"Earlier topic: Orion telescope."}'))]))))
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:3425/v1',
                                     'LLM_API_KEY': 'test-local', 'LLM_MODEL': 'test-model'}), \
             patch('openai.OpenAI', return_value=client):
            raw = OpenAIResponder().summarize(None, [{'question': 'Which telescope was first?'}])
        self.assertEqual(validated_summary(raw), 'Earlier topic: Orion telescope.')
        request = captured[0]
        self.assertEqual(request['response_format'], {'type': 'json_object'})
        self.assertEqual(set(json.loads(request['messages'][1]['content'])),
                         {'prior_summary', 'older_questions'})
        self.assertNotIn('answer', request['messages'][1]['content'])


if __name__ == '__main__':
    unittest.main()
