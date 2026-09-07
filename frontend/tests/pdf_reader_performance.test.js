import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')
const api = readFileSync(resolve(here, '../src/api/index.js'), 'utf8')

test('PDF loading and page rendering have bounded waits and cancellation', () => {
  assert.match(reader, /PDF 响应超时/)
  assert.match(reader, /task\.cancel\(\)/)
  assert.match(reader, /文字层解析超时/)
  assert.match(reader, /loadingTask\.destroy\(\)/)
})

test('visible pages render sequentially under a smaller pixel budget', () => {
  assert.match(reader, /单队列逐页渲染/)
  assert.match(reader, /5_000_000/)
  assert.doesNotMatch(reader, /Promise\.all\(\[\.\.\.want\]/)
})

test('page pixels are shown before the optional text and OCR layer finishes', () => {
  const painted = reader.indexOf('rendered.value[p] = true')
  const textLayer = reader.indexOf('void renderTextLayer(p, pdfPage, vp, generation, token)')
  assert.ok(painted > 0 && textLayer > painted)
  assert.match(reader, /const pendingRenders = new Map\(\)/)
  assert.match(reader, /pageRenderTokens\[p\] !== token/)
})

test('long-document page geometry includes the accumulated page gap', () => {
  assert.match(reader, /const PAGE_GAP = 10/)
  assert.match(reader, /pageH\(i\) \+ PAGE_GAP/)
  assert.match(reader, /const h = pageH\(p\) \+ PAGE_GAP/)
})

test('task event disconnects fall back to persisted status polling', () => {
  assert.match(api, /const startPolling/)
  assert.match(api, /source\.onerror = startPolling/)
  assert.match(api, /http\.get\(`\/tasks\/\$\{encodeURIComponent\(taskId\)\}`\)/)
})
