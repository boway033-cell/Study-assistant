<template>
  <div class="mindmap">
    <div class="mm-toolbar">
      <el-button-group>
        <el-button size="small" @click="zoomBy(-0.15)">−</el-button>
        <span class="mm-zoom">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" @click="zoomBy(0.15)">＋</el-button>
        <el-button size="small" @click="fitAll">适应</el-button>
        <el-button size="small" type="primary" plain @click="exportPng">⬇ 导出图片</el-button>
      </el-button-group>
      <span class="mm-tip">点击节点查看详情 · 滚轮缩放 · 拖动空白平移</span>
    </div>
    <div class="mm-canvas" ref="scroller" @mousedown="onPanStart" @mousemove="onPanMove" @mouseup="onPanEnd" @mouseleave="onPanEnd" @wheel="onWheel">
      <div class="mm-inner" :style="{ transform: 'scale(' + scale + ')' }">
        <svg :width="svgW" :height="svgH" class="mm-svg">
          <g v-for="n in flatNodes" :key="'line-' + n.id">
            <path v-for="c in n.children" :key="'p-' + c.id" :d="linkPath(n, c)" class="mm-link" />
          </g>
          <g v-for="n in flatNodes" :key="'node-' + n.id"
            :transform="'translate(' + n.x + ', ' + n.y + ')'" class="mm-node" @click="emit('select', n)">
            <title>{{ n.title }}</title>
            <rect :width="n.w" :height="n.h" rx="8" ry="8"
              :class="['mm-rect', 'mm-depth-' + n.depth, 'mm-m-' + (n.mastery || 'unknown'), { selected: n.id === selectedId }]" />
            <text :x="10" :y="n.h / 2 + 4" class="mm-text" :class="'mm-text-depth-' + n.depth">{{ typeIcon(n) }} {{ truncate(n.title) }}</text>
          </g>
        </svg>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  selectedId: { type: [Number, null], default: null },
})
const emit = defineEmits(['select'])

const H_GAP = 48
const NODE_H = 38
const V_GAP = 12
const PAD = 28
const FONT = 13  // 每字约 14px 宽

// 根据最长标题动态计算节点宽度（有上下限）
const maxTitleLen = computed(() => {
  let m = 0
  const walk = (nodes) => nodes.forEach(n => { m = Math.max(m, [...(n.title || '')].length); if (n.children?.length) walk(n.children) })
  walk(props.data)
  return m
})
const NODE_W = computed(() => Math.max(92, Math.min(210, maxTitleLen.value * 14 + 34)))

const scale = ref(0.9)
const scroller = ref(null)
let panning = false
let panStart = { x: 0, y: 0, sl: 0, st: 0 }

// 左→右布局，返回 { items, totalH }
function layout(items, depth) {
  let y = 0
  const out = []
  const w = NODE_W.value
  for (const it of items) {
    const sub = it.children?.length ? layout(it.children, depth + 1) : null
    const kidsH = sub ? sub.totalH : 0
    const blockH = Math.max(kidsH, NODE_H)
    const centerY = y + blockH / 2
    out.push({
      ...it,
      x: depth * (w + H_GAP),
      y: centerY - NODE_H / 2,
      w, h: NODE_H,
      depth,
      children: sub ? sub.items : [],
    })
    y += blockH + V_GAP
  }
  return { items: out, totalH: Math.max(0, y - V_GAP) }
}

const laid = computed(() => layout(props.data, 0))

const flatNodes = computed(() => {
  const out = []
  const walk = (nodes) => { for (const n of nodes) { out.push(n); if (n.children?.length) walk(n.children) } }
  walk(laid.value.items)
  return out
})

const maxDepth = computed(() => {
  let m = 0
  const walk = (nodes, d) => { for (const n of nodes) { m = Math.max(m, d); if (n.children?.length) walk(n.children, d + 1) } }
  walk(props.data, 0)
  return m
})
const svgW = computed(() => (maxDepth.value + 1) * (NODE_W.value + H_GAP) + PAD * 2)
const svgH = computed(() => laid.value.totalH + PAD * 2)  // 修复：用整棵树高度，子树不再被裁切

const typeIcon = (n) => {
  return { concept: '', theorem: '📐', point: '🎯', example: '📝', question: '❓' }[n.node_type] || ''
}

const truncate = (t) => {
  const max = Math.floor((NODE_W.value - 26) / 14)
  const s = [...(t || '')]
  return s.length > max ? s.slice(0, max - 1).join('') + '…' : (t || '')
}

const linkPath = (parent, child) => {
  const x1 = parent.x + parent.w
  const y1 = parent.y + parent.h / 2
  const x2 = child.x
  const y2 = child.y + child.h / 2
  const mx = (x1 + x2) / 2
  return 'M ' + x1 + ' ' + y1 + ' C ' + mx + ' ' + y1 + ', ' + mx + ' ' + y2 + ', ' + x2 + ' ' + y2
}

const zoomBy = (d) => {
  scale.value = Math.min(2, Math.max(0.4, Math.round((scale.value + d) * 100) / 100))
}

const fitAll = () => {
  if (!scroller.value || !svgW.value) return
  const w = scroller.value.clientWidth - 30
  const h = scroller.value.clientHeight - 30
  scale.value = Math.min(1.3, Math.max(0.3, Math.min(w / svgW.value, h / svgH.value)))
}

const onWheel = (e) => {
  e.preventDefault()
  zoomBy(e.deltaY > 0 ? -0.1 : 0.1)
}

const onPanStart = (e) => {
  if (e.target.tagName === 'rect' || e.target.tagName === 'text' || e.target.tagName === 'path' || e.target.tagName === 'title') return
  panning = true
  panStart = { x: e.clientX, y: e.clientY, sl: scroller.value.scrollLeft, st: scroller.value.scrollTop }
}
const onPanMove = (e) => {
  if (!panning || !scroller.value) return
  scroller.value.scrollLeft = panStart.sl - (e.clientX - panStart.x)
  scroller.value.scrollTop = panStart.st - (e.clientY - panStart.y)
}
const onPanEnd = () => { panning = false }

const exportPng = () => {
  const svg = document.querySelector('.mm-svg')
  if (!svg) return
  const xml = new XMLSerializer().serializeToString(svg)
  const svgBlob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(svgBlob)
  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = svg.clientWidth * 2
    canvas.height = svg.clientHeight * 2
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#F5F0E8'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    URL.revokeObjectURL(url)
    const a = document.createElement('a')
    a.href = canvas.toDataURL('image/png')
    a.download = '知识导图.png'
    a.click()
  }
  img.src = url
}
</script>

<style scoped>
.mindmap { display: flex; flex-direction: column; height: 100%; min-height: 420px; }
.mm-toolbar {
  display: flex; align-items: center; gap: 10px; padding: 6px 10px;
  background: var(--el-fill-color-light); border-radius: 10px 10px 0 0;
  border: 1px solid var(--el-border-color-lighter); flex-wrap: wrap;
}
.mm-zoom { font-size: 13px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.mm-tip { font-size: 12px; color: var(--el-text-color-secondary); margin-left: auto; }
.mm-canvas {
  flex: 1; overflow: auto; background: #F5F0E8;
  border: 1px solid var(--el-border-color-lighter); border-radius: 0 0 10px 10px;
  cursor: grab; user-select: none;
}
.mm-canvas:active { cursor: grabbing; }
.mm-inner { transform-origin: top left; width: max-content; padding: 12px; }
.mm-svg { display: block; }
.mm-link { fill: none; stroke: #C2A285; stroke-width: 1.5; }
.mm-rect { stroke: #D4C9B8; stroke-width: 1; cursor: pointer; }
.mm-rect.selected { stroke: #8B5A2B; stroke-width: 2.5; }
.mm-depth-0 { fill: #8B5A2B; }
.mm-depth-1 { fill: #5F8D5F; }
.mm-depth-2 { fill: #EDE5D8; }
.mm-depth-3 { fill: #F5F0E8; }
.mm-m-known { stroke: #34c39b; stroke-width: 2; }
.mm-m-fuzzy { stroke: #f6a94a; stroke-width: 2; }
.mm-m-miss { stroke: #f06565; stroke-width: 2; }
.mm-m-unknown { stroke: #D4C9B8; }
.mm-text { font-size: 13px; fill: #fff; pointer-events: none; }
.mm-text-depth-2, .mm-text-depth-3 { fill: #44473F; }
</style>
