import { expect, test } from '@playwright/test'

const citation = {
  document_id: 'doc-1', chunk_id: 'chunk-2', document_title: 'polaris-relay.md',
  source_path: 'documents/doc-1.md', page_number: null, section: 'Polaris Relay',
  line_start: 3, chunk_text: 'Polaris Relay is operated by East Region Operations.',
}

const ids = { run_id: 'run-agent-1', message_id: 'message-1' }
const wire = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify({ ...ids, ...data })}\n\n`
const stage = (name: string, extra: object = {}) => wire('stage', { stage: name, status: 'completed', ...extra })

test.beforeEach(async ({ page }) => {
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: [] } }))
  await page.route('**/api/documents/*/citations/*', route => route.fulfill({ json: { status: 'valid' } }))
})

test('keyboard Agent run shows query, two retrieval rounds, answer, citation and stop reason', async ({ page }) => {
  await page.route('**/api/agent/run/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: stage('plan', { query: 'Which team operates Polaris Relay?' })
      + stage('retrieve', { round: 1, query: 'Which team operates Polaris Relay?', evidence_ids: ['chunk-1'] })
      + stage('plan_next', { next_query: 'Polaris Relay operator', evidence_status: 'missing' })
      + stage('retrieve', { round: 2, query: 'Polaris Relay operator', new_evidence_ids: ['chunk-2'] })
      + stage('answer')
      + wire('token', { text: 'East Region ' }) + wire('token', { text: 'Operations.' })
      + wire('citation', { citation })
      + wire('done', { status: 'answered', answer: 'East Region Operations.',
        citation_ids: ['chunk-2'], stop_reason: 'answered' }),
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('Which team operates Polaris Relay?')
  const button = page.getByRole('button', { name: 'Agent 流式回答' })
  await button.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('East Region Operations.')).toBeVisible()
  await expect(button).toBeFocused()
  await expect(page.getByText('查询：Polaris Relay operator').first()).toBeVisible()
  await expect(page.getByText('第 2 轮检索完成')).toBeVisible()
  await expect(page.getByText('停止原因：已回答')).toBeVisible()
  await expect(page.getByText('run-agent-1')).toBeVisible()
  const source = page.locator('summary').filter({ hasText: '查看来源 1' })
  await source.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText(citation.chunk_text)).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'East Region Operations.' })).toHaveCount(1)
})

test('captured error never displays provisional answer as success', async ({ page }) => {
  await page.route('**/api/agent/run/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: wire('stage', { stage: 'retrieve', status: 'failed', round: 1, query: 'test' })
      + wire('token', { text: 'Provisional text' })
      + wire('error', { error_code: 'model_failed' }),
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('alert')).toContainText('模型服务调用失败')
  await expect(page.getByText('第 1 轮检索失败')).toBeVisible()
  await expect(page.getByText('Provisional text')).toHaveCount(0)
  await expect(page.getByText('停止原因：已回答')).toHaveCount(0)
})

test('connection ending without terminal event is not marked successful', async ({ page }) => {
  await page.route('**/api/agent/run/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: stage('retrieve', { round: 1, query: 'test' }) + wire('token', { text: 'Incomplete answer' }),
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('alert')).toContainText('连接中断')
  await expect(page.getByText('Incomplete answer')).toHaveCount(0)
})

test('network reset after a partial answer is reported as interruption', async ({ page }) => {
  await page.addInitScript(({ prefix }) => {
    const original = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/agent/run/stream')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(prefix))
            window.setTimeout(() => controller.error(new TypeError('network reset')), 0)
          },
        })
        return Promise.resolve(new Response(body, { headers: { 'content-type': 'text/event-stream' } }))
      }
      return original(input, init)
    }
  }, { prefix: stage('retrieve', { round: 1, query: 'test' }) + wire('token', { text: 'Partial' }) })
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('alert')).toContainText('连接中断')
  await expect(page.getByText('Partial')).toHaveCount(0)
})

test('refusal has a completed stop reason without an answer', async ({ page }) => {
  await page.route('**/api/agent/run/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: stage('plan', { query: 'No evidence question' })
      + stage('retrieve', { round: 1, query: 'No evidence question', evidence_ids: [] })
      + wire('done', { status: 'insufficient_evidence', answer: null,
        citation_ids: [], stop_reason: 'no_evidence' }),
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('No evidence question')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByText('当前文档没有足够证据回答这个问题。')).toBeVisible()
  await expect(page.getByText('停止原因：未找到证据')).toBeVisible()
})

test('changed run identity is rejected rather than merged into an answer', async ({ page }) => {
  await page.route('**/api/agent/run/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: stage('plan', { query: 'test' }) + wire('token', { text: 'Fake answer' })
      + wire('done', { run_id: 'different-run', status: 'answered', answer: 'Fake answer',
        citation_ids: ['chunk-2'], stop_reason: 'answered' }),
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('alert')).toContainText('格式异常')
  await expect(page.getByText('Fake answer')).toHaveCount(0)
})

test('partial text is silent to live regions until the terminal event', async ({ page }) => {
  await page.addInitScript(({ prefix, suffix }) => {
    const original = window.fetch.bind(window)
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/api/agent/run/stream')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(prefix))
            Object.assign(window, { completeAgentStream: () => {
              controller.enqueue(new TextEncoder().encode(suffix))
              controller.close()
            } })
          },
        })
        return Promise.resolve(new Response(body, { headers: { 'content-type': 'text/event-stream' } }))
      }
      return original(input, init)
    }
  }, {
    prefix: stage('retrieve', { round: 1, query: 'test', evidence_ids: ['chunk-2'] })
      + wire('token', { text: 'East Region ' }),
    suffix: wire('token', { text: 'Operations.' }) + wire('citation', { citation })
      + wire('done', { status: 'answered', answer: 'East Region Operations.',
        citation_ids: ['chunk-2'], stop_reason: 'answered' }),
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.locator('[aria-live="off"]')).toContainText('East Region ')
  await expect(page.getByRole('status')).toContainText('检索完成')
  await expect(page.getByText('East Region Operations.')).toHaveCount(0)
  await page.evaluate(() => (window as typeof window & { completeAgentStream: () => void }).completeAgentStream())
  await expect(page.getByRole('status').filter({ hasText: 'East Region Operations.' })).toHaveCount(1)
})
