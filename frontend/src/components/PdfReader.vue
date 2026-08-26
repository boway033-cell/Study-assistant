<!-- PdfReader v2.1：本地渲染为底层（滚动/单页/双页、文本层、四色高亮+批注卡片、目录、位置记忆、深色），AI 为可选增强 -->
<template>
  <div ref="rootEl" class="pdf-reader" :class="{ 'pr-dark': dark }">
    <div class="pr-toolbar" aria-label="PDF 阅读控制栏">
      <div class="pr-toolbar-group pr-view-controls">
        <el-button v-if="showToc" size="small" :type="showTocPanel ? 'primary' : ''" @click="toggleTocPanel">📑 <span class="pr-control-label">文档导航</span></el-button>
        <el-radio-group v-model="mode" size="small" class="pr-mode" aria-label="翻页方式">
          <el-radio-button value="scroll">连续</el-radio-button>
          <el-radio-button value="single">单页</el-radio-button>
          <el-radio-button value="double">双页</el-radio-button>
        </el-radio-group>
      </div>
      <div class="pr-toolbar-group pr-page-controls">
        <el-button size="small" aria-label="上一页" :disabled="page <= 1" @click="goPage(-1)">←</el-button>
        <span class="pr-pageinfo">
          <el-input-number v-model="page" :min="1" :max="numPages || 1" size="small" controls-position="right" aria-label="当前页" @change="onPageInput" />
          <span class="pr-total">/ {{ numPages || '…' }} 页</span>
        </span>
        <el-button size="small" aria-label="下一页" :disabled="page >= numPages" @click="goPage(1)">→</el-button>
      </div>
      <div class="pr-toolbar-group pr-display-controls">
        <el-button size="small" aria-label="缩小" @click="zoomBy(-0.15)">−</el-button>
        <span class="pr-zoom">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" aria-label="放大" @click="zoomBy(0.15)">＋</el-button>
        <el-button size="small" @click="fitWidth">适应宽</el-button>
        <el-button size="small" @click="fitPage">适应页</el-button>
        <el-button size="small" :type="dark ? 'primary' : ''" :aria-label="dark ? '切换浅色阅读' : '切换深色阅读'" @click="dark = !dark">{{ dark ? '☀️' : '🌙' }}</el-button>
      </div>
      <div v-if="bookId" class="pr-toolbar-group pr-research-controls pr-research-full">
        <template v-if="showAi">
          <el-button size="small" type="warning" plain @click="analyzePage">解读本页</el-button>
          <el-button size="small" type="success" plain :loading="aiBusy" @click="summarizeChapter">{{ aiBusy ? '生成中…' : '总结本章' }}</el-button>
        </template>
        <el-button size="small" @click="showAnnPanel = true">标注 {{ annotations.length }}</el-button>
      </div>
      <el-dropdown v-if="bookId" trigger="click" class="pr-research-menu">
        <el-button size="small">研究工具⌄</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="showAi" @click="analyzePage">解读本页</el-dropdown-item>
            <el-dropdown-item v-if="showAi" :disabled="aiBusy" @click="summarizeChapter">总结本章</el-dropdown-item>
            <el-dropdown-item divided @click="showAnnPanel = true">查看标注（{{ annotations.length }}）</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div class="pr-body-wrap">
      <aside v-if="showToc && showTocPanel" class="pr-toc" aria-label="文档导航">
        <div class="pr-toc-title"><span>文档目录</span><small>{{ toc.length }} 项</small></div>
        <div v-if="toc.length" class="pr-toc-list">
          <button v-for="t in toc" :key="t.id" class="pr-toc-item"
            :style="{ paddingLeft: (Math.max(1, t.level) - 1) * 14 + 12 + 'px' }"
            :class="{ active: t.start_page === page }"
            :title="t.title" @click="jumpToPage(t.start_page)"><span>{{ t.title }}</span><small>{{ t.start_page }}</small></button>
        </div>
        <div v-else class="pr-toc-empty">
          <b>尚未识别到目录</b>
          <span>可继续阅读，或进入目录工作台检查结构。</span>
        </div>
        <button class="pr-toc-review" type="button" @click="emit('request-toc-review')">校正目录结构</button>
      </aside>

      <div ref="scroller" class="pr-body" :class="'pr-mode-' + mode"
        @scroll="onScroll" @mouseup="onMouseUp" @mousedown="onMouseDown" @wheel="onWheel">
        <div v-for="p in renderPageList" :key="p" class="pr-page" :data-page="p"
          :style="{ width: pageWidthPx(p) + 'px', height: pageH(p) + 'px' }">
          <canvas :ref="(el) => setCanvasRef(p, el)" class="pr-canvas" />
          <div :ref="(el) => setTextRef(p, el)" class="text-layer"></div>
          <span v-if="ocrStates[p] === 'loading'" class="pr-ocr-state">正在生成本页可选文字层…</span>
          <span v-else-if="ocrStates[p] === 'ready'" class="pr-ocr-state ready">OCR 文字层</span>
          <button v-if="pageErrors[p]" class="pr-page-retry" type="button" @click="retryPage(p)">
            本页渲染失败，点击重试
          </button>
          <div v-for="(st, i) in hlStyles(p)" :key="st.id + '-' + i" class="pr-hl"
            :class="'pr-hl-' + st.markType"
            :style="st.style" :title="st.ann.text || ''" />
        </div>
        <div v-if="loading" class="pr-loading" v-loading="true" element-loading-text="正在渲染原文…" />
        <div v-if="errorMsg" class="pr-error">⚠️ {{ errorMsg }}</div>
      </div>
    </div>

    <!-- 选中文字浮动工具条 -->
    <div v-if="selToolbar" class="pr-sel-bar" :style="{ top: selPos.y + 'px', left: selPos.x + 'px' }">
      <el-button size="small" type="primary" @click="aiAction('explain')">💡 解释</el-button>
      <el-button size="small" type="success" @click="aiAction('translate')">🌐 翻译</el-button>
      <el-button v-if="reanchorId" size="small" type="danger" @click="saveReanchor">更新批注位置</el-button>
      <template v-else>
        <el-button size="small" type="warning" @click="saveQuickMark('highlight')">高亮</el-button>
        <el-button size="small" @click="saveQuickMark('underline')">划线</el-button>
        <el-button size="small" type="primary" plain @click="openAnnCard('create')">批注</el-button>
      </template>
    </div>

    <!-- 批注卡片（创建/编辑） -->
    <div v-if="annCard.visible" class="pr-ann-card">
      <div class="ann-card-title">{{ annCard.mode === 'edit' ? '编辑批注' : '添加批注' }}</div>
      <el-radio-group v-model="annCard.markType" size="small" class="ann-mark-types">
        <el-radio-button value="highlight">高亮</el-radio-button>
        <el-radio-button value="underline">划线</el-radio-button>
      </el-radio-group>
      <div class="ann-colors">
        <span v-for="c in COLORS" :key="c" class="ann-color"
          :class="{ active: annCard.color === c }" :style="{ background: c }" @click="annCard.color = c" />
      </div>
      <el-input v-model="annCard.note" type="textarea" :rows="2" size="small" placeholder="批注可选；留空也能保存" />
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
          <span class="ann-dot" :class="{ underline: a.mark_type === 'underline' }" :style="{ '--ann-color': a.color, background: a.mark_type === 'underline' ? 'transparent' : a.color }"></span>
          <el-tag size="small" effect="plain">{{ a.mark_type === 'underline' ? '划线' : (a.note ? '批注' : '高亮') }}</el-tag>
          <el-tag size="small" type="warning">第 {{ a.page }} 页</el-tag>
          <el-button link size="small" @click="jumpToPage(a.page)">跳转</el-button>
          <el-button link size="small" @click="openAnnCard('edit', a)">编辑</el-button>
          <el-button v-if="a.status === 'needs_reanchor'" link size="small" type="warning" @click="startReanchor(a)">重新选择</el-button>
          <el-button v-else-if="a.schema_version < 2" link size="small" type="primary" @click="repairOldAnnotation(a)">自动校准</el-button>
          <el-button link size="small" type="danger" @click="removeAnn(a)">删除</el-button>
        </div>
        <el-alert v-if="a.status === 'needs_reanchor'" type="warning" :closable="false" title="原位置无法可靠恢复，请跳转后重新选择原文" />
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
import { annotationSegments, clipSelectionRects } from '../utils/pdfAnnotations'
import {
  listAnnotations, createAnnotation, updateAnnotation, deleteAnnotation,
  repairAnnotation, getPdfTextLayer,
  aiExplain, aiSummarize, aiVision, getBook, getKnowledgeTree,
} from '../api'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const emit = defineEmits(['page-change', 'request-toc-review'])
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
const showTocPanel = ref(props.showToc)
const tocPanelTouched = ref(false)
const pageList = ref([])

// 非连续模式只保留当前页/跨页 DOM，避免数百页空 canvas 常驻内存。
const renderPageList = computed(() => {
  if (!numPages.value) return []
  if (mode.value === 'double') {
    const r = { start: pairStartOf(page.value), end: pairEndOf(page.value) }
    const list = []
    for (let p = r.start; p <= r.end; p++) list.push(p)
    return list
  }
  if (mode.value === 'single') return [page.value]
  return pageList.value
})
const mode = ref('scroll')
const baseHeights = {}   // scale=1 时的页高缓存（缩放不重算）
const baseWidths = {}
const pageHeights = ref({})
const pageMetricsVersion = ref(0)
const rendered = ref({})
const pageErrors = ref({})
const annotations = ref([])
const ocrStates = ref({})
const showAnnPanel = ref(false)
const nodeOptions = ref([])

const toggleTocPanel = () => {
  tocPanelTouched.value = true
  showTocPanel.value = !showTocPanel.value
}

watch(() => props.toc.length, (length) => {
  if (props.showToc && length && !tocPanelTouched.value) showTocPanel.value = true
})

// AI 状态
const aiPanel = ref(false)
const aiTitle = ref('AI 解读')
const aiLoading = ref(false)
const aiResult = ref('')
const aiBusy = ref(false)
const selToolbar = ref(false)
const selPos = ref({ x: 0, y: 0 })
let selText = ''
let selAnchor = null
let selPage = 1
let pendingSel = null   // AI 解释后保存为批注用的选区快照
const reanchorId = ref(null)

// 批注卡片
const annCard = ref({ visible: false, mode: 'create', page: 1, anchor: null, text: '', color: COLORS[0], markType: 'highlight', note: '', knowledgeNodeId: null, editingId: null })

let pdfDoc = null
let renderTasks = {}
const pendingRenders = new Set()
let bookTitle = ''
let renderGeneration = 0
let zoomTimer = null
let scrollFrame = null

function setCanvasRef(p, el) {
  if (!el) { delete canvasRefs[p]; return }
  canvasRefs[p] = el
  // HTML canvas 默认 300×150，会让数百个尚未渲染的占位页白白占用像素内存。
  if (!rendered.value[p] && !pendingRenders.has(p)) { el.width = 1; el.height = 1 }
}
function setTextRef(p, el) { if (el) textRefs[p] = el; else delete textRefs[p] }

const aiResultHtml = computed(() => {
  const t = aiResult.value || ''
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br/>').replace(/#{1,3} (.+)/g, '<b>$1</b>')
})

// ===== 页高/页宽（缩放按比例，不重算）=====
const pageH = (p) => { void pageMetricsVersion.value; return Math.round((baseHeights[p] || 800) * scale.value) }
const pageWidthPx = (p) => { void pageMetricsVersion.value; return Math.round((baseWidths[p] || 595) * scale.value) }
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
    // 只读取第一页尺寸作为默认值；实际渲染时惰性修正，避免导入 400+ 页对象。
    const HEIGHTS = {}
    const WIDTHS = {}
    const firstPage = await doc.getPage(1)
    const firstViewport = firstPage.getViewport({ scale: 1 })
    for (let i = 1; i <= doc.numPages; i++) {
      HEIGHTS[i] = firstViewport.height
      WIDTHS[i] = firstViewport.width
    }
    Object.assign(baseHeights, HEIGHTS)
    Object.assign(baseWidths, WIDTHS)
    pageHeights.value = {}
    // 初始页：有记忆且允许记忆时用记忆；否则用指定页（知识树跳转等）
    const saved = readPos()
    const useSaved = props.useSavedPos && saved && saved.page
    const target = useSaved ? saved.page : (props.initialPage || 1)
    page.value = Math.min(Math.max(1, target), doc.numPages)
    // 自动适应宽度（页面更大更清晰）
    await nextTick()
    fitWidth()
    // fitWidth 会以当前滚动中心保持视口；首次加载需在它完成后重新应用目标页，
    // 否则深链 ?page=N 会被初始 scrollTop=0 的滚动事件改回第 1 页。
    await nextTick()
    page.value = Math.min(Math.max(1, target), doc.numPages)
    if (useSaved && saved.scrollTop) {
      scroller.value.scrollTop = saved.scrollTop
    } else {
      scroller.value.scrollTop = mode.value === 'scroll' ? pageOffset(page.value) + 2 : 0
    }
    await renderVisible()
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
  // 单页/双页没有虚拟长列表，当前页就是唯一渲染范围。
  if (mode.value === 'double') {
    return { start: pairStartOf(page.value), end: pairEndOf(page.value) }
  }
  return { start: page.value, end: page.value }
}

const renderVisible = async () => {
  if (!numPages.value) return
  const r = visibleRange()
  const want = new Set()
  for (let p = r.start; p <= r.end; p++) want.add(p)
  for (const p of Object.keys(rendered.value)) {
    if (!want.has(Number(p))) clearPage(Number(p))
  }
  const generation = renderGeneration
  await Promise.all([...want].map(p => rendered.value[p] ? null : renderPage(Number(p), generation)))
}

const renderPage = async (p, generation = renderGeneration) => {
  if (!pdfDoc || rendered.value[p] || pendingRenders.has(p)) return
  pendingRenders.add(p)
  try {
    if (renderTasks[p]) { try { renderTasks[p].cancel() } catch {} delete renderTasks[p] }
    const pdfPage = await pdfDoc.getPage(p)
    if (generation !== renderGeneration) return
    const vp1 = pdfPage.getViewport({ scale: 1 })
    if (baseWidths[p] !== vp1.width || baseHeights[p] !== vp1.height) {
      baseWidths[p] = vp1.width
      baseHeights[p] = vp1.height
      pageMetricsVersion.value++
    }
    const vp = pdfPage.getViewport({ scale: scale.value })
    const cv = canvasRefs[p]
    if (!cv) return
    // 单页像素预算约 8MP（RGBA 约 32MB），避免扫描件/高倍缩放造成内存尖峰。
    const cssPixels = Math.max(1, vp.width * vp.height)
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8_000_000 / cssPixels)))
    cv.width = Math.floor(vp.width * dpr)
    cv.height = Math.floor(vp.height * dpr)
    cv.style.width = Math.floor(vp.width) + 'px'
    cv.style.height = Math.floor(vp.height) + 'px'
    const ctx = cv.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const task = pdfPage.render({ canvasContext: ctx, viewport: vp })
    renderTasks[p] = task
    await task.promise
    if (generation !== renderGeneration) return
    delete renderTasks[p]
    const tl = textRefs[p]
    if (tl) {
      tl.style.setProperty('--total-scale-factor', String(scale.value))
      tl.style.width = Math.floor(vp.width) + 'px'
      tl.style.height = Math.floor(vp.height) + 'px'
      tl.innerHTML = ''
      const textContent = await pdfPage.getTextContent()
      if (generation !== renderGeneration) return
      const tlInstance = new pdfjsLib.TextLayer({ textContentSource: textContent, container: tl, viewport: vp })
      await tlInstance.render()
      // 扫描页没有 PDF 文本层：仅为当前渲染页按需取得 OCR 坐标，不加载整本文档。
      if (!tl.querySelector('span') && props.bookId && p === page.value && generation === renderGeneration) {
        await renderOcrTextLayer(p, tl, generation)
      }
    }
    rendered.value[p] = true
    delete pageErrors.value[p]
  } catch (e) {
    if (e?.name !== 'RenderingCancelledException') {
      console.error('render err', p, e)
      pageErrors.value[p] = e?.message || '页面渲染失败'
    }
  } finally {
    pendingRenders.delete(p)
  }
}

const renderOcrTextLayer = async (p, tl, generation) => {
  if (ocrStates.value[p] === 'loading' || tl.querySelector('span')) return
  ocrStates.value[p] = 'loading'
  try {
    const layer = await getPdfTextLayer(props.bookId, p, true)
    if (generation !== renderGeneration || !textRefs[p] || textRefs[p] !== tl) return
    const fragment = document.createDocumentFragment()
    for (const item of layer.items || []) {
      if (!item.text || item.w <= 0 || item.h <= 0) continue
      const span = document.createElement('span')
      span.textContent = item.text
      span.dataset.source = 'ocr'
      span.dataset.width = String(item.w)
      span.style.left = (item.x * 100) + '%'
      span.style.top = (item.y * 100) + '%'
      span.style.fontSize = `${Math.max(4, item.h * tl.clientHeight)}px`
      fragment.appendChild(span)
    }
    tl.appendChild(fragment)
    // 将每行透明文字压缩到 OCR 框宽度，使浏览器 Range 命中框贴合扫描图。
    for (const span of tl.querySelectorAll('span[data-source="ocr"]')) {
      const expected = Number(span.dataset.width) * tl.clientWidth
      const natural = span.getBoundingClientRect().width
      if (natural > 0 && expected > 0) span.style.setProperty('--ocr-scale-x', String(expected / natural))
    }
    ocrStates.value[p] = (layer.items || []).length ? 'ready' : 'empty'
  } catch (e) {
    console.warn('OCR text layer unavailable', p, e)
    ocrStates.value[p] = 'failed'
  }
}

const ensureCurrentOcrLayer = () => {
  const p = page.value
  const tl = textRefs[p]
  if (props.bookId && rendered.value[p] && tl && !tl.querySelector('span') && !ocrStates.value[p]) {
    renderOcrTextLayer(p, tl, renderGeneration)
  }
}

const clearPage = (p) => {
  if (renderTasks[p]) { try { renderTasks[p].cancel() } catch {} delete renderTasks[p] }
  pendingRenders.delete(p)
  const cv = canvasRefs[p]
  if (cv) { cv.width = 1; cv.height = 1 }
  const tl = textRefs[p]
  if (tl) tl.innerHTML = ''
  delete ocrStates.value[p]
  rendered.value[p] = false
}

const retryPage = async (p) => {
  delete pageErrors.value[p]
  clearPage(p)
  await nextTick()
  renderPage(p, renderGeneration)
}

const onScroll = () => {
  selToolbar.value = false
  if (mode.value !== 'scroll') return
  if (scrollFrame) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    renderVisible()
    page.value = currentPageAt(scroller.value.scrollTop)
    ensureCurrentOcrLayer()
    notifyPageChange()
    savePosDebounced()
  })
}

const notifyPageChange = () => {
  emit('page-change', { page: page.value, chapter: currentChapter()?.title || '' })
}

const onWheel = (e) => {
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    zoomBy(e.deltaY > 0 ? -0.1 : 0.1)
    return
  }
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
    const next = delta > 0 ? (s === 1 ? 2 : s + 2) : (s <= 2 ? 1 : s - 2)
    const p = Math.min(numPages.value, Math.max(1, next))
    scrollToPage(p)
    return
  }
  scrollToPage(Math.min(numPages.value, Math.max(1, page.value + delta)))
}

const onPageInput = (val) => {
  if (val) scrollToPage(val, false)
}

const scrollToPage = (p, smooth = true) => {
  if (!scroller.value) return
  const target = Math.min(numPages.value, Math.max(1, p))
  page.value = mode.value === 'double' ? pairStartOf(target) : target
  // 单页/双页用瞬时跳转（避免平滑滚动中途 onScroll 把页码回弹）
  const useSmooth = smooth && mode.value === 'scroll'
  const top = mode.value === 'scroll' ? pageOffset(target) + 2 : 0
  scroller.value.scrollTo({ top, behavior: useSmooth ? 'smooth' : 'auto' })
  nextTick(renderVisible)
  notifyPageChange()
}

const jumpToPage = (p) => scrollToPage(p)

const applyScale = (nextScale) => {
  if (!scroller.value) return
  const oldScale = scale.value || 1
  const anchor = (scroller.value.scrollTop + scroller.value.clientHeight / 2) / oldScale
  scale.value = Math.min(2.5, Math.max(0.5, Math.round(nextScale * 100) / 100))
  renderGeneration++
  for (const p of Object.keys(rendered.value)) clearPage(Number(p))
  for (const task of Object.values(renderTasks)) { try { task.cancel() } catch {} }
  renderTasks = {}
  clearTimeout(zoomTimer)
  nextTick(() => {
    if (mode.value === 'scroll') {
      scroller.value.scrollTop = Math.max(0, anchor * scale.value - scroller.value.clientHeight / 2)
    }
    zoomTimer = setTimeout(() => renderVisible(), 140)
  })
}

const zoomBy = (d) => {
  applyScale(scale.value + d)
}
const fitWidth = () => {
  if (!scroller.value || !numPages.value) return
  const w = scroller.value.clientWidth - 30
  const pairFactor = mode.value === 'double' && pairEndOf(page.value) > pairStartOf(page.value) ? 2 : 1
  const base = (baseWidths[page.value] || 595) * pairFactor + (pairFactor === 2 ? 12 : 0)
  applyScale(Math.max(0.5, Math.min(2, w / base)))
}
const fitPage = () => {
  if (!scroller.value || !numPages.value) return
  const w = scroller.value.clientWidth - 30
  const h = scroller.value.clientHeight - 30
  const pairFactor = mode.value === 'double' && pairEndOf(page.value) > pairStartOf(page.value) ? 2 : 1
  const bw = (baseWidths[page.value] || 595) * pairFactor + (pairFactor === 2 ? 12 : 0)
  const bh = baseHeights[page.value] || 800
  applyScale(Math.max(0.5, Math.min(2, Math.min(w / bw, h / bh))))
}

watch(mode, (nv) => {
  if (nv === 'double' && scroller.value) {
    const w = scroller.value.clientWidth - 40
    const bw = baseWidths[page.value] || 595
    if (bw * scale.value * 2 > w) {
      applyScale(Math.max(0.5, Math.min(2, w / (bw * 2))))
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
    const segment = annotationSegments(a).find(s => Number(s.page) === Number(p))
    if (!segment) continue
    const rects = segment.rects || []
    for (const r of rects) {
      if (![r.x, r.y, r.w, r.h].every(Number.isFinite) || r.w <= 0 || r.h <= 0) continue
      out.push({
        id: a.id,
        ann: a,
        markType: a.mark_type || 'highlight',
        style: {
          left: (r.x * 100) + '%',
          top: (((a.mark_type || 'highlight') === 'underline' ? r.y + r.h : r.y) * 100) + '%',
          width: (r.w * 100) + '%',
          height: (r.h * 100) + '%',
          '--mark-color': a.color || COLORS[0],
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
  const range = sel.getRangeAt(0).cloneRange()
  const rect = range.getBoundingClientRect()
  if (!rect.width) return
  selText = sel.toString().trim().slice(0, 2000)
  selAnchor = snapshotRange(range, selText)
  if (!selAnchor?.segments?.length) { selToolbar.value = false; return }
  selPage = selAnchor.segments[0].page
  const pr = rootEl.value.getBoundingClientRect()
  selPos.value = {
    x: Math.max(8, Math.min(rect.left - pr.left, pr.width - 320)),
    y: Math.max(8, Math.min(rect.bottom - pr.top + 8, pr.height - 60)),
  }
  selToolbar.value = true
}

const snapshotRange = (range, exact) => {
  const rangeRects = Array.from(range.getClientRects()).filter(r => r.width > 0.5 && r.height > 0.5)
  const pages = Array.from(rootEl.value?.querySelectorAll('.pr-page') || []).map(pageEl => ({
    page: Number(pageEl.dataset.page),
    source: pageEl.querySelector('.text-layer span[data-source="ocr"]') ? 'ocr' : 'pdf-text',
    bounds: pageEl.getBoundingClientRect(),
  }))
  const anchorText = range.startContainer?.textContent || ''
  const focusText = range.endContainer?.textContent || ''
  return {
    schema_version: 2,
    quote: {
      exact,
      prefix: anchorText.slice(Math.max(0, range.startOffset - 32), range.startOffset),
      suffix: focusText.slice(range.endOffset, range.endOffset + 32),
    },
    segments: clipSelectionRects(rangeRects, pages),
  }
}

const onMouseDown = () => {}

// 批注卡片
const openAnnCard = (modeName, ann = null, ev = null) => {
  selToolbar.value = false
  if (modeName === 'edit' && ann) {
    annCard.value = {
      visible: true, mode: 'edit', page: ann.page,
      anchor: null, text: ann.text || '',
      color: ann.color || COLORS[0], note: ann.note || '',
      markType: ann.mark_type || 'highlight',
      knowledgeNodeId: ann.knowledge_node_id || null, editingId: ann.id,
    }
  } else {
    annCard.value = {
      visible: true, mode: 'create', page: selPage,
      anchor: selAnchor, text: selText || '',
      color: COLORS[0], markType: 'highlight', note: '', knowledgeNodeId: null, editingId: null,
    }
  }
  window.getSelection()?.removeAllRanges()
}

const saveAnnCard = async () => {
  const c = annCard.value
  if (c.mode === 'edit') {
    try {
      await updateAnnotation(c.editingId, { note: c.note, color: c.color, mark_type: c.markType, knowledge_node_id: c.knowledgeNodeId || null })
      ElMessage.success('批注已保存')
    } catch (e) { ElMessage.error(e.message) }
  } else {
    if (!props.bookId) { ElMessage.warning('缺少书籍信息'); return }
    if (!c.anchor?.segments?.length) { ElMessage.warning('没有可保存的文字位置'); return }
    const first = c.anchor.segments[0]
    try {
      await createAnnotation(props.bookId, {
        page: first.page, rect_json: JSON.stringify(first.rects), anchor: c.anchor, text: c.text,
        color: c.color, mark_type: c.markType, note: c.note || '', knowledge_node_id: c.knowledgeNodeId || null,
      })
      ElMessage.success(c.note ? '批注已保存' : (c.markType === 'underline' ? '已添加划线' : '已添加高亮'))
    } catch (e) { ElMessage.error(e.message) }
  }
  annCard.value.visible = false
  loadAnnotations()
}

const saveQuickMark = async (markType) => {
  selToolbar.value = false
  if (!props.bookId || !selAnchor?.segments?.length) return ElMessage.warning('没有可保存的文字位置')
  const first = selAnchor.segments[0]
  try {
    await createAnnotation(props.bookId, {
      page: first.page, rect_json: JSON.stringify(first.rects), anchor: selAnchor,
      text: selText, color: COLORS[0], mark_type: markType, note: '', origin: 'user',
    })
    window.getSelection()?.removeAllRanges()
    await loadAnnotations()
    ElMessage.success(markType === 'underline' ? '已添加划线' : '已添加高亮')
  } catch (e) { ElMessage.error(e.message) }
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

const repairOldAnnotation = async (annotation) => {
  try {
    const repaired = await repairAnnotation(annotation.id)
    if (repaired.status === 'active' && repaired.schema_version >= 2) ElMessage.success('已根据原文重新校准位置')
    else ElMessage.warning('未能自动定位，请重新选择原文')
    await loadAnnotations()
  } catch (e) { ElMessage.error(e.message) }
}

const startReanchor = (annotation) => {
  reanchorId.value = annotation.id
  showAnnPanel.value = false
  jumpToPage(annotation.page)
  ElMessage.info('请在原页重新选择对应文字，然后点击“更新批注位置”')
}

const saveReanchor = async () => {
  if (!reanchorId.value || !selAnchor?.segments?.length) return
  const first = selAnchor.segments[0]
  try {
    await updateAnnotation(reanchorId.value, {
      page: first.page, rect_json: JSON.stringify(first.rects), anchor: selAnchor,
      text: selText, status: 'active',
    })
    reanchorId.value = null
    selToolbar.value = false
    window.getSelection()?.removeAllRanges()
    ElMessage.success('批注位置已更新')
    await loadAnnotations()
  } catch (e) { ElMessage.error(e.message) }
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
  if (selAnchor?.segments?.length) pendingSel = { anchor: selAnchor, text: selText }
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
      page: pendingSel.anchor.segments[0].page,
      rect_json: JSON.stringify(pendingSel.anchor.segments[0].rects),
      anchor: pendingSel.anchor,
      text: pendingSel.text,
      color: COLORS[0],
      mark_type: 'highlight', origin: 'ai',
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
    const blob = await new Promise((resolve, reject) => cv.toBlob(
      value => value ? resolve(value) : reject(new Error('页面图像编码失败')), 'image/jpeg', 0.82,
    ))
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = () => reject(new Error('页面图像读取失败'))
      reader.readAsDataURL(blob)
    })
    const resp = await aiVision({ book_id: props.bookId, page: page.value, image })
    if (!resp.ok) throw new Error(resp.error || '视觉分析失败')
    aiResult.value = resp.result
  } catch (e) { aiResult.value = '⚠️ ' + e.message } finally { aiLoading.value = false; aiBusy.value = false }
}

// ===== 生命周期 =====
const onKeydown = (e) => {
  // 输入框内不拦截
  const tag = e.target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (e.key === 'ArrowLeft') { e.preventDefault(); goPage(-1) }
  else if (e.key === 'ArrowRight') { e.preventDefault(); goPage(1) }
  else if (e.key === 'PageDown') { e.preventDefault(); goPage(1) }
  else if (e.key === 'PageUp') { e.preventDefault(); goPage(-1) }
}

onMounted(async () => {
  try {
    const b = props.bookId ? await getBook(props.bookId) : null
    bookTitle = b?.title || ''
  } catch {}
  window.addEventListener('keydown', onKeydown)
  loadPdf()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  savePos()
  clearTimeout(zoomTimer)
  clearTimeout(saveTimer)
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
  for (const p of Object.keys(rendered.value)) clearPage(Number(p))
  if (pdfDoc) { try { pdfDoc.destroy() } catch {} }
})
</script>

<style scoped>
.pdf-reader { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 360px; }
.pr-page-retry { position:absolute; inset:50% auto auto 50%; transform:translate(-50%,-50%); z-index:4; padding:8px 12px; border:1px solid #e5e7eb; border-radius:8px; background:rgba(255,255,255,.94); color:#8b5a2b; box-shadow:0 1px 2px rgba(15,23,42,.08); cursor:pointer; }
.pr-page-retry:hover { transform:translate(-50%,-52%); box-shadow:0 4px 12px rgba(15,23,42,.12); }
.pr-toolbar {
  display: flex; align-items: center; gap: 10px; padding: 6px 8px; flex-wrap: nowrap; overflow-x:auto;
  background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0;
  border: 1px solid var(--el-border-color-extra-light);
}
.pr-toolbar-group { display:flex; align-items:center; gap:6px; flex:none; }
.pr-toolbar-group + .pr-toolbar-group { padding-left:10px; border-left:1px solid var(--el-border-color-lighter); }
.pr-page-controls { margin-left:auto; }
.pr-research-controls { margin-left:auto; }
.pr-research-menu { display:none; flex:none; margin-left:auto; }
.pr-mode :deep(.el-radio-button__inner) { padding: 6px 10px; font-size: 12px; }
.pr-pageinfo { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.pr-pageinfo :deep(.el-input-number) { width:82px; }
.pr-total { white-space: nowrap; }
.pr-zoom { font-size: 12px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.pr-body-wrap { display: flex; flex: 1; min-height: 300px; overflow: hidden; border: 1px solid var(--el-border-color-extra-light); border-radius: 0 0 8px 8px; }
.pr-toc { width:clamp(250px,22vw,320px); flex-shrink:0; display:flex; flex-direction:column; overflow:hidden; background:var(--study-surface-paper); border-right:1px solid var(--el-border-color-extra-light); }
.pr-toc-title { display:flex; align-items:center; justify-content:space-between; min-height:40px; padding:8px 12px; border-bottom:1px solid var(--el-border-color-lighter); color:var(--el-text-color-primary); font-size:13px; font-weight:700; }
.pr-toc-title small { color:var(--el-text-color-secondary); font-weight:400; }
.pr-toc-list { flex:1; overflow-y:auto; padding:6px; }
.pr-toc-item { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; width:100%; min-height:34px; padding-top:7px; padding-right:8px; padding-bottom:7px; border:0; border-radius:7px; background:transparent; color:var(--el-text-color-regular); font-size:13px; line-height:1.45; text-align:left; cursor:pointer; }
.pr-toc-item span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pr-toc-item small { flex:none; color:var(--el-text-color-placeholder); font:12px Georgia,serif; }
.pr-toc-item:hover { background: var(--el-color-primary-light-9); }
.pr-toc-item.active { background: var(--el-color-primary-light-8); color: var(--el-color-primary); font-weight: 600; }
.pr-toc-empty { display:flex; flex:1; flex-direction:column; justify-content:center; gap:7px; padding:20px; color:var(--el-text-color-secondary); text-align:center; }
.pr-toc-empty b { color:var(--el-text-color-primary); }
.pr-toc-review { margin:8px; padding:8px 10px; border:1px solid var(--el-border-color); border-radius:8px; background:#fffaf2; color:var(--el-color-primary); cursor:pointer; }
.pr-body { position: relative; flex: 1; overflow: auto; padding: 10px 14px; background: #525659; }
.pr-body.pr-mode-scroll, .pr-body.pr-mode-single { text-align: center; }
.pr-body.pr-mode-double { text-align: center; white-space: nowrap; }
.pr-page { position: relative; box-shadow: 0 2px 10px rgba(0,0,0,.4); background: #fff; }
.pr-mode-scroll .pr-page, .pr-mode-single .pr-page { display: block; margin: 0 auto 10px; }
.pr-mode-double .pr-page { display: inline-block; vertical-align: top; margin: 0 4px; }
@media (max-width:1400px) {
  .pr-research-full { display:none; }
  .pr-research-menu { display:inline-flex; }
}
@media (max-width:1280px) {
  .pr-control-label { display:none; }
  .pr-research-controls { margin-left:0; }
  .pr-display-controls .el-button:nth-of-type(3),.pr-display-controls .el-button:nth-of-type(4) { display:none; }
}
@media (max-width:820px) {
  .pr-toc { position:absolute; inset:43px auto 0 0; z-index:8; width:min(82vw,320px); box-shadow:6px 0 18px rgba(15,23,42,.16); }
}
.pr-canvas { display: block; }
.text-layer {
  position: absolute; inset: 0; overflow: clip; line-height: 1; cursor: text;
  text-align: initial; letter-spacing: normal; word-spacing: normal; text-size-adjust: none;
  transform-origin: 0 0; z-index: 1; --min-font-size: 1; --min-font-size-inv: calc(1 / var(--min-font-size));
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
}
.text-layer :deep(:is(span, br)) { position: absolute; white-space: pre; transform-origin: 0 0; color: transparent; cursor: text; user-select: text; }
.text-layer :deep(> :not(.markedContent)), .text-layer :deep(.markedContent span:not(.markedContent)) {
  z-index: 1; --font-height: 0; --scale-x: 1; --rotate: 0deg;
  font-size: calc(var(--text-scale-factor) * var(--font-height));
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
.text-layer :deep(.markedContent) { display: contents; }
.text-layer :deep(span[data-source="ocr"]) {
  display: inline-block; line-height: 1; --ocr-scale-x: 1;
  transform: scaleX(var(--ocr-scale-x));
}
.text-layer :deep(span::selection) { background: rgba(59, 130, 246, 0.4); }
.pr-hl { position: absolute; z-index: 2; border-radius: 2px; pointer-events: none; }
.pr-hl-highlight { background:var(--mark-color); opacity:.24; mix-blend-mode:multiply; }
.pr-hl-underline { height:0!important; margin-top:-2px; border-bottom:2px solid var(--mark-color); border-radius:0; }
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
  position: absolute; z-index: 60; width: 280px; padding: 10px; top: 58px; right: 12px;
  background: #fff; border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.3);
  border: 1px solid var(--el-border-color-light);
}
.pr-ocr-state { position:absolute; right:8px; top:8px; z-index:3; padding:3px 7px; border-radius:10px; background:rgba(15,23,42,.72); color:#fff; font-size:11px; pointer-events:none; }
.pr-ocr-state.ready { opacity:.55; }
.ann-card-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.ann-mark-types { margin-bottom:8px; }
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
.ann-dot.underline { height:8px; border-bottom:2px solid var(--ann-color); border-radius:0; }
.ann-text { font-size: 13px; color: var(--el-text-color-regular); margin-bottom: 4px; }
.ann-note { font-size: 12px; color: var(--el-text-color-secondary); }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
