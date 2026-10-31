import { expect, test, type Page } from '@playwright/test'

type UploadFixture = { status: number; json: { document_id: string; status: string; filename?: string; chunk_count?: number; error_code?: string } }

async function mockUploads(page: Page, respond: () => UploadFixture) {
  const documents = new Map<string, UploadFixture['json']>()
  await page.route('**/api/documents', route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { documents: [...documents.values()] } })
    const response = respond()
    const filename = route.request().postDataBuffer()?.toString().match(/filename="([^"]+)"/)?.[1]
    documents.set(response.json.document_id, { ...response.json, filename: response.json.filename ?? filename })
    return route.fulfill(response)
  })
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: [] } }))
  await page.route('**/api/documents/*/citations/*', route => route.fulfill({ json: { status: 'valid' } }))
})

const citation = {
  document_id: 'doc-1',
  chunk_id: 'chunk-orion',
  document_title: 'telescopes.md',
  source_path: 'documents/doc-1.md',
  section: 'Telescope Notes > Orion',
  line_start: 4,
  chunk_text: 'The Orion telescope was built in 2022 by the North Observatory.',
}

test('keyboard user uploads Markdown, asks, and opens the original citation', async ({ page }) => {
  await mockUploads(page, () => ({
    status: 201,
    json: { document_id: 'doc-1', status: 'ready', filename: 'telescopes.md', chunk_count: 2 },
  }))
  await page.route('**/api/chat', route => route.fulfill({
    json: { run_id: 'run-1', status: 'answered', answer: 'The North Observatory built it.', citations: [citation] },
  }))
  await page.goto('/')

  const fileInput = page.getByLabel('PDF 或 Markdown 文件')
  const fileChooser = page.waitForEvent('filechooser')
  await page.keyboard.press('Tab')
  await expect(fileInput).toBeFocused()
  await expect(fileInput).toHaveCSS('outline-style', 'solid')
  await page.keyboard.press('Enter')
  await (await fileChooser).setFiles('../backend/tests/fixtures/telescopes.md')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: '上传并建立索引' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByText('telescopes.md')).toBeVisible()
  await expect(page.getByText('索引就绪')).toBeVisible()
  await expect(page.getByRole('button', { name: '上传并建立索引' })).toBeFocused()

  const question = page.getByRole('textbox', { name: '独立英文问题' })
  for (let steps = 0; steps < 8 && !await question.evaluate(element => element === document.activeElement); steps++) await page.keyboard.press('Tab')
  await expect(question).toBeFocused()
  await page.keyboard.type('Which observatory built the Orion telescope?')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: '检索并回答' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByText('The North Observatory built it.')).toBeVisible()
  await expect(page.getByRole('button', { name: '检索并回答' })).toBeFocused()
  const source = page.locator('summary').filter({ hasText: '查看来源 1' })
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Agent 流式回答' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(source).toBeFocused()
  await expect(source).toContainText('telescopes.md')
  await expect(source).toContainText('Telescope Notes > Orion')
  await page.keyboard.press('Enter')
  await expect(page.getByText(citation.chunk_text)).toBeVisible()
  await expect(page.getByText('Telescope Notes > Orion · 第 4 行')).toBeVisible()
})

test('blank question, failed import, and failed model have different messages', async ({ page }) => {
  let uploadFails = true
  await mockUploads(page, () => (uploadFails ? {
    status: 503,
    json: { document_id: 'doc-failed', status: 'failed', error_code: 'index_failed' },
  } : {
    status: 201,
    json: { document_id: 'doc-ready', status: 'ready', filename: 'telescopes.md', chunk_count: 2 },
  }))
  await page.route('**/api/chat', route => route.fulfill({
    status: 503,
    json: { run_id: 'run-failed', status: 'error', error_code: 'model_failed', answer: null, citations: [] },
  }))
  await page.goto('/')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await expect(page.getByRole('alert')).toContainText('请输入问题')

  await page.getByLabel('PDF 或 Markdown 文件').setInputFiles('../backend/tests/fixtures/telescopes.md')
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByRole('alert')).toContainText('文档索引失败')
  await expect(page.getByLabel('PDF 或 Markdown 文件')).toHaveAttribute('aria-describedby', /upload-error/)

  uploadFails = false
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByText('索引就绪')).toBeVisible()
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('Which observatory built Orion?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await expect(page.getByRole('alert')).toContainText('模型服务调用失败')
  await expect(page.getByText('The North Observatory built it.')).toHaveCount(0)
})

test('an insufficient-evidence result is not displayed as a model failure', async ({ page }) => {
  await page.route('**/api/chat', route => route.fulfill({
    json: { run_id: 'run-2', status: 'insufficient_evidence', answer: null, citations: [] },
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What is the launch mass?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await expect(page.getByText('当前文档没有足够证据回答这个问题。')).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('a failed second import does not mark that file ready', async ({ page }) => {
  let uploadCount = 0
  await mockUploads(page, () => {
    uploadCount += 1
    return uploadCount === 1 ? {
      status: 201,
      json: { document_id: 'doc-ready', status: 'ready', filename: 'telescopes.md', chunk_count: 2 },
    } : {
      status: 503,
      json: { document_id: 'doc-failed', status: 'failed', error_code: 'parse_failed' },
    }
  })
  await page.goto('/')
  await page.getByLabel('PDF 或 Markdown 文件').setInputFiles('../backend/tests/fixtures/telescopes.md')
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByText('索引就绪')).toBeVisible()

  await page.getByLabel('PDF 或 Markdown 文件').setInputFiles({ name: 'headings.md', mimeType: 'text/markdown', buffer: Buffer.from('# No body') })
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByRole('alert')).toContainText('文档索引失败')
  const entries = page.getByRole('list', { name: '文档列表' }).getByRole('listitem')
  await expect(entries.filter({ hasText: 'headings.md' })).toContainText('失败')
  await expect(entries.filter({ hasText: 'headings.md' })).not.toContainText('索引就绪')
  await expect(entries.filter({ hasText: 'telescopes.md' })).toContainText('索引就绪')
})

test('context limit error is associated with the question field', async ({ page }) => {
  await page.route('**/api/chat', route => route.fulfill({
    status: 422,
    json: { status: 'error', error_code: 'context_limit_exceeded', run_id: 'run-long', answer: null, citations: [] },
  }))
  await page.goto('/')
  const question = page.getByRole('textbox', { name: '独立英文问题' })
  await question.fill('What happened?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await expect(page.getByRole('alert')).toContainText('超出本轮上下文限制')
  await expect(question).toHaveAttribute('aria-invalid', 'true')
  await expect(question).toHaveAttribute('aria-describedby', 'question-error')
})

test('long unbroken evidence remains readable on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/chat', route => route.fulfill({
    json: { run_id: 'run-long-chunk', status: 'answered', answer: 'A supported answer.', citations: [{ ...citation, chunk_text: 'X'.repeat(600) }] },
  }))
  await page.goto('/')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('What happened?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await page.locator('summary').click()
  await expect(page.locator('blockquote')).toContainText('X'.repeat(600))
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth }))
  expect(width.scroll).toBeLessThanOrEqual(width.viewport)
})

test('PDF upload and answer show the cited page instead of a Markdown section', async ({ page }) => {
  const pdfExcerpt = 'The Orion telescope was built in 2022 by the North Observatory.'
  await mockUploads(page, () => ({
    status: 201,
    json: { document_id: 'pdf-1', status: 'ready', filename: 'observatories.pdf', chunk_count: 2 },
  }))
  await page.route('**/api/chat', route => route.fulfill({
    json: { run_id: 'pdf-run', status: 'answered', answer: 'North Observatory built Orion.',
      citations: [{ ...citation, document_id: 'pdf-1', document_title: 'observatories.pdf',
        source_path: 'documents/pdf-1.pdf', section: null, line_start: null, page_number: 2,
        chunk_text: pdfExcerpt }] },
  }))
  await page.goto('/')
  const input = page.getByLabel('PDF 或 Markdown 文件')
  await expect(input).toHaveAttribute('accept', /\.pdf/)
  await input.setInputFiles('../backend/tests/fixtures/observatories.pdf')
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByText('observatories.pdf')).toBeVisible()
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('Which observatory built Orion?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  const source = page.locator('summary').filter({ hasText: '查看来源 1' })
  await expect(source).toContainText('第 2 页')
  await source.click()
  await expect(page.getByText('第 2 页', { exact: true })).toBeVisible()
  await expect(page.getByText('Telescope Notes > Orion · 第 4 行')).toHaveCount(0)
  await expect(page.getByText(pdfExcerpt)).toBeVisible()
})

test('an unparseable PDF is shown as a failed import', async ({ page }) => {
  await mockUploads(page, () => ({
    status: 503,
    json: { document_id: 'broken-pdf', status: 'failed', error_code: 'parse_failed' },
  }))
  await page.goto('/')
  await page.getByLabel('PDF 或 Markdown 文件').setInputFiles({
    name: 'broken.pdf', mimeType: 'application/pdf', buffer: Buffer.from('broken'),
  })
  await page.getByRole('button', { name: '上传并建立索引' }).click()
  await expect(page.getByRole('alert')).toContainText('PDF')
  await expect(page.getByText('索引就绪')).toHaveCount(0)
})
