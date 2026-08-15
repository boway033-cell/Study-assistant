<!-- PdfReader v2.1：本地渲染为底层（滚动/单页/双页、文本层、四色高亮+批注卡片、目录、位置记忆、深色），AI 为可选增强 -->
<template>
  <div ref="rootEl" class="pdf-reader" :class="{ 'pr-dark': dark }">
    <div class="pr-toolbar">
      <el-button v-if="showToc" size="small" :type="showTocPanel ? 'primary' : ''" @click="showTocPanel = !showTocPanel">📑 目录</el-button>
      <el-radio-group v-model="mode" size="small" class="pr-mode">
        <el-radio-button value="scroll">连续</el-radio-button>
        <el-radio-button value="single">单页</el-radio-button>
        <el-radio-button value="double">双页</el-radio-button>
      </el-radio-group>
      <el-button-group>
        <el-button size="small" :disabled="page <= 1" @click="goPage(-1)">上一页</el-button>
        <el-button size="small" :disabled="page >= numPages" @click="goPage(1)">下一页</el-button>
      </el-button-group>
      <span class="pr-pageinfo">
        <el-input-number v-model="page" :min="1" :max="numPages || 1" size="small" controls-position="right" style="width: 100px" />
        <span class="pr-total">/ {{ numPages || '…' }} 页</span>
      </span>
      <el-button-group>
        <el-button size="small" @click="zoomBy(-0.15)">−</el-button>
        <span class="pr-zoom">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" @click="zoomBy(0.15)">＋</el-button>
        <el-button size="small" @click="fitWidth">适应宽</el-button>
        <el-button size="small" @click="fitPage">适应页</el-button>
        <el-button size="small" :type="dark ? 'primary' : ''" @click="dark = !dark">{{ dark ? '☀️' : '🌙' }}</el-button>
      </el-button-group>
      <template v-if="bookId && showAi">
        <el-button size="small" type="warning" plain @click="analyzePage">🤖 解读本页</el-button>
        <el-button size="small" type="success" plain @click="summarizeChapter" :loading="aiBusy">{{ aiBusy ? '生成中…' : '📝 总结本章' }}</el-button>
      </template>
      <el-button v-if="bookId" size="small" @click="showAnnPanel = true">🖍 标注({{ annotations.length }})</el-button>
    </div>

    <div class="pr-body-wrap">
      <aside v-if="showToc && showTocPanel" class="pr-toc">
        <div class="pr-toc-title">目录</div>
        <div v-for="t in toc" :key="t.id" class="pr-toc-item"
          :style="{ paddingLeft: (t.level - 1) * 14 + 8 + 'px' }"
          :class="{ active: t.start_page === page }"
          @click="jumpToPage(t.start_page)">{{ t.title }}</div>
      </aside>

      <div ref="scroller" class="pr-body" :class="'pr-mode-' + mode"
        @scroll="onScroll" @mouseup="onMouseUp" @mousedown="onMouseDown" @wheel="onWheel">
        <div v-for="p in pageList" :key="p" class="pr-page" :data-page="p"
          :style="{ width: pageWidthPx(p) + 'px', height: pageH(p) + 'px' }">
          <canvas :ref="(el) => setCanvasRef(p, el)" class="pr-canvas" />
          <div :ref="(el) => setTextRef(p, el)" class="text-layer"></div>
          <div v-for="(st, i) in hlStyles(p)" :key="st.id + '-' + i" class="pr-hl"
            :style="st.style" @click.stop="openAnnCard('edit', st.ann, $event)" :title="st.ann.text || ''" />
        </div>
        <div v-if="loading" class="pr-loading" v-loading="true" element-loading-text="正在渲染原文…" />
        <div v-if="errorMsg" class="pr-error">⚠️ {{ errorMsg }}</div>
      </div>
    </div>

    <!-- 选中文字浮动工具条 -->
    <div v-if="selToolbar" class="pr-sel-bar" :style="{ top: selPos.y + 'px', left: selPos.x + 'px' }">
      <el-button size="small" type="primary" @click="aiAction('explain')">💡 解释</el-button>
      <el-button size="small" type="success" @click="aiAction('translate')">🌐 翻译</el-button>
      <el-button size="small" type="warning" @click="openAnnCard('create')">🖍 高亮</el-button>
    </div>

    <!-- 批注卡片（创建/编辑） -->
    <div v-if="annCard.visible" class="pr-ann-card" :style="{ top: annCard.y + 'px', left: annCard.x + 'px' }">
      <div class="ann-card-title">{{ annCard.mode === 'edit' ? '编辑批注' : '添加批注' }}</div>
      <div class="ann-colors">
        <span v-for="c in COLORS" :key="c" class="ann-color"
          :class="{ active: annCard.color === c }" :style="{ background: c }" @click="annCard.color = c" />
      </div>
      <el-input v-model="annCard.note" type="textarea" :rows="2" size="small" placeholder="写笔记…" />
      <el-select v-model="annCard.knowledgeNodeId" placeholder="挂到知识树节点（可选）" clearable size="small" style="width: 100%; margin-top: 6px">
        <el-option v-for="n in nodeOptions" :key="n.id" :label="n.label" :value="n.id" />
      </el-select>
      <div class="ann-actions">
        <el-button v-if="annCard.mode === 'edit'" size="small" type="danger" plain @click="deleteAnnFromCard">删除</el-button>
        <el-button size="small" @click="annCard.visible = false">取消</el-button>
        <el-button size="small" type="primary" @click="saveAnnCard">{{ annCard.mode === 'edit' ? '保存' : '添加' }}</el-button>
      </div>
    </div>

    <!-- AI 结果抽屉 -->
    <el-drawer v-model="aiPanel" :title="aiTitle" size="42%">
      <div v-if="aiLoading" v-loading="true" style="height: 200px" />
      <div v-else-if="aiResult" class="ai-result" v-html="aiResultHtml"></div>
      <el-empty v-else description="等待操作" :image-size="80" />
      <template #footer>
        <div v-if="!aiLoading && aiResult && !aiResult.startsWith('⚠️') && pendingSel" class="ai-footer">
          <el-button type="warning" plain size="small" @click="saveAiAsAnnotation">🖍 保存为高亮批注</el-button>
        </div>
      </template>
    </el-drawer>

    <!-- 标注管理抽屉 -->
    <el-drawer v-model="showAnnPanel" title="我的标注" size="40%">
      <div class="ann-export" v-if="annotations.length">
        <el-button size="small" type="primary" plain @click="exportAnns">⬇ 导出 Markdown</el-button>
      </div>
      <div v-if="!annotations.length" class="form-tip">还没有标注：在正文中选中文字 → 点「🖍 高亮」即可添加</div>
      <div v-for="a in annotations" :key="a.id" class="ann-item">
        <div class="ann-head">
          <span class="ann-dot" :style="{ background: a.color }"></span>
          <el-tag size="small" type="warning">第 {{ a.page }} 页</el-tag>
          <el-button link size="small" @click="jumpToPage(a.page)">跳转</el-button>
          <el-button link size="small" type="danger" @click="removeAnn(a)">删除</el-button>
        </div>
        <div class="ann-text">{{ a.text || '' }}</div>
        <div v-if="a.note" class="ann-note">📝 {{ a.note }}</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  listAnnotations, createAnnotation, updateAnnotation, deleteAnnotation,
  aiExplain, aiSummarize, aiVision, listBooks, getBook, getKnowledgeTree,
} from '../api'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const emit = defineEmits(['page-change'])
const COLORS = ['#f9e572', '#9be5a0', '#8ec8f5', '#f5b8c8']  // 黄/绿/蓝/粉

const props = defineProps({
  src: { type: String, default: '' },
  bookId: { type: Number, default: null },
  initialPage: { type: Number, default: 1 },
  toc: { type: Array, default: () => [] },
  showToc: { type: Boolean, default: false },
  showAi: { type: Boolean, default: false },
  useSavedPos: { type: Boolean, default: true },  // false = 强制从 initialPage 打开（知识树跳转等）
})

const rootEl = ref(null)
const scroller = ref(null)
const canvasRefs = {}
const textRefs = {}
const page = ref(1)
const numPages = ref(0)
const scale = ref(1.1)
const dark = ref(false)
const loading = ref(false)
const errorMsg = ref('')
const showTocPanel = ref(false)
const pageList = ref([])
const mode = ref('scroll')
const baseHeights = {}   // scale=1 时的页高缓存（缩放不重算）
const baseWidths = {}
const pageHeights = ref({})
const rendered = ref({})
const annotations = ref([])
const showAnnPanel = ref(false)
const nodeOptions = ref([])

// AI 状态
const aiPanel = ref(false)
const aiTitle = ref('AI 解读')
const aiLoading = ref(false)
const aiResult = ref('')
const aiBusy = ref(false)
const selToolbar = ref(false)
const selPos = ref({ x: 0, y: 0 })
let selText = ''
let selRects = null
let selPage = 1
let pendingSel = null   // AI 解释后保存为批注用的选区快照

// 批注卡片
const annCard = ref({ visible: false, mode: 'create', x: 0, y: 0, page: 1, rects: [], text: '', color: COLORS[0], note: '', knowledgeNodeId: null, editingId: null })

let pdfDoc = null
let renderTasks = {}
const pendingRenders = new Set()
let bookTitle = ''

function setCanvasRef(p, el) { if (el) canvasRefs[p] = el }
function setTextRef(p, el) { if (el) textRefs[p] = el }

const aiResultHtml = computed(() => {
  const t = aiResult.value || ''
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br/>').replace(/#{1,3} (.+)/g, '<b>$1</b>')
})

// ===== 页高/页宽（缩放按比例，不重算）=====
const pageH = (p) => Math.round((baseHeights[p] || 800) * scale.value)
const pageWidthPx = (p) => Math.round((baseWidths[p] || 595) * scale.value)
const pageOffset = (p) => {
  let acc = 0
  if (mode.value === 'double') {
    for (let i = 1; i < p; i++) {
      if (i === pairStartOf(i)) {
        const e = pairEndOf(i)
        acc += Math.max(pageH(i), e > i ? pageH(e) : 0)
      }
    }
    return acc
  }
  for (let i = 1; i < p; i++) acc += pageH(i)
  return acc
}

// ===== 加载 =====
const loadPdf = async () => {
  if (!props.src) return
  loading.value = true
  errorMsg.value = ''
  if (pdfDoc) { try { pdfDoc.destroy() } catch {} pdfDoc = null }
  try {
    const doc = await pdfjsLib.getDocument({
      url: props.src, disableAutoFetch: true,
      cMapUrl: 'cmaps/', cMapPacked: true, standardFontDataUrl: 'standard_fonts/',
    }).promise
    pdfDoc = doc
    numPages.value = doc.numPages
    pageList.value = Array.from({ length: doc.numPages }, (_, i) => i + 1)
    // 预取前 N 页高度（保证首屏滚动位置正确），其余惰性补全
    const HEIGHTS = {}
    const WIDTHS = {}
    for (let i = 1; i <= doc.numPages; i++) {
      const pg = await doc.getPage(i)
      const vp1 = pg.getViewport({ scale: 1 })
      HEIGHTS[i] = vp1.height
      WIDTHS[i] = vp1.width
    }
    Object.assign(baseHeights, HEIGHTS)
    Object.assign(baseWidths, WIDTHS)
    pageHeights.value = {}
    // 初始页：有记忆且允许记忆时用记忆；否则用指定页（知识树跳转等）
    const saved = readPos()
    const useSaved = props.useSavedPos && saved && saved.page
    const target = useSaved ? saved.page : (props.initialPage || 1)
    page.value = Math.min(Math.max(1, target), doc.numPages)
    await renderVisible()
    if (useSaved && saved.scrollTop) {
      await nextTick()
      scroller.value.scrollTop = saved.scrollTop
    } else {
      await nextTick()
      scrollToPage(page.value, false)
    }
    await loadAnnotations()
    loadNodeOptions()
  } catch (e) {
    console.error('pdf load error', e)
    errorMsg.value = 'PDF 加载失败：' + (e.message || e)
  } finally {
    loading.value = false
  }
}

// ===== 显示模式与可见范围 =====
const pairStartOf = (p) => {
  if (p <= 1) return 1
  return p % 2 === 0 ? p : p - 1   // 跨页：(1),(2,3),(4,5)…
}
const pairEndOf = (p) => {
  const s = pairStartOf(p)
  if (s === 1) return 1
  return Math.min(numPages.value, s + 1)
}

// 视口顶部所在页（页码指示用，与渲染缓冲无关）
const currentPageAt = (st) => {
  let acc = 0
  for (let p = 1; p <= numPages.value; p++) {
    acc += pageH(p)
    if (acc > st) return p
  }
  return numPages.value
}

const visibleRange = () => {
  if (!scroller.value || !numPages.value) return { start: 1, end: 1 }
  const st = scroller.value.scrollTop
  const ch = scroller.value.clientHeight
  if (mode.value === 'scroll') {
    let acc = 0
    let start = 1, end = 1
    for (let p = 1; p <= numPages.value; p++) {
      const h = pageH(p)
      if (acc + h > st && start === 1) start = p
      if (acc + h > st + ch) { end = p; break }
      acc += h
      end = p
    }
    return { start: Math.max(1, start - 1), end: Math.min(numPages.value, end + 1) }
  }
  // 单页/双页：只渲染当前页（或当前跨页）
  if (mode.value === 'double') {
    let acc = 0
    let row = 1
    while (row <= numPages.value) {
      const s = pairStartOf(row)
      const e = pairEndOf(s)
      const h = Math.max(pageH(s), e > s ? pageH(e) : 0)
      if (acc + h > st) break
      acc += h
      row = e + 1
    }
    const cur = Math.min(row, numPages.value)
    return { start: pairStartOf(cur), end: pairEndOf(cur) }
  }
  let cur = 1
  let acc = 0
  for (let p = 1; p <= numPages.value; p++) {
    const h = pageH(p)
    if (acc + h > st) { cur = p; break }
    acc += h
  }
  return { start: cur, end: cur }
}

const renderVisible = async () => {
  if (!numPages.value) return
  const r = visibleRange()
  const want = new Set()
  for (let p = r.start; p <= r.end; p++) want.add(p)
  for (const p of Object.keys(rendered.value)) {
    if (!want.has(Number(p))) clearPage(Number(p))
  }
  for (const p of want) {
    if (!rendered.value[p]) renderPage(Number(p))
  }
}

const renderPage = async (p) => {
  if (!pdfDoc || rendered.value[p] || pendingRenders.has(p)) return
  pendingRenders.add(p)
  try {
    if (renderTasks[p]) { try { await renderTasks[p].cancel() } catch {} delete renderTasks[p] }
    const pdfPage = await pdfDoc.getPage(p)
    const vp = pdfPage.getViewport({ scale: scale.value })
    const cv = canvasRefs[p]
    if (!cv) return
    const dpr = window.devicePixelRatio || 1
    cv.width = Math.floor(vp.width * dpr)
    cv.height = Math.floor(vp.height * dpr)
    cv.style.width = Math.floor(vp.width) + 'px'
    cv.style.height = Math.floor(vp.height) + 'px'
    const ctx = cv.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const task = pdfPage.render({ canvasContext: ctx, viewport: vp })
    renderTasks[p] = task
    await task.promise
    delete renderTasks[p]
    const tl = textRefs[p]
    if (tl) {
      tl.style.width = Math.floor(vp.width) + 'px'
      tl.style.height = Math.floor(vp.height) + 'px'
      const textContent = await pdfPage.getTextContent()
      const tlInstance = new pdfjsLib.TextLayer({ textContentSource: textContent, container: tl, viewport: vp })
      await tlInstance.render()
    }
    rendered.value[p] = true
  } catch (e) {
    if (e?.name !== 'RenderingCancelledException') console.error('render err', p, e)
  } finally {
    pendingRenders.delete(p)
  }
}

const clearPage = (p) => {
  if (renderTasks[p]) { try { renderTasks[p].cancel() } catch {} delete renderTasks[p] }
  pendingRenders.delete(p)
  const cv = canvasRefs[p]
  if (cv) { cv.width = 1; cv.height = 1 }
  const tl = textRefs[p]
  if (tl) tl.innerHTML = ''
  rendered.value[p] = false
}

const onScroll = () => {
  renderVisible()
  const r = visibleRange()
  // 连续模式：页码=视口顶部页（渲染缓冲只影响渲染，不影响页码）
  if (mode.value === 'scroll') {
    page.value = currentPageAt(scroller.value.scrollTop)
  } else if (mode.value === 'double') {
    const cur = r.start
    page.value = pairStartOf(cur)
  }
  notifyPageChange()
  savePosDebounced()
}

const notifyPageChange = () => {
  emit('page-change', { page: page.value, chapter: currentChapter()?.title || '' })
}

const onWheel = (e) => {
  if (e.ctrlKey || e.metaKey) { zoomBy(e.deltaY > 0 ? -0.1 : 0.1); return }
  if (mode.value === 'scroll') return  // 连续模式自然滚动
  if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
  if (e.deltaY > 0) goPage(1)
  else goPage(-1)
  e.preventDefault()
}

const goPage = (delta) => {
  if (mode.value === 'scroll') {
    const p = Math.min(numPages.value, Math.max(1, page.value + delta))
    scrollToPage(p)
    return
  }
  if (mode.value === 'double') {
    const s = pairStartOf(page.value)
    const next = s === 1 ? 2 : s + 2
    const p = Math.min(numPages.value, Math.max(1, next))
    scrollToPage(p)
    return
  }
  scrollToPage(Math.min(numPages.value, Math.max(1, page.value + delta)))
}

const scrollToPage = (p, smooth = true) => {
  if (!scroller.value) return
  const target = Math.min(numPages.value, Math.max(1, p))
  page.value = target
  // 单页/双页用瞬时跳转（避免平滑滚动中途 onScroll 把页码回弹）
  const useSmooth = smooth && mode.value === 'scroll'
  scroller.value.scrollTo({ top: pageOffset(target) + 2, behavior: useSmooth ? 'smooth' : 'auto' })
  renderVisible()
  notifyPageChange()
}

const jumpToPage = (p) => scrollToPage(p)

const zoomBy = (d) => {
  scale.value = Math.min(2.5, Math.max(0.5, Math.round((scale.value + d) * 100) / 100))
  renderVisible()
}
const fitWidth = () => {
  if (!scroller.value || !numPages.value) return
  const w = scroller.value.clientWidth - 30
  const base = baseWidths[page.value] || 595
  scale.value = Math.max(0.5, Math.min(2, w / base))
  renderVisible()
}
const fitPage = () => {
  if (!scroller.value || !numPages.value) return
  const w = scroller.value.clientWidth - 30
  const h = scroller.value.clientHeight - 30
  const bw = baseWidths[page.value] || 595
  const bh = baseHeights[page.value] || 800
  scale.value = Math.max(0.5, Math.min(2, Math.min(w / bw, h / bh)))
  renderVisible()
}

watch(mode, (nv) => {
  if (nv === 'double' && scroller.value) {
    const w = scroller.value.clientWidth - 40
    const bw = baseWidths[page.value] || 595
    if (bw * scale.value * 2 > w) {
      scale.value = Math.max(0.5, Math.min(2, w / (bw * 2)))
    }
  }
  page.value = Math.max(1, Math.min(numPages.value || 1, page.value))
  scrollToPage(page.value, false)
})

// ===== 位置记忆 =====
const posKey = () => 'sa-reader-' + (props.bookId || props.src)
const readPos = () => { try { return JSON.parse(localStorage.getItem(posKey()) || 'null') } catch { return null } }
const savePos = () => { try { localStorage.setItem(posKey(), JSON.stringify({ page: page.value, scrollTop: scroller.value?.scrollTop || 0 })) } catch {} }
let saveTimer = null
const savePosDebounced = () => { clearTimeout(saveTimer); saveTimer = setTimeout(savePos, 800) }

// ===== 标注 =====
const hlStyles = (p) => {
  const out = []
  for (const a of annotations.value) {
    if (a.page !== p) continue
    let rects = []
    try { rects = JSON.parse(a.rect_json) } catch {}
    for (const r of rects) {
      out.push({
        id: a.id,
        ann: a,
        style: {
          left: (r.x * 100) + '%',
          top: (r.y * 100) + '%',
          width: (r.w * 100) + '%',
          height: (r.h * 100) + '%',
          background: (a.color || COLORS[0]) + '99',
        },
      })
    }
  }
  return out
}

const loadAnnotations = async () => {
  if (!props.bookId) return
  try { annotations.value = await listAnnotations(props.bookId) } catch {}
}

const loadNodeOptions = async () => {
  try {
    const tree = await getKnowledgeTree()
    const flat = []
    const walk = (nodes, depth) => {
      for (const n of nodes) {
        flat.push({ id: n.id, label: '　'.repeat(depth) + n.title })
        if (n.children?.length) walk(n.children, depth + 1)
      }
    }
    walk(tree.items || [], 0)
    nodeOptions.value = flat
  } catch {}
}

const onMouseUp = async (e) => {
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !sel.toString().trim()) { selToolbar.value = false; return }
  const node = sel.anchorNode
  const tl = node?.parentElement?.closest('.text-layer')
  if (!tl) return
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  if (!rect.width) return
  selText = sel.toString().trim().slice(0, 2000)
  selRects = sel.getRangeAt(0).getClientRects()
  // 选区所在页：以文本层所在 .pr-page 为准（连续模式多页可见时更可靠）
  const pageEl = tl.closest('.pr-page')
  selPage = pageEl ? parseInt(pageEl.dataset.page) : page.value
  const pr = rootEl.value.getBoundingClientRect()
  selPos.value = {
    x: Math.max(8, Math.min(rect.left - pr.left + scroller.value.scrollLeft, pr.width - 320)),
    y: Math.max(8, rect.bottom - pr.top + scroller.value.scrollTop + 8),
  }
  selToolbar.value = true
}

const onMouseDown = () => {}

// 批注卡片
const openAnnCard = (modeName, ann = null, ev = null) => {
  selToolbar.value = false
  const pr = rootEl.value.getBoundingClientRect()
  let x, y
  if (ev) {
    x = ev.clientX - pr.left + scroller.value.scrollLeft
    y = ev.clientY - pr.top + scroller.value.scrollTop
  } else {
    x = selPos.value.x
    y = selPos.value.y + 40
  }
  // 边界钳制（窄面板/靠边时避免溢出）
  x = Math.max(8, Math.min(x, pr.width - 280))
  y = Math.max(8, Math.min(y, pr.height - 260))
  if (modeName === 'edit' && ann) {
    annCard.value = {
      visible: true, mode: 'edit', x, y, page: ann.page,
      rects: JSON.parse(ann.rect_json || '[]'), text: ann.text || '',
      color: ann.color || COLORS[0], note: ann.note || '',
      knowledgeNodeId: ann.knowledge_node_id || null, editingId: ann.id,
    }
  } else {
    annCard.value = {
      visible: true, mode: 'create', x, y, page: selPage,
      rects: selRects ? Array.from(selRects) : [], text: selText || '',
      color: COLORS[0], note: '', knowledgeNodeId: null, editingId: null,
    }
  }
  window.getSelection()?.removeAllRanges()
}

const saveAnnCard = async () => {
  const c = annCard.value
  if (c.mode === 'edit') {
    try {
      await updateAnnotation(c.editingId, { note: c.note, color: c.color, knowledge_node_id: c.knowledgeNodeId || null })
      ElMessage.success('批注已保存')
    } catch (e) { ElMessage.error(e.message) }
  } else {
    if (!props.bookId) { ElMessage.warning('缺少书籍信息'); return }
    // 把 DOMRect 归一化为 0-1 坐标
    const pageEl = document.querySelector('.pr-page[data-page="' + c.page + '"]')
    if (!pageEl) { ElMessage.warning('页面未就绪'); return }
    const pr = pageEl.getBoundingClientRect()
    const rects = []
    for (const r of c.rects) {
      rects.push({
        x: +((r.left - pr.left) / pr.width).toFixed(4),
        y: +((r.top - pr.top) / pr.height).toFixed(4),
        w: +(r.width / pr.width).toFixed(4),
        h: +(r.height / pr.height).toFixed(4),
      })
    }
    try {
      await createAnnotation(props.bookId, {
        page: c.page, rect_json: JSON.stringify(rects), text: c.text,
        color: c.color, note: c.note || '', knowledge_node_id: c.knowledgeNodeId || null,
      })
      ElMessage.success('已添加高亮')
    } catch (e) { ElMessage.error(e.message) }
  }
  annCard.value.visible = false
  loadAnnotations()
}

const deleteAnnFromCard = async () => {
  try {
    await deleteAnnotation(annCard.value.editingId)
    ElMessage.success('已删除')
    annCard.value.visible = false
    loadAnnotations()
  } catch (e) { ElMessage.error(e.message) }
}

const removeAnn = async (a) => {
  await deleteAnnotation(a.id)
  loadAnnotations()
}

const exportAnns = () => {
  const lines = ['# 标注导出（' + (bookTitle || 'PDF') + '）', '']
  const byPage = {}
  for (const a of annotations.value) {
    (byPage[a.page] = byPage[a.page] || []).push(a)
  }
  for (const pg of Object.keys(byPage).sort((x, y) => x - y)) {
    lines.push('## 第 ' + pg + ' 页')
    for (const a of byPage[pg]) {
      lines.push('')
      if (a.text) lines.push('> ' + a.text.replace(/\n/g, ' '))
      if (a.note) lines.push('- 📝 ' + a.note.replace(/\n/g, ' '))
    }
    lines.push('')
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = (bookTitle || 'reader') + '-标注.md'
  a.click()
  URL.revokeObjectURL(url)
}

// ===== AI 增强（可选）=====
const aiAction = async (action) => {
  selToolbar.value = false
  if (!selText) return
  // 记录选区快照（用于「保存为批注」）
  if (selRects) {
    const pageEl = document.querySelector('.pr-page[data-page="' + selPage + '"]')
    if (pageEl) {
      const pr2 = pageEl.getBoundingClientRect()
      pendingSel = {
        page: selPage,
        rects: Array.from(selRects).map(r => ({
          x: +((r.left - pr2.left) / pr2.width).toFixed(4),
          y: +((r.top - pr2.top) / pr2.height).toFixed(4),
          w: +(r.width / pr2.width).toFixed(4),
          h: +(r.height / pr2.height).toFixed(4),
        })),
        text: selText,
      }
    }
  }
  aiTitle.value = action === 'translate' ? '翻译' : 'AI 解释'
  aiPanel.value = true
  aiLoading.value = true
  aiResult.value = ''
  try {
    const resp = await aiExplain({ text: selText, action, book_title: bookTitle, chapter_title: '' })
    if (!resp.ok) throw new Error(resp.error || 'AI 调用失败')
    aiResult.value = resp.result
  } catch (e) { aiResult.value = '⚠️ ' + e.message } finally { aiLoading.value = false }
}

const saveAiAsAnnotation = async () => {
  if (!pendingSel || !props.bookId || !aiResult.value || aiResult.value.startsWith('⚠️')) return
  try {
    await createAnnotation(props.bookId, {
      page: pendingSel.page,
      rect_json: JSON.stringify(pendingSel.rects),
      text: pendingSel.text,
      color: COLORS[0],
      note: '💡 AI 解读：' + aiResult.value.slice(0, 1500),
      knowledge_node_id: null,
    })
    ElMessage.success('已保存为高亮批注')
    loadAnnotations()
  } catch (e) { ElMessage.error(e.message) }
}

const summarizeChapter = async () => {
  if (!props.bookId) return
  aiTitle.value = '章节总结'
  aiPanel.value = true
  aiLoading.value = true
  aiResult.value = ''
  aiBusy.value = true
  try {
    const cur = currentChapter()
    if (!cur) { aiResult.value = '⚠️ 未找到当前页所属章节'; return }
    const resp = await aiSummarize({ book_id: props.bookId, chapter_id: cur.id })
    if (!resp.ok) throw new Error(resp.error || 'AI 调用失败')
    aiResult.value = resp.result
  } catch (e) { aiResult.value = '⚠️ ' + e.message } finally { aiLoading.value = false; aiBusy.value = false }
}

const currentChapter = () => {
  const flat = []
  const walk = (nodes) => { for (const n of nodes) { flat.push(n); if (n.children?.length) walk(n.children) } }
  walk(props.toc || [])
  let best = null
  for (const c of flat) {
    if (c.start_page && c.start_page <= page.value) {
      if (!best || c.start_page > best.start_page) best = c
    }
  }
  return best
}

const analyzePage = async () => {
  if (!props.bookId) return
  const cv = canvasRefs[page.value]
  if (!cv) { ElMessage.warning('页面尚未渲染完成'); return }
  aiTitle.value = 'AI 解读本页（Qwen-VL 视觉分析）'
  aiPanel.value = true
  aiLoading.value = true
  aiResult.value = ''
  aiBusy.value = true
  try {
    const image = cv.toDataURL('image/jpeg', 0.8)
    const resp = await aiVision({ book_id: props.bookId, page: page.value, image })
    if (!resp.ok) throw new Error(resp.error || '视觉分析失败')
    aiResult.value = resp.result
  } catch (e) { aiResult.value = '⚠️ ' + e.message } finally { aiLoading.value = false; aiBusy.value = false }
}

// ===== 生命周期 =====
onMounted(async () => {
  try {
    const resp = await listBooks({ page_size: 100 })
    const b = resp.items.find(x => x.id === props.bookId)
    bookTitle = b?.title || ''
  } catch {}
  loadPdf()
})

onBeforeUnmount(() => {
  savePos()
  if (pdfDoc) { try { pdfDoc.destroy() } catch {} }
})
</script>

<style scoped>
.pdf-reader { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 360px; }
.pr-toolbar {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px; flex-wrap: wrap;
  background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0;
  border: 1px solid var(--el-border-color-extra-light);
}
.pr-mode :deep(.el-radio-button__inner) { padding: 6px 10px; font-size: 12px; }
.pr-pageinfo { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.pr-total { white-space: nowrap; }
.pr-zoom { font-size: 12px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.pr-body-wrap { display: flex; flex: 1; min-height: 300px; overflow: hidden; border: 1px solid var(--el-border-color-extra-light); border-radius: 0 0 8px 8px; }
.pr-toc { width: 180px; flex-shrink: 0; overflow-y: auto; background: #f6f9fb; border-right: 1px solid var(--el-border-color-extra-light); padding: 6px 0; }
.pr-toc-title { font-weight: 600; font-size: 12px; padding: 4px 10px; color: var(--el-text-color-primary); }
.pr-toc-item { font-size: 12px; padding: 4px 8px; cursor: pointer; color: var(--el-text-color-regular); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr-toc-item:hover { background: var(--el-color-primary-light-9); }
.pr-toc-item.active { background: var(--el-color-primary-light-8); color: var(--el-color-primary); font-weight: 600; }
.pr-body { position: relative; flex: 1; overflow: auto; padding: 10px 14px; background: #525659; }
.pr-body.pr-mode-scroll, .pr-body.pr-mode-double { text-align: center; }
.pr-page { position: relative; box-shadow: 0 2px 10px rgba(0,0,0,.4); background: #fff; }
.pr-mode-scroll .pr-page, .pr-mode-single .pr-page { display: block; margin: 0 auto 10px; }
.pr-mode-double .pr-page { display: inline-block; vertical-align: top; margin: 0 4px 10px; }
.pr-canvas { display: block; }
.text-layer { position: absolute; inset: 0; overflow: hidden; line-height: 1; }
.text-layer :deep(span) { position: absolute; white-space: pre; transform-origin: 0 0; color: transparent; }
.text-layer :deep(span::selection) { background: rgba(59, 130, 246, 0.35); }
.pr-hl { position: absolute; border-radius: 2px; pointer-events: auto; cursor: pointer; }
.pr-hl:hover { outline: 1px solid #c45656; }
.pr-loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.pr-error { color: #ffd9a0; padding: 20px; font-size: 13px; }
.pr-dark .pr-page { filter: invert(0.92) hue-rotate(180deg); }
.pr-dark .pr-hl { filter: none; }
.pr-sel-bar {
  position: absolute; z-index: 50; display: flex; gap: 4px; padding: 4px;
  background: #fff; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.25);
  border: 1px solid var(--el-border-color-light);
}
.pr-ann-card {
  position: absolute; z-index: 60; width: 260px; padding: 10px;
  background: #fff; border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.3);
  border: 1px solid var(--el-border-color-light);
}
.ann-card-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.ann-colors { display: flex; gap: 6px; margin-bottom: 8px; }
.ann-color { width: 22px; height: 22px; border-radius: 50%; cursor: pointer; border: 2px solid transparent; }
.ann-color.active { border-color: #3e7fa3; }
.ann-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
.ai-result { font-size: 14px; line-height: 1.9; white-space: pre-wrap; color: var(--el-text-color-primary); }
.ai-footer { text-align: right; }
.ann-export { margin-bottom: 10px; }
.ann-item { padding: 10px; border: 1px solid var(--el-border-color-extra-light); border-radius: 8px; margin-bottom: 8px; }
.ann-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ann-dot { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.ann-text { font-size: 13px; color: var(--el-text-color-regular); margin-bottom: 4px; }
.ann-note { font-size: 12px; color: var(--el-text-color-secondary); }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
