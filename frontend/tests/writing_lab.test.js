import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const drawer = readFileSync(resolve(here, '../src/components/WritingLabDrawer.vue'), 'utf8')
const workbench = readFileSync(resolve(here, '../src/views/LiteratureWorkbenchView.vue'), 'utf8')

test('writing DNA keeps the 20-article and rights boundaries visible', () => {
  assert.match(drawer, /至少20篇完整文章/)
  assert.match(drawer, /book_ids\.length>=20/)
  assert.match(drawer, /我确认有权处理所选文章/)
  assert.match(drawer, /不复制原文观点，也不冒充原作者/)
})

test('workbench exposes versioned DNA, imitation, and DOCX AI-tone cleaning', () => {
  assert.match(workbench, /WritingLabDrawer/)
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
