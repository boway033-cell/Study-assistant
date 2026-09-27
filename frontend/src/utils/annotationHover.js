/**
 * PDF 批注悬浮卡的纯逻辑层。
 *
 * 这里只做「数据整形 + 命中判定 + 定位计算」，不接触 DOM，也不读写后端，
 * 便于在 Node 里独立做单元测试。组件（PdfReader.vue）只负责把事件坐标、
 * 页面矩形和这里的结果接起来。
 *
 * 关键约束：
 * - 来源只认结构化字段 `origin`，绝不从 note / text 的字符串里猜 "AI""💡"。
 * - 不修改任何历史 note 内容，只做展示。
 * - 同一 Annotation 的多矩形 / 跨页分段只展示一次（按 id 去重）。
 */

export const SOURCE_USER = 'user'
export const SOURCE_AI = 'ai'

export const SOURCE_LABEL_USER = '我的批注'
export const SOURCE_LABEL_AI = 'AI 批注'

/** 悬浮卡尺寸上限：AI 批注可能很长，超出部分在卡片内部滚动。 */
export const HOVER_CARD_MAX_WIDTH = 320
export const HOVER_CARD_MAX_HEIGHT = 220

/** 悬浮卡展示的原文上下文长度。 */
export const HOVER_TEXT_PREVIEW_CHARS = 60

/** 命中容差（像素）：高亮按矩形本身命中，划线是一条细线，需要上下留量。 */
export const HOVER_HIT_TOLERANCE_PX = { x: 4, y: 6 }

/** 打开 / 关闭的短延迟：避免鼠标扫过页面时闪烁，同时不产生明显迟钝感。 */
export const HOVER_OPEN_DELAY_MS = 70
export const HOVER_CLOSE_DELAY_MS = 140

const KIND_LABEL = { highlight: '高亮', underline: '划线' }

const finiteRect = (rect) =>
  !!rect && [rect.x, rect.y, rect.w, rect.h].every((n) => Number.isFinite(n)) && rect.w > 0 && rect.h > 0

/**
 * 解析批注来源。`origin === "ai"` 才是 AI 批注；其余（含字段缺失、旧数据、
 * 未知取值）一律按用户批注兼容。
 */
export function annotationSource(annotation) {
  const origin = annotation?.origin === SOURCE_AI ? SOURCE_AI : SOURCE_USER
  return origin === SOURCE_AI
    ? { origin, label: SOURCE_LABEL_AI }
    : { origin, label: SOURCE_LABEL_USER }
}

/** 标注类型（高亮 / 划线）。未知取值按高亮处理，与 hlIndex 的既有默认一致。 */
export function annotationKind(annotation) {
  const kind = annotation?.mark_type === 'underline' ? 'underline' : 'highlight'
  return { kind, label: KIND_LABEL[kind] }
}

/** 有内容的 note 原样返回（保留换行），空白视为无批注。 */
export function noteText(annotation) {
  const note = typeof annotation?.note === 'string' ? annotation.note : ''
  return note.trim() ? note : ''
}

/** 被标注原文的一小段上下文（压缩空白后截断）。 */
export function textPreview(annotation, limit = HOVER_TEXT_PREVIEW_CHARS) {
  const raw = typeof annotation?.text === 'string' ? annotation.text : ''
  const flat = raw.replace(/\s+/g, ' ').trim()
  if (!flat) return ''
  return flat.length > limit ? flat.slice(0, limit) + '…' : flat
}

/** 可访问标签：文字化来源 + 类型 + 内容摘要，供键盘 focus 的矩形使用。 */
export function annotationAriaLabel(annotation, limit = 120) {
  const { label } = annotationSource(annotation)
  const { label: kindLabel } = annotationKind(annotation)
  const body = noteText(annotation) || textPreview(annotation)
  const head = `${label}（${kindLabel}）`
  if (!body) return head
  const flat = body.replace(/\s+/g, ' ').trim()
  return `${head}：${flat.length > limit ? flat.slice(0, limit) + '…' : flat}`
}

/**
 * 单条批注 → 悬浮卡条目。
 * 无 note 时给出明确的「仅高亮 / 仅划线」，避免渲染空白浮层。
 */
export function buildHoverItem(annotation) {
  const source = annotationSource(annotation)
  const kind = annotationKind(annotation)
  const note = noteText(annotation)
  const preview = textPreview(annotation)
  return {
    id: annotation?.id,
    origin: source.origin,
    sourceLabel: source.label,
    kind: kind.kind,
    kindLabel: kind.label,
    markType: kind.kind,
    note,
    hasNote: !!note,
    emptyLabel: note ? '' : kind.kind === 'underline' ? '仅划线' : '仅高亮',
    text: preview,
    fullText: typeof annotation?.text === 'string' ? annotation.text : '',
    ariaLabel: annotationAriaLabel(annotation),
  }
}

/**
 * 命中列表 → 展示列表。按 Annotation ID 去重，因此同一条批注的多矩形、
 * 跨页分段都只会出现一次；不同 Annotation 即使重叠也各自保留。
 */
export function buildHoverItems(annotations) {
  const items = []
  const seen = new Set()
  for (const annotation of annotations || []) {
    if (!annotation || annotation.id == null) continue
    if (seen.has(annotation.id)) continue
    seen.add(annotation.id)
    items.push(buildHoverItem(annotation))
  }
  return items
}

/**
 * 页面坐标命中检测。
 *
 * @param {Array} entries 当前页的 hlIndex 条目：[{ id, markType, rect:{x,y,w,h} }]
 * @param {{x:number,y:number}} point 归一化页面坐标（0~1）
 * @param {{x:number,y:number}} tolerance 归一化容差
 * @returns {Array} 命中的条目，按 entries 顺序、按 id 去重
 */
export function hitTestEntries(entries, point, tolerance) {
  const hits = []
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return hits
  const tolX = Math.max(0, tolerance?.x || 0)
  const tolY = Math.max(0, tolerance?.y || 0)
  const seen = new Set()
  for (const entry of entries || []) {
    if (!finiteRect(entry?.rect)) continue
    const r = entry.rect
    const underline = entry.markType === 'underline'
    // 划线渲染在 rect 底边上（height:0 + border-bottom），命中区要上下各留一点容差。
    const top = underline ? r.y + r.h - tolY : r.y
    const bottom = underline ? r.y + r.h + tolY : r.y + r.h
    const left = underline ? r.x - tolX : r.x
    const right = underline ? r.x + r.w + tolX : r.x + r.w
    if (point.x < left || point.x > right || point.y < top || point.y > bottom) continue
    if (seen.has(entry.id)) continue
    seen.add(entry.id)
    hits.push(entry)
  }
  return hits
}

/** 像素容差 → 归一化容差（页面尺寸未知时返回 0，退化为精确命中）。 */
export function hoverTolerance(pageRect, pixels = HOVER_HIT_TOLERANCE_PX) {
  const w = pageRect?.width || 0
  const h = pageRect?.height || 0
  return {
    x: w > 0 ? (pixels?.x || 0) / w : 0,
    y: h > 0 ? (pixels?.y || 0) / h : 0,
  }
}

/** 多个归一化矩形的并集，用于把卡片锚在多命中区域的整体附近。 */
export function unionRect(rects) {
  const valid = (rects || []).filter(finiteRect)
  if (!valid.length) return null
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const r of valid) {
    x0 = Math.min(x0, r.x)
    y0 = Math.min(y0, r.y)
    x1 = Math.max(x1, r.x + r.w)
    y1 = Math.max(y1, r.y + r.h)
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/**
 * 悬浮卡定位：默认贴在标注下方并水平居中，放不下时翻到上方，
 * 最后无论如何都把结果夹在 bounds 内（阅读器边缘 ∩ 浏览器可视区）。
 *
 * @returns {{left:number, top:number, placement:'below'|'above'|'overlay'}}
 */
export function placeHoverCard({ anchor, card, bounds, gap = 8 }) {
  const boundW = Math.max(0, bounds.right - bounds.left)
  const boundH = Math.max(0, bounds.bottom - bounds.top)
  const width = Math.min(card?.width || 0, boundW)
  const height = Math.min(card?.height || 0, boundH)
  const maxTop = bounds.bottom - height
  const maxLeft = bounds.right - width

  let placement = 'below'
  let top = anchor.top + anchor.height + gap
  if (top > maxTop) {
    const above = anchor.top - gap - height
    if (above >= bounds.top) {
      top = above
      placement = 'above'
    } else {
      top = Math.max(bounds.top, maxTop)
      placement = 'overlay'
    }
  }
  let left = anchor.left + (anchor.width - width) / 2
  left = Math.min(left, maxLeft)
  left = Math.max(bounds.left, left)
  top = Math.max(bounds.top, Math.min(top, maxTop))
  return { left, top, placement }
}

/**
 * 批注集合变化后收敛命中：被删除的 id 立刻丢弃。
 * 返回 null 表示「已无命中，浮层应当关闭」，避免残留旧内容。
 */
export function retainHoverIds(ids, annotations) {
  const alive = new Set((annotations || []).map((a) => a?.id))
  const kept = (ids || []).filter((id) => alive.has(id))
  return kept.length ? kept : null
}

/**
 * 待打开计时器的目标（pendingKey）与当前命中 key 不一致时，旧计时器必须作废重排。
 * 覆盖「从高亮 A 移到高亮 B」：不重排的话，A 的计时器触发后会在 B 上弹出 A 的卡片。
 */
export function hoverOpenIsStale(pendingKey, nextKey) {
  return Boolean(pendingKey) && pendingKey !== nextKey
}

/**
 * 打开计时器触发时的最后一道校验：计划打开的 key 必须仍然等于当前指针下的命中 key。
 * 用于防御计时器排队期间发生的一切状态变化（移走、关闭、批注被删）。
 */
export function hoverOpenMatches(pendingKey, currentKey) {
  return Boolean(pendingKey) && pendingKey === currentKey
}

/**
 * 悬浮卡允许出现的区域 = 正文滚动区 ∩ 组件根节点 ∩ 浏览器视口。
 *
 * 只用组件根节点做 bounds 会把卡片定位到左侧目录 / 顶部工具栏上方；
 * 这里必须先求三方交集。交集退化（面积 <= 0）时退回「根节点 ∩ 视口」，
 * 保证卡片总能落在某个合法区域内。
 *
 * @param {{left,top,right,bottom}} root 组件根节点矩形
 * @param {{left,top,right,bottom}=} scroller 正文滚动容器（.pr-body）矩形
 * @param {{width,height}} viewport window.innerWidth / innerHeight
 */
export function hoverCardBounds({ root, scroller, viewport }) {
  const vpW = Number.isFinite(viewport?.width) ? viewport.width : 0
  const vpH = Number.isFinite(viewport?.height) ? viewport.height : 0
  const base = {
    left: Math.max(Number.isFinite(root?.left) ? root.left : 0, 0),
    top: Math.max(Number.isFinite(root?.top) ? root.top : 0, 0),
    right: Math.min(Number.isFinite(root?.right) ? root.right : vpW, vpW),
    bottom: Math.min(Number.isFinite(root?.bottom) ? root.bottom : vpH, vpH),
  }
  const hasScroller = !!scroller &&
    [scroller.left, scroller.top, scroller.right, scroller.bottom].every(Number.isFinite)
  if (!hasScroller) return base
  const clipped = {
    left: Math.max(base.left, scroller.left),
    top: Math.max(base.top, scroller.top),
    right: Math.min(base.right, scroller.right),
    bottom: Math.min(base.bottom, scroller.bottom),
  }
  const valid = clipped.right - clipped.left > 0 && clipped.bottom - clipped.top > 0
  return valid ? clipped : base
}
