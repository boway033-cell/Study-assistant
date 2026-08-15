<!-- PdfReader v2：本地渲染为底层（滚动/文本层/标注/目录/位置记忆/深色），AI 为可选增强 -->
<template>
  <div class="pdf-reader" :class="{ 'pr-dark': dark }">
    <div class="pr-toolbar">
      <el-button v-if="showToc" size="small" :type="showTocPanel ? 'primary' : ''" @click="showTocPanel = !showTocPanel">📑 目录</el-button>
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
        <el-button size="small" @click="fitWidth">适应</el-button>
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

      <div ref="scroller" class="pr-body" @scroll="onScroll" @mouseup="onMouseUp" @mousedown="onMouseDown">
        <div v-for="p in pageList" :key="p" class="pr-page" :data-page="p" :style="{ height: pageHeights[p] ? pageHeights[p] + 'px' : undefined }">
          <canvas :ref="(el) => setCanvasRef(p, el)" class="pr-canvas" />
          <div :ref="(el) => setTextRef(p, el)" class="text-layer"></div>
          <div v-for="a in pageAnns(p)" :key="a.id" class="pr-hl"
            :style="hlStyle(a, p)" @click="openAnn(a)" :title="a.text || ''" />
        </div>
        <div v-if="loading" class="pr-loading" v-loading="true" element-loading-text="正在渲染原文…" />
        <div v-if="errorMsg" class="pr-error">⚠️ {{ errorMsg }}</div>
      </div>
    </div>

    <!-- 选中文字浮动工具条 -->
    <div v-if="selToolbar" class="pr-sel-bar" :style="{ top: selPos.y + 'px', left: selPos.x + 'px' }">
      <el-button size="small" type="primary" @click="aiAction('explain')">💡 解释</el-button>
      <el-button size="small" type="success" @click="aiAction('translate')">🌐 翻译</el-button>
      <el-button size="small" type="warning" @click="addHighlight">🖍 高亮</el-button>
    </div>

    <!-- AI 结果抽屉 -->
    <el-drawer v-model="aiPanel" :title="aiTitle" size="42%">
      <div v-if="aiLoading" v-loading="true" style="height: 200px" />
      <div v-else-if="aiResult" class="ai-result" v-html="aiResultHtml"></div>
      <el-empty v-else description="等待操作" :image-size="80" />
    </el-drawer>

    <!-- 标注管理抽屉 -->
    <el-drawer v-model="showAnnPanel" title="我的标注" size="40%">
      <div v-if="!annotations.length" class="form-tip">还没有标注：在正文中选中文字 → 点「🖍 高亮」即可添加</div>
      <div v-for="a in annotations" :key="a.id" class="ann-item">
        <div class="ann-head">
          <el-tag size="small" type="warning">第 {{ a.page }} 页</el-tag>
          <el-button link size="small" @click="jumpToPage(a.page)">跳转</el-button>
          <el-button link size="small" type="danger" @click="removeAnn(a)">删除</el-button>
        </div>
        <div class="ann-text">{{ a.text || '' }}</div>
        <el-input v-model="a.note" size="small" placeholder="写笔记…（可关联知识树节点）" @change="saveAnnNote(a)" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  listAnnotations, createAnnotation, updateAnnotation, deleteAnnotation,
  aiExplain, aiSummarize, aiVision, listBooks, getBook,
} from '../api'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps({
  src: { type: String, default: '' },
  bookId: { type: Number, default: null },
  initialPage: { type: Number, default: 1 },
  toc: { type: Array, default: () => [] },      // [{id,title,level,start_page}]
  showToc: { type: Boolean, default: false },
  showAi: { type: Boolean, default: false },
})

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
const pageHeights = ref({})
const rendered = ref({})
const annotations = ref([])
const showAnnPanel = ref(false)

// AI 状态
const aiPanel = ref(false)
const aiTitle = ref('AI 解读')
const aiLoading = ref(false)
const aiResult = ref('')
const aiBusy = ref(false)
const selToolbar = ref(false)
const selPos = ref({ x: 0, y: 0 })
let selText = ''
let selRect = null

let pdfDoc = null
let renderSeq = 0
let bookTitle = ''

const prEl = (p) => document.querySelector('.pr-page[data-page="' + p + '"]')

function setCanvasRef(p, el) { if (el) canvasRefs[p] = el }
function setTextRef(p, el) { if (el) textRefs[p] = el }

const aiResultHtml = computed(() => {
  const t = aiResult.value || ''
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br/>').replace(/#{1,3} (.+)/g, '<b>$1</b>')
})

// ===== 加载 =====
const loadPdf = async () => {
  if (!props.src) return
  loading.value = true
  errorMsg.value = ''
  if (pdfDoc) { try { pdfDoc.destroy() } catch {} pdfDoc = null }
  try {
    const doc = await pdfjsLib.getDocument({
      url: props.src,
      disableAutoFetch: true,
      cMapUrl: 'cmaps/',
      cMapPacked: true,
      standardFontDataUrl: 'standard_fonts/',
    }).promise
    pdfDoc = doc
    numPages.value = doc.numPages
    pageList.value = Array.from({ length: doc.numPages }, (_, i) => i + 1)
    // 计算每页高度
    const heights = {}
    const tasks = []
    for (let i = 1; i <= doc.numPages; i++) {
      tasks.push(doc.getPage(i).then(pg => {
        const vp = pg.getViewport({ scale: scale.value })
        heights[i] = Math.floor(vp.height)
      }))
    }
    await Promise.all(tasks)
    pageHeights.value = heights
    // 初始定位
    const saved = readPos()
    const target = saved && saved.page ? saved.page : (props.initialPage || 1)
    page.value = Math.min(Math.max(1, target), doc.numPages)
    await renderVisible()
    if (saved && saved.scrollTop) {
      await nextTick()
      scroller.value.scrollTop = saved.scrollTop
    } else {
      await nextTick()
      scrollToPage(page.value, false)
    }
    await loadAnnotations()
  } catch (e) {
    console.error('pdf load error', e)
    errorMsg.value = 'PDF 加载失败：' + (e.message || e)
  } finally {
    loading.value = false
  }
}

// ===== 虚拟滚动渲染 =====
const currentScale = ref(scale.value)
watch(scale, () => {
  currentScale.value = scale.value
  reRenderAll()
})

const onScroll = () => {
  const visible = visibleRange()
  renderVisible(visible)
  savePosDebounced()
  // 更新当前页指示
  const off = pageOffset(visible.start)
  page.value = visible.start
}

const visibleRange = () => {
  if (!scroller.value || !numPages.value) return { start: 1, end: 1 }
  const st = scroller.value.scrollTop
  const ch = scroller.value.clientHeight
  const total = pageHeights.value
  let acc = 0
  let start = 1
  let end = 1
  for (let p = 1; p <= numPages.value; p++) {
    const h = total[p] || 800
    if (acc + h > st && start === 1) start = p
    if (acc + h > st + ch) { end = p; break }
    acc += h
    end = p
  }
  return { start: Math.max(1, start - 1), end: Math.min(numPages.value, end + 1) }
}

const pageOffset = (p) => {
  let acc = 0
  for (let i = 1; i < p; i++) acc += pageHeights.value[i] || 800
  return acc
}

const renderVisible = async (range) => {
  const r = range || visibleRange()
  const want = new Set()
  for (let p = r.start; p <= r.end; p++) want.add(p)
  for (const p of Object.keys(rendered.value)) {
    if (!want.has(Number(p))) clearPage(Number(p))
  }
  for (const p of want) {
    if (!rendered.value[p]) renderPage(Number(p))
  }
}

const renderTasks = {}
const pendingRenders = new Set()

const renderPage = async (p) => {
  if (!pdfDoc || rendered.value[p] || pendingRenders.has(p)) return
  pendingRenders.add(p)
  try {
    // 取消该页旧的渲染任务，避免同一 canvas 并发渲染
    if (renderTasks[p]) {
      try { await renderTasks[p].cancel() } catch { /* ignore */ }
      delete renderTasks[p]
    }
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
    // 文本层（本地，支持选择/高亮）
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
  if (renderTasks[p]) { try { renderTasks[p].cancel() } catch { /* ignore */ } delete renderTasks[p] }
  pendingRenders.delete(p)
  const cv = canvasRefs[p]
  if (cv) { cv.width = 1; cv.height = 1 }
  const tl = textRefs[p]
  if (tl) tl.innerHTML = ''
  rendered.value[p] = false
}

const reRenderAll = async () => {
  // 缩放/深色变化：按新 scale 重算高度与渲染
  if (!pdfDoc) return
  const heights = {}
  for (let i = 1; i <= numPages.value; i++) {
    const pg = await pdfDoc.getPage(i)
    heights[i] = Math.floor(pg.getViewport({ scale: scale.value }).height)
  }
  pageHeights.value = heights
  const cur = page.value
  await nextTick()
  scrollToPage(cur, false)
  renderVisible()
}

const goPage = (delta) => {
  const p = Math.min(numPages.value, Math.max(1, page.value + delta))
  scrollToPage(p)
}

const scrollToPage = (p, smooth = true) => {
  if (!scroller.value) return
  page.value = p
  const top = pageOffset(p) + 2
  scroller.value.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
  renderVisible()
}

const jumpToPage = (p) => { scrollToPage(p) }

const zoomBy = (d) => {
  scale.value = Math.min(2.5, Math.max(0.5, Math.round((scale.value + d) * 100) / 100))
}
const fitWidth = () => {
  if (!scroller.value || !numPages.value) return
  const w = scroller.value.clientWidth - 30
  const base = 595
  scale.value = Math.max(0.5, Math.min(2, w / base))
}

// ===== 位置记忆（本地 localStorage）=====
const posKey = () => 'sa-reader-' + (props.bookId || props.src)
const readPos = () => {
  try {
    const v = JSON.parse(localStorage.getItem(posKey()) || 'null')
    return v
  } catch { return null }
}
const savePos = () => {
  try {
    localStorage.setItem(posKey(), JSON.stringify({ page: page.value, scrollTop: scroller.value?.scrollTop || 0 }))
  } catch {}
}
let saveTimer = null
const savePosDebounced = () => {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(savePos, 800)
}

// ===== 标注（本地存储）=====
const pageAnns = (p) => annotations.value.filter(a => a.page === p)
const hlStyle = (a, p) => {
  const h = pageHeights.value[p] || 800
  let w = 595 * scale.value
  const el = prEl(p)
  if (el) w = el.clientWidth
  let rects = []
  try { rects = JSON.parse(a.rect_json) } catch {}
  const first = rects[0] || { x: 0.02, y: 0, w: 0.2, h: 0.03 }
  return {
    left: (first.x * 100) + '%',
    top: (first.y * 100) + '%',
    width: (first.w * 100) + '%',
    height: (first.h * 100) + '%',
    background: (a.color || '#f9e572') + '99',
  }
}

const loadAnnotations = async () => {
  if (!props.bookId) return
  try {
    annotations.value = await listAnnotations(props.bookId)
  } catch { /* ignore */ }
}

const onMouseUp = async (e) => {
  if (!props.bookId) return
  await nextTick()
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !sel.toString().trim()) { selToolbar.value = false; return }
  // 选区需落在文本层内
  const node = sel.anchorNode
  const tl = node?.parentElement?.closest('.text-layer')
  if (!tl) return
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  if (!rect.width) return
  selText = sel.toString().trim().slice(0, 2000)
  selRect = sel.getRangeAt(0).getClientRects()
  const scrollerRect = scroller.value.getBoundingClientRect()
  selPos.value = {
    x: Math.min(rect.left - scrollerRect.left + scroller.value.scrollLeft, scroller.value.scrollWidth - 300),
    y: rect.bottom - scrollerRect.top + scroller.value.scrollTop + 8,
  }
  selToolbar.value = true
}

const onMouseDown = () => {
  // 点击别处收起工具条（延迟，避免与 mouseup 冲突）
  setTimeout(() => { if (!selToolbar.value) return }, 0)
}

const addHighlight = async () => {
  if (!props.bookId || !selText || !selRect) return
  const pageEl = prEl(page.value)
  if (!pageEl) return
  const pageRect = pageEl.getBoundingClientRect()
  const rects = []
  for (const r of selRect) {
    rects.push({
      x: +( (r.left - pageRect.left) / pageRect.width ).toFixed(4),
      y: +( (r.top - pageRect.top) / pageRect.height ).toFixed(4),
      w: +( r.width / pageRect.width ).toFixed(4),
      h: +( r.height / pageRect.height ).toFixed(4),
    })
  }
  let knowledge_node_id = null
  try {
    const { value } = await ElMessageBox.prompt('可选：输入笔记（留空仅高亮）', '高亮标注', {
      confirmButtonText: '保存', cancelButtonText: '取消',
      inputPlaceholder: '写下你的理解…',
    })
    knowledge_node_id = null
  } catch { /* 取消 */ }
  try {
    await createAnnotation(props.bookId, {
      page: page.value,
      rect_json: JSON.stringify(rects),
      text: selText,
      color: '#f9e572',
      note: knowledge_node_id === null ? null : '',
    })
    ElMessage.success('已添加高亮')
    selToolbar.value = false
    window.getSelection()?.removeAllRanges()
    loadAnnotations()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const openAnn = (a) => {
  ElMessageBox.confirm((a.text || '') + '\n\n笔记：' + (a.note || '（无）'), '标注详情', {
    confirmButtonText: '删除', cancelButtonText: '关闭', type: 'info',
  }).then(async () => {
    await deleteAnnotation(a.id)
    loadAnnotations()
  }).catch(() => {})
}

const removeAnn = async (a) => {
  await deleteAnnotation(a.id)
  loadAnnotations()
}
const saveAnnNote = async (a) => {
  try {
    await updateAnnotation(a.id, { note: a.note || '' })
    ElMessage.success('笔记已保存')
  } catch (e) { ElMessage.error(e.message) }
}

// ===== AI 增强（可选：无 Key 时后端返回友好提示）=====
const aiAction = async (action) => {
  selToolbar.value = false
  if (!selText) return
  aiTitle.value = action === 'translate' ? '翻译' : 'AI 解释'
  aiPanel.value = true
  aiLoading.value = true
  aiResult.value = ''
  try {
    const resp = await aiExplain({ text: selText, action, book_title: bookTitle, chapter_title: '' })
    if (!resp.ok) throw new Error(resp.error || 'AI 调用失败')
    aiResult.value = resp.result
  } catch (e) {
    aiResult.value = '⚠️ ' + e.message
  } finally {
    aiLoading.value = false
  }
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
    if (!cur) { aiResult.value = '⚠️ 未找到当前页所属章节' ; return }
    const resp = await aiSummarize({ book_id: props.bookId, chapter_id: cur.id })
    if (!resp.ok) throw new Error(resp.error || 'AI 调用失败')
    aiResult.value = resp.result
  } catch (e) {
    aiResult.value = '⚠️ ' + e.message
  } finally {
    aiLoading.value = false
    aiBusy.value = false
  }
}

const currentChapter = () => {
  const flat = []
  const walk = (nodes) => {
    for (const n of nodes) {
      flat.push(n)
      if (n.children?.length) walk(n.children)
    }
  }
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
  } catch (e) {
    aiResult.value = '⚠️ ' + e.message
  } finally {
    aiLoading.value = false
    aiBusy.value = false
  }
}

// ===== 生命周期 =====
onMounted(async () => {
  try {
    const resp = await listBooks({ page_size: 100 })
    const b = resp.items.find(x => x.id === props.bookId)
    bookTitle = b?.title || ''
  } catch {}
  if (props.bookId) {
    try {
      const detail = await getBook(props.bookId)
      // 若无 toc 传入，用书籍章节树（需要扁平化）
    } catch {}
  }
  loadPdf()
})

onBeforeUnmount(() => {
  savePos()
  if (pdfDoc) { try { pdfDoc.destroy() } catch {} }
})
</script>

<style scoped>
.pdf-reader { display: flex; flex-direction: column; height: 100%; min-height: 360px; }
.pr-toolbar {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px; flex-wrap: wrap;
  background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0;
  border: 1px solid var(--el-border-color-extra-light);
}
.pr-pageinfo { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.pr-total { white-space: nowrap; }
.pr-zoom { font-size: 12px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.pr-body-wrap { display: flex; flex: 1; min-height: 300px; overflow: hidden; border: 1px solid var(--el-border-color-extra-light); border-radius: 0 0 8px 8px; }
.pr-toc { width: 180px; flex-shrink: 0; overflow-y: auto; background: #f6f9fb; border-right: 1px solid var(--el-border-color-extra-light); padding: 6px 0; }
.pr-toc-title { font-weight: 600; font-size: 12px; padding: 4px 10px; color: var(--el-text-color-primary); }
.pr-toc-item { font-size: 12px; padding: 4px 8px; cursor: pointer; color: var(--el-text-color-regular); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr-toc-item:hover { background: var(--el-color-primary-light-9); }
.pr-toc-item.active { background: var(--el-color-primary-light-8); color: var(--el-color-primary); font-weight: 600; }
.pr-body { position: relative; flex: 1; overflow: auto; padding: 10px 14px; background: #525659; text-align: center; }
.pr-page { position: relative; margin: 0 auto 10px; box-shadow: 0 2px 10px rgba(0,0,0,.4); background: #fff; width: min-content; }
.pr-canvas { display: block; }
.text-layer { position: absolute; inset: 0; overflow: hidden; opacity: 0.25; line-height: 1; }
.text-layer :deep(span) { position: absolute; white-space: pre; transform-origin: 0 0; color: transparent; }
.text-layer :deep(span::selection) { background: rgba(0, 120, 255, 0.3); }
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
.ai-result { font-size: 14px; line-height: 1.9; white-space: pre-wrap; color: var(--el-text-color-primary); }
.ann-item { padding: 10px; border: 1px solid var(--el-border-color-extra-light); border-radius: 8px; margin-bottom: 8px; }
.ann-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ann-text { font-size: 13px; color: var(--el-text-color-regular); margin-bottom: 6px; }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
