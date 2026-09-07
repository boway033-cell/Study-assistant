/* Synthetic-only browser workflow. All /api traffic is mocked; no model calls. */
const assert = require('node:assert/strict')
const { mkdirSync, readFileSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')
const base = process.argv[2] || 'http://127.0.0.1:5177'
const screenshots = process.argv[3] && resolve(process.argv[3])
const preset = JSON.parse(readFileSync(resolve(__dirname, '../backend/app/services/official_skills/sanmu/assets/presets/generic_official_v1.json'), 'utf8'))
const rows = [], errors = []
let profile = { preset, profile: structuredClone(preset), overrides: {}, etag: 'f'.repeat(64) }
const skills = ['lieflat-gongwen', 'official-document-skill', 'sanmu-document-formatting'].map(name => ({ name, role: '测试接入', license: name === 'lieflat-gongwen' ? 'PolyForm Noncommercial 1.0.0' : 'MIT' }))
function version(brief, text, stage, parent, confirmed = false) {
  const row = { id: rows.length + 1, title: brief.title, text, etag: String(rows.length + 1).padStart(64, '0'), audit: { brief, stage, confirmed, parent_id: parent?.id, checks: { warnings: ['日期待核对'], lieflat: { applicable: false } } } }
  rows.push(row); return row
}
async function mock(route) {
  const url = new URL(route.request().url()), path = url.pathname
  if (!path.startsWith('/api/')) return route.continue()
  const body = route.request().postDataJSON(), action = path.split('/').at(-1)
  let data = {}
  if (path === '/api/writing/official/options') data = { genres: ['通知', '报告', '调研报告'], skills, format: profile }
  else if (path === '/api/writing/official/outputs') data = { items: rows.slice().reverse().map(row => ({ ...row, stage: row.audit.stage })), total: rows.length }
  else if (path === '/api/writing/official/outline') data = version(body.brief, '一、核验安排\n二、材料要求\n【待补：日期】', 'outline')
  else if (/\/official\/outputs\/\d+/.test(path)) {
    const id = Number(path.split('/')[5]), row = rows.find(r => r.id === id)
    if (action === String(id)) data = row
    else if (action === 'confirm' || action === 'save') data = version({ ...row.audit.brief, title: body.title }, body.text, row.audit.stage, row, action === 'confirm')
    else if (action === 'draft') { assert.equal(row.audit.confirmed, true); data = version(row.audit.brief, '各科室：\n一、核验安排\n核验12份材料。\n【待补：日期】', 'draft', row) }
    else if (action === 'review') { row.audit.review = 'P0：日期待补，请核对实际安排。'; data = row }
    else if (action === 'revise') data = version(row.audit.brief, row.text + '\n保留核验记录。', 'draft', row)
    else if (action === 'export') { row.audit.export = { warnings: ['Font fallback used: 测试字体'], profile: preset, validation: 'structural_pass', visual_status: 'not_checked' }; data = row }
  } else if (path === '/api/writing/official/profile') { profile = { ...profile, overrides: body.overrides, etag: 'e'.repeat(64) }; data = profile }
  else if (path === '/api/books' || path === '/api/tasks' || path === '/api/writing/outputs' || path === '/api/writing/profiles') data = { items: [], total: 0 }
  else if (path === '/api/shelves') data = []
  else if (path === '/api/settings') data = { text_provider_configured: true }
  return route.fulfill({ json: data })
}
async function main() {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    await context.addInitScript(() => localStorage.setItem('aiKeyGuideSeen', 'true'))
    await context.route('**/api/**', mock)
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    page.setDefaultTimeout(10000)
    await page.goto(base + '/writing/official')
    await page.locator('.official-workbench').waitFor()
    assert.ok(page.url().includes('mode=official'))
    await page.reload()
    await page.locator('.official-workbench').waitFor()
    assert.equal(await page.locator('.writing-mode').getByRole('button', { name: '公文写作', exact: true }).getAttribute('aria-pressed'), 'true')
    await page.getByLabel('公文主题', { exact: true }).fill('关于开展资料核验的通知')
    await page.getByLabel('事实材料', { exact: true }).fill('示例单位拟核验12份材料。日期待定。')
    assert.equal(await page.getByRole('button', { name: '生成提纲', exact: true }).isDisabled(), true)
    await page.locator('label.el-checkbox').filter({ hasText: '允许将本稿材料' }).click()
    await page.getByRole('button', { name: '生成提纲', exact: true }).click()
    await page.getByLabel('公文编辑器').waitFor()
    assert.equal(await page.getByRole('button', { name: '展开正文', exact: true }).isDisabled(), true)
    await page.getByRole('button', { name: '确认提纲', exact: true }).click()
    await page.getByText('已确认', { exact: true }).waitFor()
    await page.getByLabel('公文编辑器').fill('一、修改后的安排\n二、材料要求')
    assert.equal(await page.getByRole('button', { name: '展开正文', exact: true }).isDisabled(), true)
    await page.getByRole('button', { name: '确认提纲', exact: true }).click()
    await page.getByText('已确认', { exact: true }).waitFor()
    await page.getByRole('button', { name: '展开正文', exact: true }).click()
    await page.getByRole('button', { name: '审稿', exact: true }).waitFor()
    await page.getByRole('button', { name: '审稿', exact: true }).click()
    await page.locator('.review-text').waitFor()
    await page.getByLabel('改稿意见', { exact: true }).fill('保留核验记录')
    await page.getByRole('button', { name: '按意见生成新版本', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('[aria-label="公文编辑器"]').value.includes('保留核验记录'))
    assert.equal(await page.getByRole('button', { name: '导出 Word', exact: true }).isDisabled(), true)
    await page.locator('label.el-checkbox').filter({ hasText: '已人工核对待补项' }).click()
    await page.getByRole('button', { name: '导出 Word', exact: true }).click()
    await page.getByRole('link', { name: '下载已导出的 Word' }).waitFor()
    await page.locator('.official-format .el-collapse-item__header').filter({ hasText: 'Word 排版' }).click()
    await page.locator('label.el-checkbox').filter({ hasText: '全文加粗' }).click()
    await page.getByRole('button', { name: '保存为默认…' }).click()
    await page.getByText('global.bold', { exact: true }).waitFor()
    await page.getByRole('button', { name: '取消', exact: true }).click()
    await page.getByRole('button', { name: '恢复预设', exact: true }).click()
    assert.equal(await page.getByRole('checkbox', { name: '全文加粗', exact: true }).isChecked(), true)
    const widths = []
    for (const width of [1920, 1440, 1280, 1024, 900, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.waitForTimeout(150)
      const overflow = await page.evaluate(() => ['.official-workbench', '.official-layout', '.editor-pane', '.brief-pane', '.main'].filter(selector => { const el = document.querySelector(selector); return el && el.scrollWidth > el.clientWidth + 2 }))
      assert.deepEqual(overflow, [], `Overflow at ${width}px`)
      for (const name of ['保存版本', '审稿', '导出 Word']) {
        const button = page.getByRole('button', { name, exact: true })
        await button.scrollIntoViewIfNeeded()
        const box = await button.boundingBox()
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `${name} clipped at ${width}px`)
      }
      widths.push(width)
      if (screenshots && [1440, 390].includes(width)) { mkdirSync(screenshots, { recursive: true }); await page.locator('.official-toolbar').scrollIntoViewIfNeeded(); await page.screenshot({ path: resolve(screenshots, `official-${width}.png`), fullPage: true }) }
    }
    await page.locator('.writing-mode').getByRole('button', { name: '研究写作', exact: true }).click()
    assert.equal(await page.locator('.official-workbench').isVisible(), false)
    await page.locator('.writing-mode').getByRole('button', { name: '公文写作', exact: true }).click()
    assert.ok((await page.getByLabel('公文编辑器').inputValue()).includes('保留核验记录'))
    await page.goBack()
    assert.equal(await page.locator('.official-workbench').isVisible(), false)
    await page.goForward()
    await page.locator('.official-workbench').waitFor()
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ workflow: 'passed', widths, versions: rows.length, pageErrors: errors }))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
