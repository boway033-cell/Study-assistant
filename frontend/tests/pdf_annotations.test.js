import test from 'node:test'
import assert from 'node:assert/strict'
import { annotationSegments, clipSelectionRects } from '../src/utils/pdfAnnotations.js'

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
