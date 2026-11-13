import { expect, test } from '@playwright/test'

test('create, switch, reopen and delete isolated conversation history', async ({ page }) => {
  const records: { thread_id: string; created_at: string; updated_at: string }[] = []
  const messages: Record<string, object[]> = {}
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: [] } }))
  await page.route('**/api/conversations**', async route => {
    const url = new URL(route.request().url())
    const segments = url.pathname.split('/').filter(Boolean)
    const id = segments[2]
    if (route.request().method() === 'POST' && !id) {
      const thread_id = `thread-${records.length + 1}`
      const item = { thread_id, created_at: '2026-11-13', updated_at: '2026-11-13' }
      records.push(item)
      messages[thread_id] = []
      return route.fulfill({ status: 201, json: item })
    }
    if (route.request().method() === 'DELETE' && id) {
      records.splice(records.findIndex(item => item.thread_id === id), 1)
      delete messages[id]
      return route.fulfill({ json: { thread_id: id, status: 'deleted' } })
    }
    if (route.request().method() === 'GET' && id) {
      return route.fulfill({ json: { ...records.find(item => item.thread_id === id), messages: messages[id] } })
    }
    return route.fulfill({ json: { conversations: records } })
  })
  await page.route('**/api/conversations/*/runs/stream', route => {
    const id = new URL(route.request().url()).pathname.split('/')[3]
    const question = JSON.parse(route.request().postData() ?? '{}').question as string
    messages[id].push({ message_id: `${id}-user`, run_id: `${id}-run`, role: 'user', content: question, status: null, citations: [] })
    messages[id].push({ message_id: `${id}-assistant`, run_id: `${id}-run`, role: 'assistant', content: `Answer ${id}`, status: 'answered', citations: [{ document_id: 'doc', chunk_id: 'chunk', document_title: 'source.md', source_path: 'documents/doc.md', chunk_text: 'Evidence.' }] })
    const base = { run_id: `${id}-run`, message_id: `${id}-message`, thread_id: id }
    const wire = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify({ ...base, ...data })}\n\n`
    return route.fulfill({ contentType: 'text/event-stream', body:
      wire('stage', { stage: 'retrieve', status: 'completed', round: 1 })
      + wire('token', { text: `Answer ${id}` })
      + wire('citation', { citation: (messages[id][1] as { citations: object[] }).citations[0] })
      + wire('done', { status: 'answered', answer: `Answer ${id}`, citation_ids: ['chunk'], stop_reason: 'answered' }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByRole('textbox', { name: '英文问题或追问' }).fill('Question one?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('region', { name: '会话历史' })).toContainText('Question one?')
  await page.getByRole('button', { name: '新建会话' }).click()
  await expect(page.getByRole('region', { name: '会话历史' })).not.toContainText('Question one?')
  await page.getByRole('button', { name: '会话 thread-1', exact: true }).click()
  await expect(page.getByRole('region', { name: '会话历史' })).toContainText('Answer thread-1')
  await page.reload()
  await expect(page.getByRole('region', { name: '会话历史' })).toContainText('Question one?')
  await page.getByRole('button', { name: '删除会话 thread-1' }).click()
  await expect(page.getByRole('button', { name: '会话 thread-1', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: '会话历史' })).toHaveCount(0)
})

test('rewrite failure is visible without a completed answer in session', async ({ page }) => {
  const thread = { thread_id: 'thread-rewrite', created_at: '2026-11-13', updated_at: '2026-11-13' }
  await page.addInitScript(() => window.localStorage.setItem('docscope-thread-id', 'thread-rewrite'))
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: [] } }))
  await page.route('**/api/conversations', route => route.fulfill({ json: { conversations: [thread] } }))
  await page.route('**/api/conversations/thread-rewrite', route => route.fulfill({ json: {
    ...thread, messages: [{ message_id: 'user-1', run_id: 'run-1', role: 'user',
      content: 'Which observatory built Orion?', status: null, citations: [] },
    { message_id: 'assistant-1', run_id: 'run-1', role: 'assistant',
      content: 'North Observatory', status: 'answered', citations: [] }],
  } }))
  await page.route('**/api/conversations/thread-rewrite/runs/stream', route => route.fulfill({
    contentType: 'text/event-stream', body:
      `event: stage\ndata: ${JSON.stringify({ run_id: 'run-2', message_id: 'msg-2',
        thread_id: 'thread-rewrite', stage: 'rewrite', status: 'failed' })}\n\n`
      + `event: error\ndata: ${JSON.stringify({ run_id: 'run-2', message_id: 'msg-2',
        thread_id: 'thread-rewrite', error_code: 'query_rewrite_failed' })}\n\n`,
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '英文问题或追问' }).fill('When was it built?')
  await page.getByRole('button', { name: 'Agent 流式回答' }).click()
  await expect(page.getByRole('alert')).toContainText('追问改写失败')
  await expect(page.getByRole('region', { name: '会话历史' })).not.toContainText('When was it built?')
})
