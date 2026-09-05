/* Browser regression checks with synthetic data; no user API requests pass through.
 * NODE_PATH may point to the bundled Playwright node_modules directory.
 * node scripts/library_ui_smoke.cjs http://127.0.0.1:5177 [screenshot-directory]
 */
const assert = require('node:assert/strict')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const base = process.argv[2] || 'http://127.0.0.1:5177'
const screenshots = process.argv[3] && resolve(process.argv[3])
const books = Array.from({ length: 6 }, (_, index) => ({
  id: index + 1,
  title: ['示例文献 A：多维资源分配与研究方法的比较分析（虚构测试资料）', '示例文献 B：知识组织与证据核验的界面实验', '示例文献 C：跨学科材料的阅读与归档方法'][index % 3],
  authors: '虚构作者', journal: '界面回归测试', published_year: 2011 + index,
  file_type: 'pdf', total_pages: 7, status: 'ready', deep_status: 'pending',
  reading_status: index % 2 ? 'unread' : 'reading', category: index % 2 ? '哲学' : '法学',
  favorite: true, shelf_ids: [], publication_status: 'unknown',
}))

async function mockApi(route) {
  const url = new URL(route.request().url())
  // The source module /src/api/index.js must still be loaded normally on Vite.
  if (!url.pathname.startsWith('/api/')) return route.continue()
  const path = url.pathname
  let data = {}
  if (path === '/api/books') data = { items: books, total: books.length }
  else if (path === '/api/shelves') data = []
  else if (path === '/api/tasks') data = { items: [] }
  else if (/^\/api\/books\/\d+$/.test(path)) {
    const book = books.find(item => item.id === Number(path.split('/').at(-1)))
    data = { ...book, archive: { ...book }, chapters: [], analysis: { keywords: [] } }
  } else if (path === '/api/settings') data = { text_provider_configured: true }
  else if (path === '/api/settings/providers') data = { items: [], default_text_provider: 'deepseek', task_routes: {} }
  else if (path === '/api/settings/providers/usage') data = { items: [], totals: {}, boundary: '' }
  else if (path === '/api/settings/storage') data = { total_bytes: 0, items: [] }
  else if (path === '/api/settings/capacity') data = { tier: 'normal', books: 6, chunks: 12, database_bytes: 2048 }
  else if (path.endsWith('/probe')) data = { ok: true, reason: '测试连接' }
  return route.fulfill({ json: data })
}

const frame = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
const faceY = key => key.locator('.keycap-face').evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42)
async function settles(key, expected) {
  await key.page().waitForFunction(({ selector, expected }) => {
    const face = document.querySelector(selector + ' .keycap-face')
    return face && Math.abs(new DOMMatrixReadOnly(getComputedStyle(face).transform).m42 - expected) < .2
  }, { selector: '.appearance-preview .keycap-card', expected }, { timeout: 3000 })
}

async function main() {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  const errors = []
  const results = []
  try {
    const context = await browser.newContext()
    await context.addInitScript(() => localStorage.setItem('aiKeyGuideSeen', 'true'))
    await context.route('**/api/**', mockApi)
    const page = await context.newPage()
    page.setDefaultTimeout(7000)
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })

    for (const collapsed of [false, true]) {
      await page.goto(base + '/library', { waitUntil: 'domcontentloaded' })
      await page.evaluate(collapsed => localStorage.setItem('sidebarCollapsed', String(collapsed)), collapsed)
      await page.reload({ waitUntil: 'domcontentloaded' })
      await page.locator('.paper-more').first().waitFor()
      await page.locator('.library-main .el-loading-mask').waitFor({ state: 'hidden' })
      for (const width of [1920, 1600, 1520, 1440, 1366, 1280, 1100, 900, 820, 768, 390, 320]) {
        await page.setViewportSize({ width, height: 900 })
        await frame(page)
        const more = page.locator('.paper-more').first()
        await more.scrollIntoViewIfNeeded()
        const layout = await page.evaluate(() => {
          const selectors = ['.main', '.library-main', '.library-filters', '.paper-row', '.library-commandbar']
          const overflow = selectors.filter(selector => {
            const el = document.querySelector(selector)
            return el.scrollWidth > el.clientWidth + 2
          })
          const button = document.querySelector('.paper-more')
          const box = button.getBoundingClientRect()
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
          const main = document.querySelector('.library-main').getBoundingClientRect()
          return { overflow, hit: button.contains(hit), inside: box.left >= main.left && box.right <= main.right, hitElement: hit?.className, button: box.toJSON(), main: main.toJSON() }
        })
        assert.deepEqual(layout.overflow, [], `Overflow at ${width}px, sidebar collapsed=${collapsed}`)
        assert.ok(layout.hit && layout.inside, `More is clipped at ${width}px, collapsed=${collapsed}: ${JSON.stringify(layout)}`)
        await more.click()
        const detail = page.getByRole('menuitem', { name: '资料详情', exact: true })
        await detail.waitFor({ state: 'visible' })
        const box = await detail.boundingBox()
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1, 'Menu must stay in viewport')
        await detail.click()
        await page.locator('.paper-drawer').waitFor({ state: 'visible' })
        await page.locator('.paper-drawer .el-drawer__close-btn').click()
        await page.locator('.paper-drawer').waitFor({ state: 'hidden' })
        await page.locator('.el-overlay:visible').waitFor({ state: 'hidden' })
        results.push({ width, collapsed, more: 'clickable', filters: 'contained', detail: 'opened' })
      }
    }

    // Enter on a nested action must open its menu without selecting the row.
    await page.setViewportSize({ width: 1366, height: 900 })
    await page.locator('.paper-more').first().focus()
    await page.keyboard.press('Enter')
    await page.getByRole('menuitem', { name: '资料详情', exact: true }).waitFor()
    assert.equal(await page.locator('.paper-drawer').isVisible(), false)
    await page.keyboard.press('Escape')

    // An active filter adds another control that must remain clickable.
    await page.getByRole('textbox', { name: '搜索资料', exact: true }).fill('示例文献')
    await page.setViewportSize({ width: 1100, height: 900 })
    await page.getByRole('button', { name: '清除筛选', exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: '搜索资料', exact: true }).inputValue(), '')

    await page.goto(base + '/settings', { waitUntil: 'domcontentloaded' })
    const key = page.getByRole('button', { name: '按键预览 K', exact: true })
    await key.waitFor()
    const face = await key.locator('.keycap-face').boundingBox()
    assert.equal(face.width, 128)
    assert.equal(face.height, 128)
    await key.hover()
    await page.mouse.down()
    await settles(key, 8)
    assert.equal(await key.locator('.keycap-counter').innerText(), '×1')
    assert.ok(Number(await key.locator('.keycap-shadow-contact').evaluate(el => getComputedStyle(el).opacity)) > .97)
    await page.mouse.up()
    await settles(key, 0)

    for (const event of ['pointerleave', 'pointercancel']) {
      await key.dispatchEvent('pointerdown', { button: 0 })
      await settles(key, 8)
      await key.dispatchEvent(event)
      await settles(key, 0)
    }
    await key.focus()
    await page.keyboard.down('Space')
    await settles(key, 8)
    await page.keyboard.up('Space')
    await settles(key, 0)
    assert.equal(await key.locator('.keycap-counter').innerText(), '×4')

    const card = page.locator('.appearance-panel')
    await page.mouse.move(0, 0)
    assert.equal(await card.evaluate(el => getComputedStyle(el, '::before').animationDuration), '4s')
    await card.hover({ position: { x: 5, y: 5 } })
    assert.equal(await card.evaluate(el => getComputedStyle(el, '::before').animationDuration), '2s')
    assert.equal(await card.evaluate(el => getComputedStyle(el, '::after').opacity), '0.4')
    // Element Plus visually hides its native inputs; click the visible controls.
    await page.locator('.el-switch').filter({ has: page.getByRole('switch', { name: '发光边框', exact: true }) }).click()
    await page.locator('.el-switch').filter({ has: page.getByRole('switch', { name: '机械按键', exact: true }) }).click()
    await page.locator('.el-radio-button').filter({ has: page.getByRole('radio', { name: '紧凑', exact: true }) }).click()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await key.waitFor()
    assert.equal(await card.evaluate(el => getComputedStyle(el, '::before').display), 'none')
    await key.dispatchEvent('pointerdown', { button: 0 })
    assert.equal(await faceY(key), 0)
    await key.dispatchEvent('pointercancel')
    await page.goto(base + '/library', { waitUntil: 'domcontentloaded' })
    await page.locator('.library-page.is-compact').waitFor()
    await page.goto(base + '/settings', { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: '恢复默认', exact: true }).click()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await frame(page)
    await key.dispatchEvent('pointerdown', { button: 0 })
    assert.equal(await faceY(key), 0)
    assert.equal(await card.evaluate(el => getComputedStyle(el, '::before').animationName), 'none')
    await key.dispatchEvent('pointercancel')
    await page.emulateMedia({ reducedMotion: 'no-preference' })

    if (screenshots) {
      mkdirSync(screenshots, { recursive: true })
      await page.setViewportSize({ width: 1366, height: 900 })
      await page.evaluate(() => localStorage.setItem('sidebarCollapsed', 'false'))
      await page.reload({ waitUntil: 'domcontentloaded' })
      await key.waitFor()
      await page.screenshot({ path: resolve(screenshots, 'ui-settings.png') })
      await page.goto(base + '/library', { waitUntil: 'domcontentloaded' })
      await page.locator('.paper-more').first().waitFor()
      await page.screenshot({ path: resolve(screenshots, 'library-desktop.png') })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.screenshot({ path: resolve(screenshots, 'library-mobile.png') })
    }
    assert.deepEqual(errors, [], 'No browser errors')
    console.log(JSON.stringify({ responsiveChecks: results.length, results, keyboard: 'passed', keycap: 'passed', preferences: 'passed', reducedMotion: 'passed', browserErrors: errors }, null, 2))
  } finally {
    await browser.close()
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
