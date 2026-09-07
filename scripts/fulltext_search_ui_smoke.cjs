// Synthetic long library; no user data or real search/embedding requests.
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const books = Array.from({ length: 40 }, (_, i) => ({ id: i + 1, title: `虚构资料 ${i + 1}`, file_type: 'pdf', status: 'ready', reading_status: 'unread', total_pages: 8, shelf_ids: [] }))
async function main() {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  const errors = [], queries = []
  try {
    const context = await browser.newContext()
    await context.addInitScript(() => localStorage.setItem('aiKeyGuideSeen', 'true'))
    await context.route('**/api/**', async route => {
      const url = new URL(route.request().url())
      if (!url.pathname.startsWith('/api/')) return route.continue()
      let data = {}
      if (url.pathname === '/api/books') data = { items: books, total: 40, stats: { total: 40 } }
      else if (url.pathname === '/api/shelves') data = []
      else if (url.pathname === '/api/tasks') data = { items: [] }
      else if (url.pathname === '/api/settings') data = { text_provider_configured: true }
      else if (url.pathname === '/api/search') {
        queries.push(url.searchParams.get('q'))
        data = { total: 1, items: [{ chunk_id: 101, book_id: 1, book_title: '虚构检索命中', page_start: 2, snippet: '这是<mark>核验</mark>结果。' }] }
      }
      await route.fulfill({ json: data })
    })
    const page = await context.newPage()
    page.on('pageerror', e => errors.push(e.message))
    for (const width of [1440, 900, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto((process.argv[2] || 'http://127.0.0.1:5177') + '/library')
      await page.locator('.paper-row').nth(39).waitFor({ state: 'attached' })
      const trigger = page.getByRole('button', { name: '全文检索', exact: true })
      await trigger.click()
      const dialog = page.getByRole('dialog', { name: '全文检索', exact: true })
      await dialog.waitFor({ state: 'visible' })
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '全文检索关键词')
      const geometry = await dialog.boundingBox()
      assert.ok(geometry.x >= 0 && geometry.x + geometry.width <= width + 1)
      assert.ok(geometry.y >= 0 && geometry.y + geometry.height <= 900)
      assert.equal(await dialog.evaluate(el => !!el.closest('.library-main')), false, 'Dialog must escape the long library and card stacking contexts')
      await dialog.getByLabel('全文检索关键词').fill('核验')
      await dialog.getByLabel('全文检索关键词').press('Enter')
      await dialog.getByText('共 1 条结果').waitFor()
      await dialog.getByText('《虚构检索命中》').waitFor()
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '全文检索')
      await trigger.press('Enter')
      await dialog.waitFor({ state: 'visible' })
      await dialog.getByText('共 1 条结果').waitFor()
      await dialog.locator('.el-dialog__headerbtn').click()
      await dialog.waitFor({ state: 'hidden' })
    }
    assert.deepEqual(queries, ['核验', '核验', '核验', '核验'])
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ widths: [1440, 900, 390, 320], longLibrary: 40, openFocusSearchCloseReopen: 'passed', errors }))
  } finally { await browser.close() }
}
main().catch(e => { console.error(e); process.exitCode = 1 })
