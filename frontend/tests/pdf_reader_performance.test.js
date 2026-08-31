import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')

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
