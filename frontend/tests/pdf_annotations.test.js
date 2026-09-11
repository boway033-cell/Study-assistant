import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { annotationSegments, clipSelectionRects } from '../src/utils/pdfAnnotations.js'

const here = dirname(fileURLToPath(import.meta.url))

test('cross-page selection is split and clipped into normalized page segments', () => {
  const segments = clipSelectionRects([
    { left: 20, top: 80, right: 80, bottom: 110, width: 60, height: 30 },
  ], [
    { page: 1, source: 'pdf-text', bounds: { left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100 } },
    { page: 2, source: 'ocr', bounds: { left: 0, top: 100, right: 100, bottom: 200, width: 100, height: 100 } },
  ])
  assert.deepEqual(segments.map(x => x.page), [1, 2])
  assert.equal(segments[0].rects[0].y, 0.8)
  assert.equal(segments[0].rects[0].h, 0.2)
  assert.equal(segments[1].rects[0].y, 0)
  assert.equal(segments[1].source, 'ocr')
  assert.ok(segments.every(s => s.rects.every(r => r.x >= 0 && r.y >= 0 && r.x + r.w <= 1 && r.y + r.h <= 1)))
})

test('v2 segments take precedence while v1 rect_json remains readable', () => {
  const v2 = annotationSegments({ anchor_json: JSON.stringify({ segments: [{ page: 3, source: 'ocr', rects: [{ x: 0, y: 0, w: .1, h: .1 }] }] }) })
  assert.equal(v2[0].page, 3)
  const v1 = annotationSegments({ page: 2, rect_json: '[{"x":0.1,"y":0.2,"w":0.3,"h":0.04}]' })
  assert.equal(v1[0].page, 2)
  assert.equal(v1[0].source, 'pdf-text')
})

test('highlight index writes each page\'s rects back into the per-page map', () => {
  // 行为契约：hlIndex 累积每页高亮时必须“先建 key 再 push”。
  // v2.3.0 曾写成 `const arr = m.get(pg) || []; arr.push(...)`，首次遇到某页时
  // push 进临时数组且从未 set 回 Map，导致所有高亮/划线色块永不渲染。
  const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')
  assert.match(reader, /if \(!m\.has\(pg\)\) m\.set\(pg, \[\]\)/)
  assert.match(reader, /m\.get\(pg\)\.push\(/)
})
