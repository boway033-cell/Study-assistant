<template>
  <div class="mindmap">
    <div class="mm-toolbar">
      <el-button-group>
        <el-button size="small" @click="zoomBy(-0.15)">−</el-button>
        <span class="mm-zoom">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" @click="zoomBy(0.15)">＋</el-button>
        <el-button size="small" @click="fitAll">适应</el-button>
      </el-button-group>
      <span class="mm-tip">点击节点查看详情 · 滚轮缩放 · 拖动空白平移</span>
    </div>
    <div class="mm-canvas" ref="scroller" @mousedown="onPanStart" @mousemove="onPanMove" @mouseup="onPanEnd" @mouseleave="onPanEnd">
      <div class="mm-inner" :style="{ transform: `scale(${scale})` }">
        <svg :width="svgW" :height="svgH" class="mm-svg">
          <g v-for="n in flatNodes" :key="'line-' + n.id">
            <path v-for="c in n.children" :key="'p-' + c.id"
              :d="linkPath(n, c)" class="mm-link" />
          </g>
          <g v-for="n in flatNodes" :key="'node-' + n.id"
            :transform="`translate(${n.x}, ${n.y})`" class="mm-node" @click="emit('select', n)">
            <rect :width="n.w" :height="n.h" rx="8" ry="8"
              :class="['mm-rect', 'mm-depth-' + n.depth, { selected: n.id === selectedId }]" />
            <text :x="10" :y="n.h / 2" class="mm-text" :class="'mm-text-depth-' + n.depth">{{ n.title }}</text>
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

const NODE_W = 150
const NODE_H = 34
const H_GAP = 56
const V_GAP = 12
const PAD = 24

const scale = ref(0.9)
const scroller = ref(null)
let panning = false
let panStart = { x: 0, y: 0, sl: 0, st: 0 }

// 左→右布局：递归计算每棵子树的块高，父节点垂直居中于子树
function layout(items, depth) {
  let y = 0
  const out = []
  for (const it of items) {
    const kids = it.children?.length ? layout(it.children, depth + 1) : []
    const kidsH = kids.length ? kids[kids.length - 1].y + kids[kids.length - 1].h : 0
    const blockH = Math.max(kidsH, NODE_H)
    const centerY = y + blockH / 2
    out.push({
      ...it,
      x: depth * (NODE_W + H_GAP),
      y: centerY - NODE_H / 2,
      w: NODE_W,
      h: NODE_H,
      depth,
      children: kids,
    })
    y += blockH + V_GAP
  }
  return out
}

const laid = computed(() => layout(props.data, 0))

// 展平全部节点（含子节点）用于渲染
const flatNodes = computed(() => {
  const out = []
  const walk = (nodes) => {
    for (const n of nodes) {
      out.push(n)
      if (n.children?.length) walk(n.children)
    }
  }
  walk(laid.value)
  return out
})

const maxDepth = computed(() => {
  let m = 0
  const walk = (nodes, d) => { for (const n of nodes) { m = Math.max(m, d); if (n.children?.length) walk(n.children, d + 1) } }
  walk(props.data, 0)
  return m
})
const svgW = computed(() => (maxDepth.value + 1) * (NODE_W + H_GAP) + PAD * 2)
const svgH = computed(() => {
  const last = laid.value[laid.value.length - 1]
  return (last ? last.y + last.h : 100) + PAD * 2
})

const linkPath = (parent, child) => {
  const x1 = parent.x + parent.w
  const y1 = parent.y + parent.h / 2
  const x2 = child.x
  const y2 = child.y + child.h / 2
  const mx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`
}

const zoomBy = (d) => {
  scale.value = Math.min(2, Math.max(0.4, Math.round((scale.value + d) * 100) / 100))
}

const fitAll = () => {
  if (!scroller.value || !svgW.value) return
  const w = scroller.value.clientWidth - 30
  const h = scroller.value.clientHeight - 30
  scale.value = Math.min(1.4, Math.max(0.3, Math.min(w / svgW.value, h / svgH.value)))
}

const onPanStart = (e) => {
  if (e.target.tagName === 'rect' || e.target.tagName === 'text' || e.target.tagName === 'path') return
  panning = true
  panStart = { x: e.clientX, y: e.clientY, sl: scroller.value.scrollLeft, st: scroller.value.scrollTop }
}
const onPanMove = (e) => {
  if (!panning || !scroller.value) return
  scroller.value.scrollLeft = panStart.sl - (e.clientX - panStart.x)
  scroller.value.scrollTop = panStart.st - (e.clientY - panStart.y)
}
const onPanEnd = () => { panning = false }

</script>

<style scoped>
.mindmap { display: flex; flex-direction: column; height: 100%; min-height: 400px; }
.mm-toolbar {
  display: flex; align-items: center; gap: 10px; padding: 6px 10px;
  background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0;
  border: 1px solid var(--el-border-color-extra-light); flex-wrap: wrap;
}
.mm-zoom { font-size: 12px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.mm-tip { font-size: 12px; color: var(--el-text-color-secondary); margin-left: auto; }
.mm-canvas {
  flex: 1; overflow: auto; background: linear-gradient(135deg, #fafcfe 0%, #eef5f8 100%);
  border: 1px solid var(--el-border-color-extra-light); border-radius: 0 0 8px 8px;
  cursor: grab; user-select: none;
}
.mm-canvas:active { cursor: grabbing; }
.mm-inner { transform-origin: top left; width: max-content; padding: 8px; }
.mm-svg { display: block; }
.mm-link { fill: none; stroke: #a8c3d1; stroke-width: 1.5; }
.mm-rect { stroke: #b9cfda; stroke-width: 1; cursor: pointer; }
.mm-rect.selected { stroke: #c45656; stroke-width: 2.5; }
.mm-depth-0 { fill: #5b6ee8; }
.mm-depth-1 { fill: #5f9b8f; }
.mm-depth-2 { fill: #e3eef3; }
.mm-depth-3 { fill: #f4f8fa; }
.mm-text { font-size: 12px; fill: #fff; pointer-events: none; }
.mm-text-depth-2, .mm-text-depth-3 { fill: #2c3e50; }
</style>