import { expect, test } from '@playwright/test'

test('replace and delete preserve the answer but invalidate its old citation', async ({ page }) => {
  let version = 1
  let deleted = false
  let replacementRequests = 0
  const record = () => ({ document_id: 'doc-1', filename: 'notes.md', status: 'ready', chunk_count: 1, index_version: version })
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: deleted ? [] : [record()] } }))
  await page.route('**/api/documents/doc-1', route => {
    if (route.request().method() === 'PUT') {
      replacementRequests += 1
      version += 1
      return route.fulfill({ json: record() })
    }
    deleted = true
    return route.fulfill({ json: { document_id: 'doc-1', status: 'deleted' } })
  })
  await page.route('**/api/documents/doc-1/citations/*', route => route.fulfill({
    json: { status: !deleted && route.request().url().endsWith(`chunk-${version}`) ? 'valid' : 'invalid' },
  }))
  await page.route('**/api/chat', route => route.fulfill({ json: {
    status: 'answered', run_id: `run-${version}`, answer: version === 1 ? 'North Observatory.' : 'East Observatory.',
    citations: [{ document_id: 'doc-1', chunk_id: `chunk-${version}`, document_title: 'notes.md', section: 'Notes', line_start: 2,
      chunk_text: version === 1 ? 'Orion belongs to North Observatory.' : 'Orion belongs to East Observatory.' }],
  } }))
  await page.goto('/')
  await expect(page.getByRole('list', { name: '文档列表' })).toContainText('notes.md')
  await page.getByRole('textbox', { name: '独立英文问题' }).fill('Who owns Orion?')
  await page.getByRole('button', { name: '检索并回答' }).click()
  await page.locator('summary').click()
  await expect(page.getByText('Orion belongs to North Observatory.')).toBeVisible()

  await page.getByRole('button', { name: '替换 notes.md', exact: true }).click()
  await page.getByLabel('PDF 或 Markdown 文件').setInputFiles({ name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes\nOrion belongs to East Observatory.') })
  await page.getByRole('button', { name: '上传并替换', exact: true }).click()
  await expect(page.getByText('引用已失效', { exact: false })).toBeVisible()
  await expect(page.getByText('North Observatory.', { exact: true })).toBeVisible()
  await expect(page.getByText('Orion belongs to North Observatory.')).toHaveCount(0)
  expect(replacementRequests).toBe(1)

  await page.getByRole('button', { name: '检索并回答' }).click()
  await page.locator('summary').click()
  await expect(page.getByText('Orion belongs to East Observatory.')).toBeVisible()
  const remove = page.getByRole('button', { name: '删除 notes.md', exact: true })
  await remove.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('尚未导入文档')).toBeVisible()
  await expect(page.getByText('引用已失效', { exact: false })).toBeVisible()
  await expect(page.getByText('Orion belongs to East Observatory.')).toHaveCount(0)
})

test('failed delete is visible and the user can retry', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/documents', route => route.fulfill({ json: { documents: attempts >= 2 ? [] : [{
    document_id: 'doc-failed', filename: 'notes.md', status: attempts ? 'failed' : 'ready', error_code: attempts ? 'delete_failed' : null,
  }] } }))
  await page.route('**/api/documents/doc-failed', route => {
    attempts += 1
    return route.fulfill({ status: attempts === 1 ? 503 : 200, json: {
      document_id: 'doc-failed', status: attempts === 1 ? 'failed' : 'deleted', error_code: 'delete_failed',
    } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '删除 notes.md', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('删除失败')
  await expect(page.getByRole('list', { name: '文档列表' })).toContainText('失败')
  await page.getByRole('button', { name: '删除 notes.md', exact: true }).click()
  await expect(page.getByText('尚未导入文档')).toBeVisible()
})
