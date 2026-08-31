import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const drawer = readFileSync(resolve(here, '../src/components/WritingLabDrawer.vue'), 'utf8')
const workbench = readFileSync(resolve(here, '../src/views/LiteratureWorkbenchView.vue'), 'utf8')
const writingView = readFileSync(resolve(here, '../src/views/WritingView.vue'), 'utf8')
const navigation = readFileSync(resolve(here, '../src/components/AppNavigation.vue'), 'utf8')
const router = readFileSync(resolve(here, '../src/router/index.js'), 'utf8')
const study = readFileSync(resolve(here, '../src/views/StudyView.vue'), 'utf8')
const markdown = readFileSync(resolve(here, '../src/utils/markdown.js'), 'utf8')

test('writing DNA keeps the 20-article and rights boundaries visible', () => {
  assert.match(drawer, /至少20篇完整文章/)
  assert.match(drawer, /book_ids\.length>=20/)
  assert.match(drawer, /我确认有权处理所选文章/)
  assert.match(drawer, /不复制原文观点，也不冒充原作者/)
})

test('writing is a discoverable first-class workspace with versioned DNA and DOCX cleaning', () => {
  assert.match(writingView, /WritingLabDrawer/)
  assert.match(writingView, /embedded/)
  assert.match(navigation, /写作工作台/)
  assert.match(router, /path: '\/writing'/)
  assert.match(workbench, /router\.push\('\/writing'\)/)
  assert.match(drawer, /持续完善/)
  assert.match(drawer, /独立仿写与输出/)
  assert.match(drawer, /导入 Word 并输出 Word/)
  assert.match(drawer, /规则 \{\{/)
})

test('writing outputs support corpus correction, version comparison, editing, and human review', () => {
  assert.match(drawer, /比较历史版本/)
  assert.match(drawer, /下一版排除/)
  assert.match(drawer, /保存并重新生成 Word/)
  assert.match(drawer, /逐条决定是否采用/)
  assert.match(drawer, /reviewWritingOutput/)
  assert.match(drawer, /正文按需加载/)
})

test('multi-document review is a first-class closed-corpus writing workflow', () => {
  assert.match(drawer, /多文献综述/)
  assert.match(drawer, /至少选择 2 篇/)
  assert.match(drawer, /Writing DNA/)
  assert.match(drawer, /去 AI 味生成约束/)
  assert.match(drawer, /不是系统综述/)
  assert.match(drawer, /createLiteratureReview/)
  assert.match(drawer, /v-model\.number="reviewForm\.length"/)
  assert.match(drawer, /subscribeTask\(submitted\.task_id/)
  assert.match(drawer, /citation_warning/)
})

test('research reports expose coherent writing and controlled extension choices', () => {
  assert.match(study, /连贯分析文章/)
  assert.match(study, /探索性延伸/)
  assert.match(study, /target_length:targetLength\.value/)
})

test('reader prose hides machine anchors behind compact source notes', () => {
  assert.match(markdown, /source-note/)
  assert.match(markdown, /\(B\\d\+/)
  assert.match(drawer, /citation_warning/)
})

test('research, writing, and PPTX share the selected-knowledge-object boundary', () => {
  assert.match(study, /保存批判性审查到知识库/)
  assert.match(study, /跨文献关系/)
  assert.match(drawer, /知识对象.*必选/)
  assert.match(drawer, /knowledge_note_ids/)
  assert.match(workbench, /知识对象（必选）/)
  assert.match(workbench, /每页必须引用/)
  assert.match(study, /人工复核/)
  assert.match(study, /updateStudyReportClaims/)
  assert.match(drawer, /source_freshness/)
  assert.match(workbench, /source_freshness/)
})
