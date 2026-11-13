"""DOCS-018: bounded conversation context changes retrieval intent, not facts."""

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.tests.test_agent_stream import events
from backend.tests.test_markdown_dense_api import FakeEmbedder, FakeIndex, SAMPLE
from docscope.api import create_app
from docscope.conversations import ConversationStore
from docscope.llm import OpenAIResponder


class TopicIndex(FakeIndex):
    def __init__(self):
        super().__init__()
        self.queries = []

    def query_hybrid(self, vector, text, ready_ids, limit, candidate_limit):
        self.queries.append(text)
        topic = 'Vega' if 'Vega' in text else 'Orion' if 'Orion' in text else None
        if topic is None:
            return []
        return [
            {'id': point['id'], 'score': 1.0, 'payload': point['payload']}
            for point in self.points
            if point['payload']['document_id'] in ready_ids
            and topic in (point['payload'].get('section') or '')
        ][:limit]


class RewritingResponder:
    def __init__(self):
        self.windows = []
        self.answers = []
        self.fail_rewrite = False
        self.invalid_rewrite = False

    def rewrite_question(self, question, window):
        self.windows.append((question, window))
        if self.fail_rewrite:
            raise TimeoutError('upstream timed out')
        if self.invalid_rewrite:
            return {'standalone_question': ' '}
        if question == 'When was it built?':
            entity = 'Vega' if 'Vega' in window[-1]['question'] else 'Orion'
            return {'standalone_question': f'When was the {entity} telescope built?'}
        return {'standalone_question': question}

    def plan_next(self, question, evidence):
        return {'evidence_status': 'sufficient', 'missing_fact': None,
                'next_query': None, 'bridge_text': None, 'source_chunk_id': None}

    def generate(self, question, evidence):
        self.answers.append((question, evidence))
        if not evidence:
            return {'status': 'insufficient_evidence', 'answer': None, 'citation_ids': []}
        text = evidence[0]['chunk_text']
        answer = '2020' if 'When' in question and 'Vega' in question else (
            '2022' if 'When' in question else
            'South Observatory' if 'Vega' in question else 'North Observatory')
        assert answer in text
        return {'status': 'answered', 'answer': answer,
                'citation_ids': [evidence[0]['chunk_id']]}


class MemoryRewriteApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = TopicIndex()
        self.llm = RewritingResponder()
        self.client = TestClient(create_app(data_dir=self.root, embedder=FakeEmbedder(),
                                            index=self.index, llm=self.llm))
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post('/api/documents', files={
            'file': ('telescopes.md', SAMPLE, 'text/markdown')}).status_code, 201)

    def create(self):
        return self.client.post('/api/conversations').json()['thread_id']

    def turn(self, thread, question):
        response = self.client.post(f'/api/conversations/{thread}/runs/stream', json={'question': question})
        self.assertEqual(response.status_code, 200)
        return events(response)

    def test_followup_rewrites_before_retrieval_and_cites_current_chunk(self):
        thread = self.create()
        self.assertEqual(self.turn(thread, 'Which observatory built the Orion telescope?')[-1][0], 'done')
        wire = self.turn(thread, 'When was it built?')
        self.assertEqual(wire[-1][0], 'done')
        self.assertEqual(wire[-1][1]['answer'], '2022')
        self.assertEqual(self.index.queries[-1], 'When was the Orion telescope built?')
        self.assertEqual(self.llm.answers[-1][0], 'When was the Orion telescope built?')
        self.assertEqual(wire[0][1]['stage'], 'rewrite')
        self.assertEqual(wire[0][1]['query'], 'When was the Orion telescope built?')
        self.assertEqual(wire[1][1]['query'], self.index.queries[-1])
        history = self.client.get(f'/api/conversations/{thread}').json()['messages']
        self.assertEqual(history[-2]['content'], 'When was it built?')
        self.assertIn('Orion', history[-1]['citations'][0]['section'])
        self.assertEqual(self.llm.windows[0][1], [{
            'question': 'Which observatory built the Orion telescope?',
            'answer': 'North Observatory'}])

    def test_topic_switch_and_followup_use_only_current_session(self):
        thread = self.create()
        self.turn(thread, 'Which observatory built the Orion telescope?')
        switched = self.turn(thread, 'Which observatory built the Vega telescope?')
        self.assertEqual(switched[-1][1]['answer'], 'South Observatory')
        self.assertEqual(self.index.queries[-1], 'Which observatory built the Vega telescope?')
        followup = self.turn(thread, 'When was it built?')
        self.assertEqual(followup[-1][1]['answer'], '2020')
        self.assertEqual(self.index.queries[-1], 'When was the Vega telescope built?')
        other = self.create()
        fresh = self.turn(other, 'When was it built?')
        self.assertEqual(fresh[-1][1]['status'], 'insufficient_evidence')
        self.assertEqual(self.index.queries[-1], 'When was it built?')
        self.assertEqual(len(self.llm.windows), 2)

    def test_recent_window_has_four_turn_and_byte_bounds(self):
        thread = self.create()
        store = ConversationStore(self.root)
        for number in range(6):
            store.append_turn(thread, f'Question {number}?', {
                'run_id': f'run-{number}', 'status': 'answered',
                'answer': f'Answer {number}.', 'citations': []})
        self.turn(thread, 'Which observatory built the Vega telescope?')
        window = self.llm.windows[-1][1]
        self.assertEqual(len(window), 4)
        self.assertEqual([item['question'] for item in window],
                         ['Question 2?', 'Question 3?', 'Question 4?', 'Question 5?'])

    def test_rewrite_failure_does_not_retrieve_or_persist_new_turn(self):
        thread = self.create()
        self.turn(thread, 'Which observatory built the Orion telescope?')
        previous_queries = list(self.index.queries)
        self.llm.fail_rewrite = True
        wire = self.turn(thread, 'When was it built?')
        self.assertEqual(wire[-1][0], 'error')
        self.assertEqual(wire[-1][1]['error_code'], 'query_rewrite_failed')
        self.assertEqual(self.index.queries, previous_queries)
        self.assertEqual(len(self.client.get(f'/api/conversations/{thread}').json()['messages']), 2)

    def test_invalid_rewrite_cannot_be_used_as_retrieval_query(self):
        thread = self.create()
        self.turn(thread, 'Which observatory built the Orion telescope?')
        previous_queries = list(self.index.queries)
        self.llm.invalid_rewrite = True
        wire = self.turn(thread, 'When was it built?')
        self.assertEqual(wire[-1][0], 'error')
        self.assertEqual(self.index.queries, previous_queries)

    def test_long_history_is_bounded_before_rewriter(self):
        thread = self.create()
        store = ConversationStore(self.root)
        for number in range(6):
            store.append_turn(thread, f'Question {number}?', {
                'run_id': f'run-{number}', 'status': 'answered',
                'answer': 'X' * 3000, 'citations': []})
        self.turn(thread, 'Which observatory built the Vega telescope?')
        window = self.llm.windows[-1][1]
        self.assertLessEqual(len(json.dumps(window, ensure_ascii=False).encode()), 6000)
        self.assertLessEqual(len(window), 4)
        self.assertTrue(all(len(item['answer']) <= 1000 for item in window))


class RewritePromptTest(unittest.TestCase):
    def test_openai_rewriter_only_receives_current_question_and_bounded_history(self):
        client = SimpleNamespace()
        client.close = lambda: None
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            model='test-model', usage=None,
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                content='{"standalone_question":"When was the Orion telescope built?"}'))])))
        with patch.dict('os.environ', {'LLM_BASE_URL': 'http://127.0.0.1:3425/v1',
                                     'LLM_API_KEY': 'test-local', 'LLM_MODEL': 'test-model'}), \
             patch('openai.OpenAI', return_value=client):
            with patch.object(client.chat.completions, 'create', wraps=client.chat.completions.create) as create:
                result = OpenAIResponder().rewrite_question('When was it built?', [
                    {'question': 'Which observatory built the Orion telescope?', 'answer': 'North Observatory'}])
        self.assertEqual(result['standalone_question'], 'When was the Orion telescope built?')
        request = create.call_args.kwargs
        self.assertEqual(request['response_format'], {'type': 'json_object'})
        user = json.loads(request['messages'][1]['content'])
        self.assertEqual(set(user), {'question', 'recent_turns'})
        self.assertEqual(user['recent_turns'][0]['answer'], 'North Observatory')
        self.assertNotIn('citation', request['messages'][1]['content'])


if __name__ == '__main__':
    unittest.main()
