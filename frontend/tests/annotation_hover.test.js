import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  annotationAriaLabel, annotationSource, buildHoverItem, buildHoverItems,
  hitTestEntries, hoverTolerance, placeHoverCard, retainHoverIds, unionRect,
  HOVER_CARD_MAX_HEIGHT, HOVER_CARD_MAX_WIDTH, HOVER_CLOSE_DELAY_MS, HOVER_HIT_TOLERANCE_PX,
  HOVER_OPEN_DELAY_MS,
} from '../src/utils/annotationHover.js'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')

// 悬浮卡模板片段，用于断言「卡片内容走文本插值，不用原生 title / v-html」。
const cardStart = reader.indexOf('class="pr-hover-card"')
const cardEnd = reader.indexOf('<!-- 选中文字浮动工具条 -->')
const cardTemplate = reader.slice(reader.lastIndexOf('<div', cardStart), cardEnd)

const approx = (actual, expected, message) => {
  assert.ok(Math.abs(actual - expected) < 1e-9, message || `${actual} ≈ ${expected}`)
}

// ---------------------------------------------------------------------------
// 1~3. 来源只认结构化 origin
// ---------------------------------------------------------------------------
test('origin=ai maps to the AI source label', () => {
  assert.equal(annotationSource({ origin: 'ai' }).origin, 'ai')
  assert.equal(annotationSource({ origin: 'ai' }).label, 'AI 批注')
  assert.equal(buildHoverItem({ id: 1, origin: 'ai', note: '解读' }).sourceLabel, 'AI 批注')
})

test('origin=user maps to the user source label', () => {
  assert.equal(annotationSource({ origin: 'user' }).origin, 'user')
  assert.equal(annotationSource({ origin: 'user' }).label, '我的批注')
})

test('missing origin falls back to the user label, never guessed from the note text', () => {
  assert.equal(annotationSource({}).origin, 'user')
  assert.equal(annotationSource({}).label, '我的批注')
  assert.equal(annotationSource({ origin: null }).label, '我的批注')
  assert.equal(annotationSource({ origin: 'unknown' }).label, '我的批注')
  // 旧 ai note 里带 "💡 AI 解读：" 前缀，但没有 origin 字段时仍按用户批注兼容，
  // 反过来 origin=user 的 note 里出现 "AI" 字样也不能被识别成 AI 批注。
  assert.equal(annotationSource({ note: '💡 AI 解读：旧数据' }).origin, 'user')
  assert.equal(annotationSource({ origin: 'user', note: '💡 AI 解读：xxx' }).origin, 'user')
})

// ---------------------------------------------------------------------------
// 4. 无 note 时不出现空白浮层
// ---------------------------------------------------------------------------
test('annotations without a note show an explicit only-highlight / only-underline state', () => {
  const highlight = buildHoverItem({ id: 1, origin: 'user', mark_type: 'highlight', note: '', text: '被标注的原文' })
  assert.equal(highlight.hasNote, false)
  assert.equal(highlight.emptyLabel, '仅高亮')
  assert.equal(highlight.text, '被标注的原文')

  const underline = buildHoverItem({ id: 2, origin: 'user', mark_type: 'underline', note: '   ', text: '' })
  assert.equal(underline.hasNote, false)
  assert.equal(underline.emptyLabel, '仅划线')

  // 有 note 时不再显示 emptyLabel，且换行原样保留
  const withNote = buildHoverItem({ id: 3, note: '第一行\n第二行', text: '原文' })
  assert.equal(withNote.hasNote, true)
  assert.equal(withNote.emptyLabel, '')
  assert.equal(withNote.note, '第一行\n第二行')

  // 只有原文时截断为预览
  assert.equal(buildHoverItem({ id: 4, note: '', text: 'x'.repeat(200) }).text.length, 61)
})

// ---------------------------------------------------------------------------
// 5. 同一 Annotation 的多矩形 / 跨页分段只展示一次
// ---------------------------------------------------------------------------
test('one annotation with several rects is shown exactly once', () => {
  const annotation = { id: 7, origin: 'user', mark_type: 'highlight', note: '合并展示', text: '原文' }
  const items = buildHoverItems([annotation, annotation, annotation, annotation])
  assert.equal(items.length, 1)
  assert.equal(items[0].id, 7)
  assert.equal(items[0].note, '合并展示')
})

// ---------------------------------------------------------------------------
// 6. 重叠位置的不同 Annotation 不合并
// ---------------------------------------------------------------------------
test('overlapping annotations at the same point are all reported and never merged', () => {
  const entries = [
    { id: 11, markType: 'highlight', rect: { x: 0.2, y: 0.3, w: 0.4, h: 0.05 } },
    { id: 12, markType: 'highlight', rect: { x: 0.25, y: 0.32, w: 0.4, h: 0.05 } },
    { id: 13, markType: 'underline', rect: { x: 0.2, y: 0.5, w: 0.4, h: 0.02 } },
  ]
  const tolerance = hoverTolerance({ width: 600, height: 800 })
  const hits = hitTestEntries(entries, { x: 0.3, y: 0.34 }, tolerance)
  // DOM 覆盖顺序不再决定可见性：两条都被命中
  assert.deepEqual(hits.map((h) => h.id), [11, 12])
  const items = buildHoverItems(hits)
  assert.equal(items.length, 2)
  assert.notEqual(items[0].id, items[1].id)
})

// ---------------------------------------------------------------------------
// 7. AI 长批注保留内容，但受 UI 最大尺寸约束
// ---------------------------------------------------------------------------
test('a long AI note is preserved verbatim while the card caps its own size', () => {
  const note = '💡 AI 解读：' + '这是一段很长的解读，用来验证卡片内部滚动。'.repeat(80)
  const item = buildHoverItem({ id: 9, origin: 'ai', mark_type: 'highlight', note, text: '原文' })
  assert.equal(item.note, note)
  assert.equal(item.note.length, note.length)
  assert.equal(item.hasNote, true)

  assert.equal(HOVER_CARD_MAX_HEIGHT, 220)
  assert.equal(HOVER_CARD_MAX_WIDTH, 320)
  // 组件侧：卡片有最大宽高且内部滚动，不会撑破阅读区
  assert.match(reader, /\.pr-hover-card \{[\s\S]*?max-width: min\(320px, 78vw\);[\s\S]*?max-height: 220px;[\s\S]*?overflow: auto;/)
  assert.match(reader, /\.pr-hover-note \{ white-space: pre-wrap; word-break: break-word;/)
})

// ---------------------------------------------------------------------------
// 8. 换书 / 删除批注后不残留旧悬浮内容
// ---------------------------------------------------------------------------
test('deleted annotations are pruned so no stale hover content survives', () => {
  const first = { id: 1, note: 'a' }
  const second = { id: 2, note: 'b' }
  assert.deepEqual(retainHoverIds([1, 2], [first, second]), [1, 2])
  assert.deepEqual(retainHoverIds([1, 2], [second]), [2])
  // 命中的批注全被删除 → null，调用方必须关闭浮层
  assert.equal(retainHoverIds([1], [second]), null)
  assert.equal(retainHoverIds([1, 2], []), null)

  assert.match(reader, /retainHoverIds\(hoverCard\.value\.ids, annotations\.value\)/)
  assert.match(reader, /watch\(\(\) => props\.src[\s\S]{0,90}closeHoverCard\(\)/)
  assert.match(reader, /watch\(\(\) => props\.bookId[\s\S]{0,60}closeHoverCard\(\)/)
  assert.match(reader, /watch\(mode, \(nv\) => \{\s*closeHoverCard\(\)/)
  // Esc、滚出可视区、卸载都会关闭
  assert.match(reader, /e\.key === 'Escape' && hoverCard\.value\.visible/)
  assert.match(reader, /cancelAnimationFrame\(hoverScanFrame\)/)
})

// ---------------------------------------------------------------------------
// 9. 高亮与划线都能命中
// ---------------------------------------------------------------------------
test('both highlight rects and underline rects are hoverable', () => {
  const entries = [
    { id: 1, markType: 'highlight', rect: { x: 0.1, y: 0.1, w: 0.5, h: 0.04 } },
    { id: 2, markType: 'underline', rect: { x: 0.1, y: 0.2, w: 0.5, h: 0.02 } },
  ]
  const tolerance = hoverTolerance({ width: 600, height: 800 })
  assert.equal(HOVER_HIT_TOLERANCE_PX.y, 6)

  const insideHighlight = hitTestEntries(entries, { x: 0.3, y: 0.12 }, tolerance)
  assert.deepEqual(insideHighlight.map((h) => h.id), [1])

  // 划线渲染在 rect 底边上，命中带需要上下留量
  const onUnderline = hitTestEntries(entries, { x: 0.3, y: 0.22 }, tolerance)
  assert.deepEqual(onUnderline.map((h) => h.id), [2])
  // 划线矩形内部（远离底线）不算命中，避免误报
  assert.equal(hitTestEntries(entries, { x: 0.3, y: 0.201 }, tolerance).length, 0)
  // 完全在页面空白处
  assert.equal(hitTestEntries(entries, { x: 0.9, y: 0.9 }, tolerance).length, 0)
})

// ---------------------------------------------------------------------------
// 附加：定位与锚点
// ---------------------------------------------------------------------------
test('the card anchor is the union of every hit rect', () => {
  const union = unionRect([
    { x: 0.1, y: 0.2, w: 0.2, h: 0.02 },
    { x: 0.35, y: 0.21, w: 0.1, h: 0.02 },
  ])
  assert.equal(union.x, 0.1)
  assert.equal(union.y, 0.2)
  approx(union.w, 0.35)
  approx(union.h, 0.03)
  assert.equal(unionRect([]), null)
  assert.equal(unionRect([{ x: 0, y: 0, w: 0, h: 0 }]), null)
})

test('card placement avoids the reader edges and the viewport', () => {
  const bounds = { left: 40, top: 60, right: 840, bottom: 660 }
  const card = { width: 320, height: 220 }

  const below = placeHoverCard({ anchor: { left: 300, top: 200, width: 120, height: 18 }, card, bounds })
  assert.equal(below.placement, 'below')
  assert.ok(below.left >= bounds.left && below.left + card.width <= bounds.right)
  assert.ok(below.top >= bounds.top && below.top + card.height <= bounds.bottom)
  // 水平方向以标注为中心
  approx(below.left, 300 + (120 - 320) / 2)

  const nearBottom = placeHoverCard({ anchor: { left: 300, top: 600, width: 120, height: 18 }, card, bounds })
  assert.equal(nearBottom.placement, 'above')
  assert.ok(nearBottom.top + card.height <= 600)

  const nearTop = placeHoverCard({ anchor: { left: 60, top: 62, width: 120, height: 18 }, card, bounds })
  assert.ok(nearTop.left >= bounds.left, '不越出左侧')
  assert.ok(nearTop.top >= bounds.top, '不越出顶部')

  const corner = placeHoverCard({ anchor: { left: 820, top: 640, width: 20, height: 16 }, card, bounds })
  assert.ok(corner.left + card.width <= bounds.right + 1e-9, '不越出右侧')
  assert.ok(corner.top + card.height <= bounds.bottom + 1e-9, '不越出底部')

  // 上下都放不下（阅读区比卡片还矮）时退化为覆盖，但仍夹在边界内
  const tiny = placeHoverCard({
    anchor: { left: 0, top: 100, width: 10, height: 10 },
    card,
    bounds: { left: 0, top: 0, right: 200, bottom: 150 },
  })
  assert.equal(tiny.placement, 'overlay')
  assert.ok(tiny.left >= 0 && tiny.top >= 0)
  assert.ok(tiny.left + 200 <= 200 && tiny.top + 150 <= 150)
})

test('hover delays stay short enough to avoid flicker without feeling laggy', () => {
  assert.ok(HOVER_OPEN_DELAY_MS > 0 && HOVER_OPEN_DELAY_MS <= 120)
  assert.ok(HOVER_CLOSE_DELAY_MS > 0 && HOVER_CLOSE_DELAY_MS <= 200)
})

// ---------------------------------------------------------------------------
// 12. 不是原生 title-only 方案
// ---------------------------------------------------------------------------
test('hover content is rendered as text inside the card, not via a native title', () => {
  assert.doesNotMatch(reader, /:title="st\.ann\.text/)
  assert.ok(cardTemplate.length > 0, '卡片模板必须存在')
  assert.match(cardTemplate, /\{\{ item\.sourceLabel \}\}/)
  assert.match(cardTemplate, /\{\{ item\.kindLabel \}\}/)
  assert.match(cardTemplate, /\{\{ item\.note \}\}/)
  assert.match(cardTemplate, /\{\{ item\.emptyLabel \}\}/)
  assert.match(cardTemplate, /\{\{ item\.text \}\}/)
  // 批注内容一律走文本插值，不存在把用户/AI 内容注入 innerHTML 的路径
  assert.doesNotMatch(cardTemplate, /v-html/)
  // 来源不靠颜色区分：有文字标签 + 可访问标签
  assert.match(cardTemplate, /class="pr-hover-source"/)
  assert.match(reader, /:aria-label="st\.focusable \? st\.label : null"/)
})

test('source labels are textual and exposed to assistive tech, not colour-only', () => {
  const ai = annotationAriaLabel({ origin: 'ai', mark_type: 'highlight', note: 'AI 解读内容' })
  assert.match(ai, /AI 批注/)
  assert.match(ai, /高亮/)
  assert.match(ai, /AI 解读内容/)

  const mine = annotationAriaLabel({ mark_type: 'underline', note: '', text: '划线原文' })
  assert.match(mine, /我的批注/)
  assert.match(mine, /划线/)
  assert.match(mine, /划线原文/)
})

// ---------------------------------------------------------------------------
// 13. 不破坏文字选择：矩形保持 pointer-events:none，命中走事件委托
// ---------------------------------------------------------------------------
test('highlight rects keep pointer-events:none and hit testing goes through delegation', () => {
  const block = reader.match(/\.pr-hl \{[^}]*\}/)
  assert.ok(block, '.pr-hl 基础样式必须存在')
  assert.match(block[0], /pointer-events: none/)
  assert.doesNotMatch(reader, /\.pr-hl\s*\{[^}]*pointer-events: auto/)
  // 没有悬停态直接改底色（原有高亮颜色不被永久改变）
  assert.doesNotMatch(reader, /\.pr-hl:hover/)

  // 命中检测：单个 .pr-body 委托 + rAF 节流 + 页面坐标，而不是给每个矩形挂监听
  assert.match(reader, /@mousemove="onBodyMouseMove"/)
  assert.match(reader, /requestAnimationFrame\(\(\) => \{\s*hoverScanFrame = null/)
  assert.match(reader, /closest\?\.\('\.pr-page'\)/)
  assert.match(reader, /hitTestEntries\(/)
  assert.doesNotMatch(reader, /\.pr-hl[^\n]*addEventListener/)

  // 拖选文字过程中不弹卡
  assert.match(reader, /if \(sel && !sel\.isCollapsed\) return/)
  // 按下鼠标即收起，浮层不会挡住拖选
  assert.match(reader, /const onBodyMouseDown = \(\) => \{[\s\S]{0,120}closeHoverCard\(\)/)
})

// ---------------------------------------------------------------------------
// 10 / 11 的回归由 tests/pdf_annotations.test.js 锁定；这里补一条同源守卫，
// 防止悬浮卡改造把 hlIndex 的「先 set 再 push」写回回归形态。
// ---------------------------------------------------------------------------
test('the hover rewrite keeps the hlIndex set-then-push contract intact', () => {
  assert.match(reader, /if \(!m\.has\(pg\)\) m\.set\(pg, \[\]\)/)
  assert.match(reader, /m\.get\(pg\)\.push\(/)
  // 悬浮卡只读 hlIndex，不额外遍历全量标注
  assert.match(reader, /const hlStyles = \(p\) => hlIndex\.value\.get\(Number\(p\)\) \|\| \[\]/)
})
