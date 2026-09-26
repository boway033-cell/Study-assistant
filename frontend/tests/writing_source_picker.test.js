import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  EVIDENCE_LEVEL_LABELS, SOURCE_TYPES, buildImitatePayload, canGenerateImitate, dropMissing,
  emptySelection, isSelected, removeAt, selectedGroups, sourceKey, sourceSubtitle, toggleSource,
  usableSourceCount, webSourceSelectable,
} from '../src/utils/writingSourceModel.js'

const here = dirname(fileURLToPath(import.meta.url))
const picker = readFileSync(resolve(here, '../src/components/WritingSourcePicker.vue'), 'utf8')
const drawer = readFileSync(resolve(here, '../src/components/WritingLabDrawer.vue'), 'utf8')
const markdown = readFileSync(resolve(here, '../src/utils/markdown.js'), 'utf8')
const api = readFileSync(resolve(here, '../src/api/index.js'), 'utf8')

const note = { type: 'note', id: 7, title: '笔记A', book_id: 3, book_title: '《来源文献》',
               chapter_title: '第一章', page: 12 }
const card = { type: 'evidence', id: 9, title: '证据卡A', book_id: 3, book_title: '《来源文献》',
               page: 4, verification_status: 'needs_review' }
const report = { type: 'report', id: 2, title: '审查报告A', book_ids: [3, 4] }
const local = { type: 'local_literature', id: 5, book_id: 5, title: '《本地文献》', authors: '王五',
                year: 2019, chunk_count: 42 }
const abstractWeb = { provider: 'crossref', provider_id: '10.1000/a', title: '摘要级题名',
                      authors: 'Zhang Li', year: 2021, doi: '10.1000/a', url: 'https://doi.org/10.1000/a',
                      container_title: '治理研究', evidence_level: 'abstract',
                      abstract: '一段足够长的摘要文本，说明样本范围、测量方式与主要限制条件，可以在明确标注为摘要级依据的前提下用于写作。',
                      retrieved_at: '2026-09-14T02:00:00+00:00' }
const metadataWeb = { provider: 'crossref', provider_id: '10.1000/b', title: '仅题名', authors: '',
                      year: null, doi: '10.1000/b', url: 'https://doi.org/10.1000/b',
                      container_title: '', evidence_level: 'metadata', abstract: '',
                      retrieved_at: '2026-09-14T02:00:00+00:00' }

test('selection model keeps five independent source partitions', () => {
  const selection = emptySelection()
  assert.deepEqual(Object.keys(selection).sort(), [...SOURCE_TYPES].sort())
  assert.equal(SOURCE_TYPES.length, 5)
  let next = toggleSource(selection, 'note', note)
  next = toggleSource(next, 'web', abstractWeb)
  next = toggleSource(next, 'local_literature', local)
  assert.equal(next.note.length, 1)
  assert.equal(next.web.length, 1)
  assert.equal(next.local_literature.length, 1)
  // 再次点击同一项即取消选择，不影响其他分区
  const toggled = toggleSource(next, 'note', note)
  assert.equal(toggled.note.length, 0)
  assert.equal(toggled.web.length, 1)
  assert.equal(isSelected(toggled, 'note', note), false)
  assert.equal(isSelected(toggled, 'web', abstractWeb), true)
})

test('selected sources group by type and can be removed one by one', () => {
  let selection = emptySelection()
  for (const [type, item] of [['note', note], ['evidence', card], ['web', abstractWeb]]) {
    selection = toggleSource(selection, type, item)
  }
  const groups = selectedGroups(selection)
  assert.deepEqual(groups.map(group => group.type), ['note', 'evidence', 'web'])
  assert.deepEqual(groups.map(group => group.label), ['知识笔记', '证据卡', '联网补充'])
  assert.equal(groups.length, 3, '空分区不渲染，避免无意义嵌套')
  const removed = removeAt(selection, 'evidence', 0)
  assert.equal(removed.evidence.length, 0)
  assert.equal(removed.note.length, 1)
  assert.equal(sourceKey('web', abstractWeb), 'crossref:10.1000/a')
  assert.equal(sourceKey('note', note), 'note:7')
})

test('metadata-only search hits can never be picked as facts', () => {
  assert.equal(webSourceSelectable(abstractWeb), true)
  assert.equal(webSourceSelectable(metadataWeb), false)
  assert.equal(webSourceSelectable({ evidence_level: 'abstract', abstract: '太短' }), false)
  assert.equal(EVIDENCE_LEVEL_LABELS.metadata, '元数据线索')

  let selection = toggleSource(emptySelection(), 'web', metadataWeb)
  assert.equal(usableSourceCount(selection), 0)
  assert.equal(canGenerateImitate(selection, '地方治理参与机制'), false, '只有元数据线索不能生成')
  const payload = buildImitatePayload({ topic: '地方治理参与机制', genre: '评论', length: 900 }, selection)
  assert.deepEqual(payload.external_sources, [])
  selection = toggleSource(selection, 'web', abstractWeb)
  assert.equal(usableSourceCount(selection), 1)
  assert.equal(canGenerateImitate(selection, '地方治理参与机制'), true)
  assert.equal(canGenerateImitate(selection, '地'), false, '题目过短同样不能生成')
})

test('imitate payload maps every partition and freezes the web snapshot', () => {
  let selection = emptySelection()
  selection = toggleSource(selection, 'note', note)
  selection = toggleSource(selection, 'evidence', card)
  selection = toggleSource(selection, 'report', report)
  selection = toggleSource(selection, 'local_literature', local)
  selection = toggleSource(selection, 'web', abstractWeb)
  selection = toggleSource(selection, 'web', metadataWeb)
  const payload = buildImitatePayload({ topic: '议题标题', genre: '评论', length: 800, brief: '' }, selection)
  assert.deepEqual(payload.knowledge_note_ids, [7])
  assert.deepEqual(payload.evidence_card_ids, [9])
  assert.deepEqual(payload.report_ids, [2])
  assert.deepEqual(payload.local_book_ids, [5])
  assert.equal(payload.external_sources.length, 1, '仅摘要级快照进入 payload')
  const [snapshot] = payload.external_sources
  assert.equal(snapshot.provider, 'crossref')
  assert.equal(snapshot.doi, '10.1000/a')
  assert.equal(snapshot.evidence_level, 'abstract')
  assert.equal(snapshot.retrieved_at, '2026-09-14T02:00:00+00:00')
  assert.equal('id' in snapshot, false, '联网快照不携带库内主键')
})

test('deleted sources are dropped explicitly while the rest survives', () => {
  let selection = emptySelection()
  selection = toggleSource(selection, 'note', note)
  selection = toggleSource(selection, 'note', { ...note, id: 8, title: '笔记B' })
  const { selection: kept, removed } = dropMissing(selection, 'note', [8])
  assert.equal(removed, 1)
  assert.deepEqual(kept.note.map(item => item.id), [7])
  const untouched = dropMissing(kept, 'note', [])
  assert.equal(untouched.removed, 0)
  assert.deepEqual(untouched.selection.note.map(item => item.id), [7])
})

test('list rows expose title, provenance and type signals', () => {
  assert.match(sourceSubtitle('note', note), /《来源文献》/)
  assert.match(sourceSubtitle('note', note), /第 12 页/)
  assert.match(sourceSubtitle('evidence', card), /核验：待核验/)
  assert.equal(sourceSubtitle('report', report), '报告范围 2 篇文献')
  assert.match(sourceSubtitle('local_literature', local), /王五/)
  assert.match(sourceSubtitle('web', abstractWeb), /DOI 10\.1000\/a/)
})

test('source picker renders five partitioned tabs with paging, search and a grouped picked area', () => {
  assert.match(picker, /<el-tabs v-model="active"/)
  assert.match(picker, /class="picker-tabs" stretch/)
  assert.match(picker, /for="tab in tabs"/)
  assert.match(picker, /listWritingSources\(\{ category: type/)
  assert.match(picker, /继续加载/)
  assert.match(picker, /picker-stat/)
  assert.match(picker, /已选来源/)
  assert.match(picker, /sourceSubtitle\(tab\.type, item\)/)
  assert.match(picker, /移除/)
  assert.match(picker, /verificationLabel/)
  // 联网补充：用户主动触发、元数据线索不可选、复用既有 resolve/import/handoff
  assert.match(picker, /searchLiterature\(\{ query, provider: 'crossref'/)
  assert.match(picker, /webSourceSelectable/)
  assert.match(picker, /查找开放全文/)
  assert.match(picker, /在浏览器打开/)
  assert.match(picker, /导入资料库/)
  assert.match(picker, /resolveLiterature/)
  assert.match(picker, /importOpenAccess/)
  assert.match(picker, /openBrowserHandoff/)
  // 失败时保留查询词与已选来源
  assert.match(picker, /web\.error = `文献检索未完成/)
  assert.match(picker, /查询词与已选来源已保留/)
  assert.doesNotMatch(picker, /web\.q = ''/, '失败不得清空检索词')
  assert.doesNotMatch(picker, /emit\('update:modelValue', emptySelection\(\)\)/, '失败不得清空已选来源')
  assert.doesNotMatch(picker, /class="picker-note"/, '不增加描述性说明段落')
  assert.match(drawer, /WritingSourcePicker v-model="imitateSelection" style="max-height:none"/, '来源选择器不得溢出并覆盖后续表单')
  assert.doesNotMatch(drawer, /IMITATION FROM KNOWLEDGE/, '不保留装饰性英文眉题')
  assert.doesNotMatch(drawer, /<div class="imitate-summary"/, '不重复展示已选来源摘要')
})

test('each non-web partition loads its own first page lazily when its tab is opened', () => {
  // Element Plus keeps every pane mounted, so without a per-tab fetch the four
  // non-web partitions would show an empty list as soon as the tab is opened.
  assert.match(picker, /watch\(active, type =>/)
  assert.match(picker, /if \(!list \|\| list\.loaded \|\| list\.loading\) return/)
  assert.match(picker, /fetchPage\(type, 1\)/)
  assert.match(picker, /list\.loaded = true/)
  assert.match(picker, /:class="\['picker-list', `list-\$\{tab\.type\}`\]"/)
})

test('drawer uses the picker and no longer funnels sources through one flat dropdown', () => {
  assert.match(drawer, /WritingSourcePicker v-model="imitateSelection"/)
  assert.doesNotMatch(drawer, /imitateForm\.knowledge_keys/)
  assert.doesNotMatch(drawer, /loadKnowledgeOptions/)
  assert.doesNotMatch(drawer, /取材知识对象/)
  assert.match(drawer, /buildImitatePayload\(imitateForm\.value,imitateSelection\.value\)/)
  assert.match(drawer, /canImitate/)
  // 切换 Writing DNA 只改变风格选择：加载逻辑不再重置内容来源
  assert.doesNotMatch(drawer, /loadGenProfile=async id=>\{[^}]*imitateSelection/)
  assert.doesNotMatch(drawer, /imitateSelection\.value\s*=\s*emptySelection/)
  // 内容边界由后端 prompt 与审计强制执行；配置区不重复堆叠说明文字。
  assert.doesNotMatch(drawer, /取材来源 <small>/)
})

test('imitation result surfaces the content-source audit instead of internal anchors', () => {
  assert.match(drawer, /imitateAudit=computed\(\(\)=>\{/)
  assert.match(drawer, /audit\.content_source_counts/)
  assert.match(drawer, /有效锚点 \{\{ imitateAudit\.validAnchors \}\}/)
  assert.match(drawer, /未引用来源 \{\{ imitateAudit\.unreferenced \}\}/)
  assert.match(drawer, /已截断来源 \{\{ imitateAudit\.truncated \}\}/)
  assert.match(drawer, /在线快照冻结于/)
  assert.match(drawer, /generatedOutput\.value\?\.kind!=='imitation'\)return null/)
})

test('api surface exposes source listing and topic metadata search', () => {
  assert.match(api, /listWritingSources = \(params = \{\}\) => http\.get\('\/writing\/sources'/)
  assert.match(api, /searchLiterature = \(data\) => http\.post\('\/literature\/search'/)
})

test('reader prose hides web machine anchors behind compact source notes', () => {
  assert.match(markdown, /WEB:\[A-Za-z0-9\]/)
  assert.match(markdown, /source-note/)
})
