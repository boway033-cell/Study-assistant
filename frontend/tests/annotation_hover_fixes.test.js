// Codex 审查返工（2026-09-13）的三项修复回归：
//   1. mousedown 接线：模板必须绑定到真正调用 closeHoverCard 的处理器；
//   2. 过期 hoverOpenTimer：A→B / 离开 / 快速扫过 / 关闭后不得弹旧卡；
//   3. 定位 bounds：卡片只能在「正文滚动区 ∩ 组件根节点 ∩ 视口」内出现。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  hoverCardBounds, hoverOpenIsStale, hoverOpenMatches, placeHoverCard,
  HOVER_CARD_MAX_HEIGHT, HOVER_CARD_MAX_WIDTH, HOVER_OPEN_DELAY_MS,
} from '../src/utils/annotationHover.js'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')
const logic = readFileSync(resolve(here, '../src/utils/annotationHover.js'), 'utf8')

/** 按 braces 配对截取 `const NAME = ... { ... }` 的函数体（跳过字符串字面量）。 */
function extractHandlerBody(src, name) {
  const marker = `const ${name} =`
  const start = src.indexOf(marker)
  if (start === -1) return null
  const open = src.indexOf('{', start)
  if (open === -1) return null
  let depth = 0
  let quote = null
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (quote) {
      if (ch === quote && src[i - 1] !== '\\') quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { quote = ch; continue }
    if (ch === '{') depth++
    else if (ch === '}') { depth--; if (depth === 0) return src.slice(open, i + 1) }
  }
  return null
}

// ---------------------------------------------------------------------------
// 1. mousedown 接线契约
// ---------------------------------------------------------------------------
test('template mousedown is wired to the handler that actually closes the hover card', () => {
  const binding = reader.match(/@mousedown="([A-Za-z_$][\w$]*)"/)
  assert.ok(binding, '模板里应存在 @mousedown 绑定')
  const handler = binding[1]
  assert.notEqual(handler, 'onMouseDown', '不允许绑定空的占位处理器')
  const body = extractHandlerBody(reader, handler)
  assert.ok(body, `模板绑定的 ${handler} 必须在脚本里定义`)
  assert.match(body, /closeHoverCard\(\)/, `${handler} 必须调用 closeHoverCard 关闭悬浮卡`)
  assert.match(body, /hoverCardPointerInside = false/, '按下时还应复位卡片内部驻留标记')
})

test('the previous empty placeholder handler is gone, not kept alongside', () => {
  assert.ok(!reader.includes('const onMouseDown ='), '空处理器 onMouseDown 必须删除，不能保留两套命名')
  assert.ok(reader.includes('@mouseup="onMouseUp"'), 'mouseup 的选区工具条逻辑保持不变')
})

// ---------------------------------------------------------------------------
// 2. 过期 hoverOpenTimer
// ---------------------------------------------------------------------------
test('hoverOpenIsStale / hoverOpenMatches decide purely by key equality', () => {
  assert.equal(hoverOpenIsStale('1@1', '2@1'), true, 'A → B：旧计时器过期')
  assert.equal(hoverOpenIsStale('1@1', '1@1'), false, '同一目标内移动：计时器保留')
  assert.equal(hoverOpenIsStale('', '2@1'), false, '无待打开计划时谈不上过期')
  assert.equal(hoverOpenMatches('1@1', '1@1'), true)
  assert.equal(hoverOpenMatches('1@1', '2@1'), false)
  assert.equal(hoverOpenMatches('', '1@1'), false, '空计划永不匹配，防止无 key 放行')
})

/**
 * 与 PdfReader.scanHoverAt 相同的调度决策（用纯函数表达，手动时钟推进）。
 * schedule = 命中分支；leave = !found 分支；close = closeHoverCard；
 * advanceTo = 计时器触发（含 epoch / key / 指针位置三道校验）。
 */
function createScheduler() {
  const s = { pendingKey: '', timer: null, epoch: 0, opened: [], pointerKey: '' }
  return {
    s,
    schedule(key, now) {
      if (s.timer && hoverOpenIsStale(s.pendingKey, key)) { s.timer = null; s.pendingKey = '' }
      if (s.timer) return                       // 同一目标内移动：沿用已排队的计时器
      s.pendingKey = key
      s.timer = { key, epoch: s.epoch, fireAt: now + HOVER_OPEN_DELAY_MS }
    },
    leave() { s.timer = null; s.pendingKey = '' },
    close() { s.epoch += 1; s.timer = null; s.pendingKey = '' },
    advanceTo(now) {
      const t = s.timer
      if (!t || now < t.fireAt) return
      s.timer = null
      const expected = s.pendingKey
      s.pendingKey = ''
      if (t.epoch !== s.epoch) return                          // 排队期间被关闭
      if (!hoverOpenMatches(expected, t.key)) return           // 计划与登记不一致
      if (!hoverOpenMatches(expected, s.pointerKey)) return    // 指针已移走
      s.opened.push(t.key)
    },
  }
}

test('(a) A → 空白：离开后待打开计时器立即作废，不再短暂弹卡', () => {
  const sched = createScheduler()
  sched.schedule('1@1', 0)
  sched.leave()
  sched.advanceTo(500)
  assert.deepEqual(sched.s.opened, [], '空白处不得打开任何卡片')
  assert.equal(sched.s.timer, null)
})

test('(a-对照) 一直停在 A 上：计时器正常打开 A（证明模拟器路径有效）', () => {
  const sched = createScheduler()
  sched.schedule('1@1', 0)
  sched.s.pointerKey = '1@1'
  sched.advanceTo(HOVER_OPEN_DELAY_MS + 1)
  assert.deepEqual(sched.s.opened, ['1@1'])
})

test('(b) A → B：A 的计时器作废重排，最终只弹出 B', () => {
  const sched = createScheduler()
  sched.schedule('1@1', 0)
  sched.schedule('2@1', 30)               // 30ms 时移到 B
  sched.s.pointerKey = '2@1'
  sched.advanceTo(500)
  assert.deepEqual(sched.s.opened, ['2@1'], '旧卡 A 不允许出现')
})

test('(c) 快速扫过 A → B → C：只打开最后停留的 C', () => {
  const sched = createScheduler()
  sched.schedule('1@1', 0)
  sched.schedule('2@1', 10)
  sched.schedule('3@1', 20)
  sched.s.pointerKey = '3@1'
  sched.advanceTo(500)
  assert.deepEqual(sched.s.opened, ['3@1'])
})

test('(d) 关闭后旧计时器不得复活卡片', () => {
  const sched = createScheduler()
  sched.schedule('1@1', 0)
  sched.close()
  sched.advanceTo(500)
  assert.deepEqual(sched.s.opened, [], 'closeHoverCard 后 timer 应已清除')

  // 极端情形：计时器已经进入回调但组件恰好刚被关闭（epoch 已变），同样不放行。
  const sched2 = createScheduler()
  sched2.schedule('1@1', 0)
  const timer = sched2.s.timer
  sched2.s.epoch += 1                     // 模拟回调与关闭竞态
  sched2.advanceTo(timer.fireAt + 1)
  assert.deepEqual(sched2.s.opened, [])
})

test('scanHoverAt wiring: leave branch cancels the open timer, closeHoverCard cancels too', () => {
  const scanBody = extractHandlerBody(reader, 'scanHoverAt')
  assert.ok(scanBody, 'scanHoverAt 应存在')
  const leaveBranch = scanBody.match(/if \(!found\) \{([\s\S]*?)return\s*\}/)
  assert.ok(leaveBranch, '!found 分支应显式成块')
  assert.match(leaveBranch[1], /cancelHoverOpen\(\)/, '离开命中区必须先作废待打开计时器')
  assert.match(leaveBranch[1], /scheduleHoverClose\(\)/)

  const closeBody = extractHandlerBody(reader, 'closeHoverCard')
  assert.match(closeBody, /cancelHoverOpen\(\)/, '关闭浮层必须同时作废待打开计时器')
  assert.match(closeBody, /hoverEpoch \+= 1/, '关闭必须推进 epoch，作废排队中的回调')

  const cancelBody = extractHandlerBody(reader, 'cancelHoverOpen')
  assert.match(cancelBody, /clearTimeout\(hoverOpenTimer\)/)
  assert.match(cancelBody, /hoverPendingKey = ''/)

  assert.match(scanBody, /hoverOpenIsStale\(hoverPendingKey, key\)/, 'A→B 必须检测过期并重排')
  assert.match(scanBody, /hoverOpenMatches\(expected, key\)/, '触发前必须校验计划 key')
  assert.match(scanBody, /hoverKeyOf\(live\) !== expected/, '触发前必须按当前指针位置复检')
})

// ---------------------------------------------------------------------------
// 3. 阅读区 mouseleave：先作废待打开计时器，再走延迟关闭（Codex R2）
// ---------------------------------------------------------------------------
test('onBodyMouseLeave wiring: cancels the pending open before scheduling close', () => {
  const binding = reader.match(/@mouseleave="([A-Za-z_$][\w$]*)"/)
  assert.ok(binding, '.pr-body 应存在 @mouseleave 绑定')
  assert.equal(binding[1], 'onBodyMouseLeave', '绑定应指向 onBodyMouseLeave（首个 mouseleave 绑定在 .pr-body 上）')
  const body = extractHandlerBody(reader, binding[1])
  assert.ok(body, `绑定的 ${binding[1]} 必须在脚本里定义`)
  assert.match(body, /cancelHoverOpen\(\)/, '离开阅读区必须先作废待打开计时器')
  assert.match(body, /scheduleHoverClose\(\)/, '随后仍要走延迟关闭语义')
  const cancelAt = body.indexOf('cancelHoverOpen()')
  const scheduleAt = body.indexOf('scheduleHoverClose()')
  assert.ok(cancelAt !== -1 && scheduleAt !== -1 && cancelAt < scheduleAt,
    'cancelHoverOpen 必须先于 scheduleHoverClose 调用')
})

test('pointer enters A, reader mouseleave fires before the open delay expires → card never opens', () => {
  // 用扩展的调度器模拟 onBodyMouseLeave：清掉待打开计时器，并排一个延迟关闭。
  const sched = createScheduler()
  sched.schedule('1@1', 0)                    // 指针进入高亮 A，打开计时器排队
  assert.ok(sched.s.timer, '前提：A 的打开计时器已排队')

  sched.leave()                               // 鼠标离开 .pr-body（cancelHoverOpen）
  assert.equal(sched.s.timer, null, 'mouseleave 必须立刻清掉待打开计时器')
  sched.s.pointerKey = ''

  // 等待超过打开延迟：卡片从未打开
  sched.advanceTo(HOVER_OPEN_DELAY_MS + 100)
  assert.deepEqual(sched.s.opened, [], '打开延迟到期后也不得弹出卡片')

  // 延迟关闭到期：只有关闭动作发生，没有任何打开
  sched.close()
  assert.deepEqual(sched.s.opened, [], '整个生命周期内卡片从未打开')
})

test('anti-flicker semantics preserved: entering the card keeps it open (close suppressed)', () => {
  const body = extractHandlerBody(reader, 'scheduleHoverClose')
  assert.match(body, /if \(hoverPinned\.value \|\| hoverCardPointerInside\) return/,
    '卡片已固定或指针在卡片内部时不得排关闭计时器（防闪烁语义保留）')
  assert.match(body, /HOVER_CLOSE_DELAY_MS/, '关闭仍走原有延迟')
})

// ---------------------------------------------------------------------------
// 4. 定位 bounds：正文滚动区 ∩ 组件根节点 ∩ 视口
// ---------------------------------------------------------------------------
const VP = { width: 1440, height: 900 }
const CARD = { width: HOVER_CARD_MAX_WIDTH, height: HOVER_CARD_MAX_HEIGHT }

test('bounds intersect the scroller, so an expanded TOC is never covered', () => {
  // 左侧目录展开后占 320px，顶部工具栏占 56px：scroller 被挤到 (320, 56) 起。
  const b = hoverCardBounds({
    root: { left: 0, top: 0, right: 1440, bottom: 900 },
    scroller: { left: 320, top: 56, right: 1440, bottom: 900 },
    viewport: VP,
  })
  assert.equal(b.left, 320, '卡片左界必须让开目录')
  assert.equal(b.top, 56, '卡片上界必须让开工具栏')
  assert.equal(b.right, 1440)
  assert.equal(b.bottom, 900)
})

test('bounds respect the viewport on the right / bottom / small windows', () => {
  const b = hoverCardBounds({
    root: { left: 0, top: 0, right: 2000, bottom: 1200 },   // root 比视口大
    scroller: { left: 320, top: 56, right: 2000, bottom: 1200 },
    viewport: { width: 1024, height: 640 },
  })
  assert.equal(b.right, 1024, '右缘不得超出视口')
  assert.equal(b.bottom, 640, '底缘不得超出视口')

  // 小窗口：整块阅读器比视口大，取交集后仍不越界。
  const small = hoverCardBounds({
    root: { left: -80, top: -40, right: 700, bottom: 500 },
    scroller: { left: -80, top: -40, right: 700, bottom: 500 },
    viewport: { width: 600, height: 400 },
  })
  assert.deepEqual(small, { left: 0, top: 0, right: 600, bottom: 400 })
})

test('degenerate intersection falls back to root ∩ viewport instead of a zero-area bounds', () => {
  const b = hoverCardBounds({
    root: { left: 0, top: 0, right: 1440, bottom: 900 },
    scroller: { left: 2000, top: 56, right: 2400, bottom: 900 },  // 完全在根节点外
    viewport: VP,
  })
  assert.deepEqual(b, { left: 0, top: 0, right: 1440, bottom: 900 })
})

test('placed card never covers the expanded TOC, top bar, right or bottom edges', () => {
  const root = { left: 0, top: 0, right: 1440, bottom: 900 }
  const scroller = { left: 320, top: 56, right: 1440, bottom: 900 }
  const bounds = hoverCardBounds({ root, scroller, viewport: VP })

  const anchors = [
    { name: '贴着目录右侧', left: 322, top: 300, width: 400, height: 24 },
    { name: '页面顶部', left: 400, top: 60, width: 400, height: 24 },
    { name: '页面底部', left: 400, top: 820, width: 400, height: 24 },
    { name: '页面右缘', left: 1300, top: 300, width: 120, height: 24 },
    { name: '角落', left: 1320, top: 850, width: 110, height: 20 },
  ]
  for (const { name, ...anchor } of anchors) {
    const placed = placeHoverCard({ anchor, card: CARD, bounds, gap: 8 })
    assert.ok(placed.left >= bounds.left - 1e-9, `${name}：卡片左缘不得进入目录 (${placed.left})`)
    assert.ok(placed.top >= bounds.top - 1e-9, `${name}：卡片上缘不得进入工具栏 (${placed.top})`)
    assert.ok(placed.left + CARD.width <= bounds.right + 1e-9, `${name}：卡片不得越过右缘`)
    assert.ok(placed.top + CARD.height <= bounds.bottom + 1e-9, `${name}：卡片不得越过底缘`)
  }
})

test('edge flipping is preserved with the clipped bounds (bottom anchor flips above)', () => {
  const bounds = hoverCardBounds({
    root: { left: 0, top: 0, right: 1440, bottom: 900 },
    scroller: { left: 320, top: 56, right: 1440, bottom: 900 },
    viewport: VP,
  })
  const low = placeHoverCard({
    anchor: { left: 400, top: 820, width: 400, height: 24 },
    card: CARD, bounds, gap: 8,
  })
  assert.equal(low.placement, 'above', '底部锚点翻转到上方')
  assert.ok(low.top + CARD.height <= 900 + 1e-9)

  const high = placeHoverCard({
    anchor: { left: 400, top: 60, width: 400, height: 24 },
    card: CARD, bounds, gap: 8,
  })
  assert.equal(high.placement, 'below', '顶部锚点放在下方')
})

test('positionHoverCard computes bounds from scroller ∩ root ∩ viewport (wiring contract)', () => {
  const body = extractHandlerBody(reader, 'positionHoverCard')
  assert.match(body, /hoverCardBounds\(\{ root: rootRect, scroller: scrollerRect, viewport \}\)/)
  assert.match(logic, /export function hoverCardBounds/, 'bounds 计算必须落在纯逻辑层，便于单测')
  // 锚点越界收起逻辑保留：卡片不会停在滚动区外的空白上。
  assert.match(body, /box\.left > sr\.right \|\| box\.right < sr\.left/)
})
