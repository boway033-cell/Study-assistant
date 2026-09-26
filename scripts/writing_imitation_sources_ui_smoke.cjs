/* Synthetic-only browser workflow for the imitation source picker.
 *
 * Every /api request is mocked: no model calls, no real knowledge base, no real
 * network. Run against a locally served production build, e.g.
 *   npx vite preview --port 5333 --strictPort
 *   node scripts/writing_imitation_sources_ui_smoke.cjs http://127.0.0.1:5333 [screenshotDir]
 */
const assert = require('node:assert/strict')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const base = process.argv[2] || 'http://127.0.0.1:5333'
const screenshotDir = process.argv[3] ? resolve(process.argv[3]) : null
if (screenshotDir) mkdirSync(screenshotDir, { recursive: true })

const errors = []
const steps = []
let imitatePayload = null

const dnaA = { id: 9, name: 'DNA-A 评论写作', target_author: '', book_ids: [201, 202], corpus_count: 20,
               status: 'ready', current_version: 1, rights_acknowledged: true, feedback: '', error_msg: null,
               created_at: '2026-09-01T00:00:00', updated_at: '2026-09-01T00:00:00' }
const dnaB = { ...dnaA, id: 10, name: 'DNA-B 学术随笔', book_ids: [203] }
const revision = { id: 31, profile_id: 9, version: 1, language_dna: '# 语言DNA\n短句为主。',
                   structure_patterns: '# 结构模板\n问题—证据—结论。', logic_dna: '# 逻辑结构DNA\n因果推进。',
                   cognitive_framework: '# 认知框架\n机制优先。', visual_style_guide: '# 视觉指南\n少用表格。',
                   writing_dna: '# 整合DNA\n克制。', quality: {}, feedback: '', created_at: '2026-09-01T00:00:00' }

const noteRows = [
  { type: 'note', id: 1, title: '笔记A：参与机制的财政约束', book_id: 101, book_title: '《参与式治理研究》',
    chapter_title: '第三章', page: 42, origin: 'user', preview: '财政紧缩下基层组织能力决定参与质量。' },
  { type: 'note', id: 2, title: '笔记B：DNA 语料之外的观察', book_id: 102, book_title: '《地方财政笔记》',
    chapter_title: null, page: 7, origin: 'user', preview: '样本只覆盖东部两省。' },
  { type: 'note', id: 3, title: '笔记C：访谈摘要', book_id: 103, book_title: '《基层访谈集》',
    chapter_title: null, page: 12, origin: 'user', preview: '受访者强调考核压力。' },
]
const cardRows = [
  { type: 'evidence', id: 11, title: '证据卡A：三种机制对照', book_id: 101, book_title: '《参与式治理研究》',
    page: 44, verification_status: 'verified', claim_preview: '机制差异来自考核方式。', preview: '三种机制的效果方向不同。' },
  { type: 'evidence', id: 12, title: '证据卡B：反向结果', book_id: 104, book_title: '《预算约束》',
    page: 9, verification_status: 'needs_review', claim_preview: '存在相反证据。', preview: '两项研究结论相反。' },
]
const reportRows = [
  { type: 'report', id: 21, title: '批判性审查：参与机制综述', book_ids: [101, 102],
    evidence_summary: { total: 6 }, hypothesis_count: 2, preview: '结论尚不稳定。' },
]
const localRows = [
  { type: 'local_literature', id: 101, book_id: 101, title: '《参与式治理研究》', authors: '王五', year: 2019,
    journal: '治理研究', file_type: 'pdf', chunk_count: 42 },
  { type: 'local_literature', id: 102, book_id: 102, title: '《地方财政笔记》', authors: '李四', year: 2021,
    journal: null, file_type: 'pdf', chunk_count: 18 },
]
const localPage2 = [
  { type: 'local_literature', id: 103, book_id: 103, title: '《基层访谈集》', authors: '赵六', year: 2022,
    journal: null, file_type: 'docx', chunk_count: 9 },
]
const searchResults = [
  { provider: 'crossref', provider_id: '10.1000/abs', title: 'Participatory governance under fiscal stress',
    authors: 'Zhang Li', year: 2021, doi: '10.1000/abs', container_title: 'Journal of Public Administration',
    url: 'https://doi.org/10.1000/abs', evidence_level: 'abstract', retrieved_at: '2026-09-14T02:00:00+00:00',
    abstract: '本文比较三种参与机制在财政紧缩条件下的稳定性，样本只覆盖东部两省，结论不能外推到全国。' },
  { provider: 'crossref', provider_id: '10.1000/meta', title: 'Metadata-only hit on municipal budgeting',
    authors: 'Chen Wei', year: 2017, doi: '10.1000/meta', container_title: '',
    url: 'https://doi.org/10.1000/meta', evidence_level: 'metadata', retrieved_at: '2026-09-14T02:00:00+00:00',
    abstract: '' },
]
const generatedOutput = {
  id: 501, profile_id: 9, kind: 'imitation', title: '地方治理中的参与机制', input_type: 'text',
  download_ready: false, created_at: '2026-09-14T02:05:00',
  output_text: ['# 地方治理中的参与机制', '',
                '核心判断：财政紧缩条件下，参与质量更依赖基层组织能力[NOTE:1]。', '',
                '海外研究给出可比较的摘要级依据[WEB:crossref:10.1000/abs]。', '',
                '## 结论', '', '证据边界清楚，样本仅覆盖东部两省。', '',
                '## 来源索引', '',
                '1. 知识笔记“笔记A：参与机制的财政约束”（关联文献 B101）',
                '2. crossref｜Participatory governance under fiscal stress，Zhang Li，2021，摘要级依据'].join('\n'),
  audit: {
    dna_profile_id: 9, dna_version: 1, calibration_books: ['《语料一》', '《语料二》'],
    calibration_policy: 'dna_corpus_style_only',
    content_source_counts: { note: 1, evidence: 1, report: 1, local_literature: 1, web: 1 },
    content_policy: 'selected_content_sources_only', valid_anchor_count: 5, citation_count: 5,
    invalid_anchors: [], unreferenced_sources: [], truncated_sources: [],
    external_sources: [searchResults[0]], external_retrieved_at: searchResults[0].retrieved_at,
    citation_notes: [{ number: 2, anchor: 'WEB:crossref:10.1000/abs',
                       label: 'crossref｜Participatory governance under fiscal stress，Zhang Li，2021，摘要级依据' }],
    ai_tone_violations: {}, human_review_required: true,
  },
  source_freshness: { status: 'fresh', message: '生成时使用的知识对象与文献片段未发生变化。',
                      checked_sources: 4, immutable_snapshots: 1, changed: [], missing: [],
                      checked_at: '2026-09-14T02:06:00' },
}

const sourcesPayload = url => {
  const category = url.searchParams.get('category') || 'note'
  const q = (url.searchParams.get('q') || '').trim()
  const page = Number(url.searchParams.get('page') || 1)
  const ids = url.searchParams.getAll('ids')
  const counts = { note: noteRows.length, evidence: cardRows.length, report: reportRows.length,
                   local_literature: localRows.length + localPage2.length }
  const table = { note: noteRows, evidence: cardRows, report: reportRows }
  if (category === 'local_literature') {
    const all = [...localRows, ...localPage2]
    const items = page === 1 ? localRows : localPage2
    if (!ids.length) return { category, items, total: all.length, page, page_size: 20, counts, missing_ids: [] }
    const wanted = ids.map(Number)
    const picked = all.filter(row => wanted.includes(row.book_id))
    return { category, items: picked, total: picked.length, page: 1, page_size: 20, counts,
             missing_ids: wanted.filter(id => !all.some(row => row.book_id === id)) }
  }
  const rows = table[category] || []
  if (ids.length) {
    const wanted = ids.map(Number)
    const picked = rows.filter(row => wanted.includes(row.id))
    return { category, items: picked, total: picked.length, page: 1, page_size: 20, counts,
             missing_ids: wanted.filter(id => !rows.some(row => row.id === id)) }
  }
  const filtered = q ? rows.filter(row => row.title.includes(q)) : rows
  const start = (page - 1) * 20
  return { category, items: filtered.slice(start, start + 20), total: filtered.length, page, page_size: 20,
           counts, missing_ids: [] }
}

async function mock(route) {
  const url = new URL(route.request().url())
  const path = url.pathname
  const method = route.request().method()
  if (!path.startsWith('/api/')) return route.continue()
  const json = (data, status = 200) => route.fulfill({ status, json: data })
  if (path === '/api/writing/sources') return json(sourcesPayload(url))
  if (path === '/api/writing/profiles' && method === 'GET') return json({ items: [dnaA, dnaB] })
  if (path === '/api/writing/profiles/9') return json({ ...dnaA, corpus_manifest: [], missing_book_ids: [], revisions: [revision] })
  if (path === '/api/writing/profiles/10') return json({ ...dnaB, corpus_manifest: [], missing_book_ids: [], revisions: [] })
  if (/^\/api\/writing\/profiles\/\d+\/imitate$/.test(path)) {
    imitatePayload = route.request().postDataJSON()
    return json({ task_id: 'task-imitate-1' }, 202)
  }
  if (path === '/api/writing/outputs/501') return json(generatedOutput)
  if (path === '/api/writing/outputs') return json({ items: [], total: 0, page: 1, page_size: 20 })
  if (path === '/api/literature/search') {
    const body = route.request().postDataJSON()
    assert.ok(body.query.length >= 3, '检索词必须由用户输入')
    return json({ attempt_id: 77, provider: 'crossref', query: body.query, count: searchResults.length,
                  results: searchResults,
                  evidence_levels: { abstract: '摘要级依据（主张不得超出摘要）', metadata: '元数据线索' } })
  }
  if (path === '/api/literature/resolve') {
    return json({ attempt_id: 78, candidates: [{ provider: 'unpaywall', route: 'open_access',
                                                 url: 'https://example.org/oa.pdf', label: 'Unpaywall 开放全文',
                                                 direct_download: true, status: 'ready', message: '' }],
                  next: 'download' })
  }
  if (path === '/api/tasks/task-imitate-1/events') return json({ detail: 'no stream' }, 404)
  if (path === '/api/tasks/task-imitate-1') return json({ id: 'task-imitate-1', status: 'done', progress: 1,
                                                          message: '独立新作已生成', result: { output_id: 501 } })
  if (path === '/api/tasks') return json({ items: [], total: 0 })
  if (path === '/api/books') return json({ items: [{ id: 101, title: '《参与式治理研究》', status: 'ready' }], total: 1 })
  if (path === '/api/settings') return json({ text_provider_configured: true })
  return json({ items: [], total: 0 })
}

const shot = async (page, name) => { if (screenshotDir) await page.screenshot({ path: resolve(screenshotDir, name) }) }

async function main() {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) })
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    await context.addInitScript(() => localStorage.setItem('aiKeyGuideSeen', 'true'))
    await context.route('**/api/**', mock)
    const page = await context.newPage()
    page.on('pageerror', error => errors.push(error.message))
    page.setDefaultTimeout(15000)

    await page.goto(base + '/writing')
    await page.locator('.writing-workspace').waitFor()
    await page.locator('.writing-tabs').getByRole('tab', { name: '写作生成' }).click()
    await page.locator('.source-picker').waitFor()
    const rowsIn = type => page.locator(`.source-picker .list-${type} .picker-row`)
    // Element Plus keeps every pane in the DOM (including its v-loading mask), so
    // each interaction waits for that pane's loading overlay to go away first.
    const idle = type => page.locator(`.source-picker .list-${type} .el-loading-mask`)
      .waitFor({ state: 'hidden' }).catch(() => {})
    const idleWeb = () => page.locator('.source-picker .list-web .el-loading-mask')
      .waitFor({ state: 'hidden' }).catch(() => {})
    await idle('note')
    await rowsIn('note').first().waitFor()
    steps.push('打开写作生成页并渲染取材来源选择器')

    // 1) 五个分区各自独立
    const tabLabels = await page.locator('.source-picker .tab-label').allInnerTexts()
    assert.deepEqual(tabLabels.map(text => text.replace(/\s*\d+$/, '')),
                     ['知识笔记', '证据卡', '审查报告', '本地文献', '联网补充'])
    assert.match(tabLabels.join('|'), /知识笔记\s*3/)
    assert.match(tabLabels.join('|'), /本地文献\s*3/)
    steps.push('五类来源分区与独立数量：' + tabLabels.join(' / '))

    // 2) 无来源时不能生成
    const generate = page.getByRole('button', { name: '生成独立新作' })
    assert.equal(await generate.isDisabled(), true, '未选择内容来源时必须禁用生成')
    steps.push('未选择内容来源时生成按钮禁用')

    // 3) 知识笔记：选择 + 关键词搜索
    await rowsIn('note').first().click()
    await page.locator('.picked-panel').getByText('笔记A：参与机制的财政约束').waitFor()
    assert.match(await page.locator('.picked-panel').innerText(), /知识笔记/)
    steps.push('选择知识笔记后，已选来源按类型分组显示')

    await page.getByPlaceholder('搜索知识笔记').fill('不存在的关键词')
    await page.getByPlaceholder('搜索知识笔记').press('Enter')
    await page.locator('.source-picker .list-note').getByText('没有匹配的来源').waitFor()
    assert.match(await page.locator('.picked-panel').innerText(), /笔记A：参与机制的财政约束/, '搜索失败不得清空已选来源')
    await page.getByPlaceholder('搜索知识笔记').fill('')
    await page.getByPlaceholder('搜索知识笔记').press('Enter')
    await rowsIn('note').first().waitFor()
    steps.push('来源搜索为空时保留已选来源')

    // 4) 证据卡（含核验状态）+ 批判性审查报告
    await page.locator('.source-picker .tab-label').filter({ hasText: '证据卡' }).click()
    await idle('evidence')
    await rowsIn('evidence').first().waitFor()
    assert.match(await rowsIn('evidence').first().innerText(), /核验：已核验/)
    await rowsIn('evidence').first().click()
    await page.locator('.source-picker .tab-label').filter({ hasText: '审查报告' }).click()
    await idle('report')
    await rowsIn('report').first().waitFor()
    assert.match(await rowsIn('report').first().innerText(), /报告范围 2 篇文献/)
    await rowsIn('report').first().click()
    steps.push('证据卡显示核验状态；报告显示范围')

    // 5) 本地文献分页/继续加载
    await page.locator('.source-picker .tab-label').filter({ hasText: '本地文献' }).click()
    await idle('local_literature')
    await rowsIn('local_literature').first().waitFor()
    assert.equal(await rowsIn('local_literature').count(), 2)
    await page.getByRole('button', { name: /继续加载（2\/3）/ }).click()
    await rowsIn('local_literature').nth(2).waitFor()
    await idle('local_literature')
    await rowsIn('local_literature').first().click()
    steps.push('本地文献支持继续加载并可选作取材')

    // 6) 切换 Writing DNA 不得清空内容来源
    const pickedBefore = await page.locator('.picked-panel').innerText()
    // "Writing DNA" 在三个标签页里都出现，这里用该字段独有的说明文字定位到生成页那一项。
    await page.locator('.generate-config label.review-field:visible')
      .filter({ hasText: 'Writing DNA' }).locator('.el-select').click()
    // 其他标签页的下拉也留在 DOM 里，只点当前展开的那个。
    await page.locator('.el-select-dropdown:visible .el-select-dropdown__item')
      .filter({ hasText: 'DNA-B 学术随笔' }).click()
    assert.equal(await page.locator('.picked-panel').innerText(), pickedBefore, '切换 DNA 不得清空内容来源')
    steps.push('切换到 DNA-B 后已选来源保持不变')

    // 7) 联网补充：有摘要可选、无摘要只能作为线索
    await page.locator('.source-picker .tab-label').filter({ hasText: '联网补充' }).click()
    await page.getByPlaceholder('按主题检索学术元数据（如：地方治理 参与机制）').fill('地方治理 参与机制')
    await page.getByRole('button', { name: '检索', exact: true }).click()
    await page.getByText('Metadata-only hit on municipal budgeting').waitFor()
    const webRows = page.locator('.source-picker .list-web .web-result')
    assert.equal(await webRows.count(), 2)
    assert.match(await webRows.nth(0).innerText(), /摘要级依据/)
    assert.match(await webRows.nth(1).innerText(), /元数据线索/)
    await idleWeb()
    await webRows.nth(1).locator('.picker-row').click()
    assert.equal(await page.locator('.picked-panel').getByText('Metadata-only hit on municipal budgeting').count(), 0,
                 '只有题名/元数据的命中不得被勾选为事实依据')
    await webRows.nth(1).getByRole('button', { name: '查找开放全文' }).click()
    await webRows.nth(1).getByRole('button', { name: '导入资料库' }).waitFor()
    steps.push('联网结果区分摘要级依据与元数据线索，线索只能走开放全文流程')
    if (screenshotDir) await shot(page, '01-source-picker.png')

    await webRows.nth(0).locator('.picker-row').click()
    assert.match(await page.locator('.picked-panel').innerText(), /联网补充/)

    // 来源较多时，窄窗口下选择器必须保持正常文档流，不能覆盖后续表单和生成按钮。
    await page.setViewportSize({ width: 1280, height: 720 })
    const topicInput = page.getByPlaceholder('新文章题目或议题')
    await topicInput.scrollIntoViewIfNeeded()
    const pickerBox = await page.locator('.source-picker').boundingBox()
    const topicBox = await topicInput.boundingBox()
    const configBox = await page.locator('.generate-config').boundingBox()
    assert.ok(pickerBox && topicBox && pickerBox.y + pickerBox.height <= topicBox.y,
              '来源选择器不得覆盖题目与生成配置')
    assert.ok(configBox && pickerBox.x >= configBox.x && pickerBox.x + pickerBox.width <= configBox.x + configBox.width,
              '来源选择器和标签文字不得越出左侧配置栏')
    await generate.scrollIntoViewIfNeeded()
    assert.equal(await generate.isVisible(), true, '窄窗口中生成按钮必须可滚动抵达')
    if (screenshotDir) await shot(page, '03-narrow-no-overlap.png')
    steps.push('1280×720 窄窗口下来源选择器不覆盖后续表单，生成按钮可抵达')

    // 8) 生成：题目与内容来源齐备后按钮可用；payload 与生成后的来源索引/审计
    await topicInput.fill('地方治理中的参与机制')
    assert.equal(await generate.isDisabled(), false, '题目与内容来源齐备后生成按钮可用')
    await generate.click()
    await page.locator('.generate-result .markdown-body').getByText('来源索引').waitFor()
    // 独立新作的审计摘要：来源类型计数 + 锚点/截断 + 在线快照时间
    const auditStrip = page.locator('.generate-result .imitation-audit')
    await auditStrip.getByText('知识笔记 1 · 证据卡 1 · 批判性审查报告 1 · 本地文献 1 · 联网补充 1').waitFor()
    await auditStrip.getByText('有效锚点 5 · 引用 5').waitFor()
    await auditStrip.getByText('在线快照冻结于').waitFor()
    assert.match(await page.locator('.generate-result header').innerText(), /IMITATION DRAFT/)
    assert.ok(imitatePayload, '生成请求必须已发出')
    assert.deepEqual(imitatePayload.knowledge_note_ids, [1])
    assert.deepEqual(imitatePayload.evidence_card_ids, [11])
    assert.deepEqual(imitatePayload.report_ids, [21])
    assert.deepEqual(imitatePayload.local_book_ids, [101])
    assert.equal(imitatePayload.external_sources.length, 1)
    const [snapshot] = imitatePayload.external_sources
    assert.deepEqual(Object.keys(snapshot).sort(),
                     ['abstract', 'authors', 'container_title', 'doi', 'evidence_level', 'provider',
                      'provider_id', 'retrieved_at', 'title', 'url', 'year'].sort())
    assert.equal(snapshot.provider, 'crossref')
    assert.equal(snapshot.doi, '10.1000/abs')
    assert.equal(snapshot.evidence_level, 'abstract')
    assert.equal(snapshot.retrieved_at, '2026-09-14T02:00:00+00:00')
    const article = await page.locator('.generate-result .markdown-body').innerText()
    assert.match(article, /来源索引/)
    assert.match(article, /crossref｜Participatory governance under fiscal stress/)
    assert.doesNotMatch(article, /\[NOTE:|\[WEB:|\[EVIDENCE:|\[REPORT:/, '读者正文不得出现内部锚点')
    steps.push('生成 payload 保留 provider/DOI/evidence_level/retrieved_at；正文转为可读来源索引')
    if (screenshotDir) await shot(page, '02-generated.png')

    assert.deepEqual(errors, [], '页面不得出现运行时错误')
    console.log('OK ' + steps.length + ' steps')
    steps.forEach((step, index) => console.log(`  ${index + 1}. ${step}`))
  } finally {
    await browser.close()
  }
}

main().catch(error => { console.error('SMOKE FAILED:', error.message); process.exit(1) })
