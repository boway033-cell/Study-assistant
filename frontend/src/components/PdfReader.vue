<template>
  <div class="pdf-reader">
    <div class="pr-toolbar">
      <el-button-group>
        <el-button size="small" :disabled="page <= 1" @click="go(-1)">上一页</el-button>
        <el-button size="small" :disabled="page >= numPages" @click="go(1)">下一页</el-button>
      </el-button-group>
      <span class="pr-pageinfo">
        <el-input-number v-model="page" :min="1" :max="numPages || 1" size="small" controls-position="right" style="width: 100px" />
        <span class="pr-total">/ {{ numPages || '…' }} 页</span>
      </span>
      <el-button-group>
        <el-button size="small" @click="zoom(-0.2)">−</el-button>
        <span class="pr-zoom">{{ Math.round(scale * 100) }}%</span>
        <el-button size="small" @click="zoom(0.2)">＋</el-button>
        <el-button size="small" @click="fitPage">适应</el-button>
      </el-button-group>
    </div>
    <div class="pr-body" ref="wrap" @wheel.prevent="onWheel">
      <canvas ref="canvas" class="pr-canvas" />
      <div v-if="loading" class="pr-loading" v-loading="true" element-loading-text="正在渲染原文…" />
      <div v-if="errorMsg" class="pr-error">⚠️ {{ errorMsg }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const props = defineProps({
  src: { type: String, default: '' },   // 原始 PDF 的 URL（后端 inline 提供）
  initialPage: { type: Number, default: 1 },
})

const canvas = ref(null)
const wrap = ref(null)
const page = ref(1)
const numPages = ref(0)
const scale = ref(1.1)
const loading = ref(false)
const errorMsg = ref('')

let pdfDoc = null
let renderTask = null
let renderSeq = 0

const loadPdf = async () => {
  if (!props.src) return
  loading.value = true
  errorMsg.value = ''
  // 释放旧文档
  if (pdfDoc) {
    try { pdfDoc.destroy() } catch { /* ignore */ }
    pdfDoc = null
  }
  try {
    const doc = await pdfjsLib.getDocument({
      url: props.src,
      disableAutoFetch: true,          // 低内存：按需拉取页面
      disableStream: false,
    }).promise
    pdfDoc = doc
    numPages.value = doc.numPages
    page.value = Math.min(Math.max(1, props.initialPage || 1), doc.numPages)
    await renderPage()
  } catch (e) {
    console.error('pdf load error', e)
    errorMsg.value = 'PDF 加载失败：' + (e.message || e)
  } finally {
    loading.value = false
  }
}

const renderPage = async () => {
  const seq = ++renderSeq
  if (!pdfDoc || !canvas.value) return
  if (renderTask) {
    try { await renderTask.cancel() } catch { /* ignore */ }
    renderTask = null
  }
  try {
    if (seq !== renderSeq) return
    const pdfPage = await pdfDoc.getPage(page.value)
    if (seq !== renderSeq) return
    const viewport = pdfPage.getViewport({ scale: scale.value })
    const dpr = window.devicePixelRatio || 1
    const cv = canvas.value
    cv.width = Math.floor(viewport.width * dpr)
    cv.height = Math.floor(viewport.height * dpr)
    cv.style.width = Math.floor(viewport.width) + 'px'
    cv.style.height = Math.floor(viewport.height) + 'px'
    if (seq !== renderSeq) return
    const ctx = cv.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const task = pdfPage.render({ canvasContext: ctx, viewport })
    renderTask = task
    await task.promise
  } catch (e) {
    if (e?.name !== 'RenderingCancelledException') {
      console.error('render error', e)
    }
  }
}

const go = (delta) => {
  const p = page.value + delta
  if (p >= 1 && p <= (numPages.value || 1)) {
    page.value = p
  }
}

const zoom = (d) => {
  scale.value = Math.min(3, Math.max(0.4, Math.round((scale.value + d) * 100) / 100))
}

const fitPage = () => {
  if (!wrap.value) return
  const w = wrap.value.clientWidth - 24
  const base = pdfDoc ? 595 : 612  // A4 宽（pt）
  scale.value = Math.max(0.4, Math.min(2, w / base))
}

const onWheel = (e) => {
  if (e.ctrlKey || e.metaKey || e.deltaY === 0) {
    // Ctrl+滚轮缩放
    zoom(e.deltaY > 0 ? -0.1 : 0.1)
  } else {
    // 普通滚轮：上/下翻页（按住 Shift 则横向滚动）
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      wrap.value.scrollLeft += e.deltaX
    } else {
      go(e.deltaY > 0 ? 1 : -1)
    }
  }
}

watch(page, () => nextTick(renderPage))
watch(scale, () => nextTick(renderPage))
watch(() => props.src, () => {
  if (props.src) loadPdf()
})

onBeforeUnmount(() => {
  if (renderTask) { try { renderTask.cancel() } catch { /* ignore */ } }
  if (pdfDoc) { try { pdfDoc.destroy() } catch { /* ignore */ } }
})

// 初始加载
loadPdf()
</script>

<style scoped>
.pdf-reader { display: flex; flex-direction: column; height: 100%; min-height: 320px; }
.pr-toolbar {
  display: flex; align-items: center; gap: 10px; padding: 6px 10px;
  background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0;
  border: 1px solid var(--el-border-color-extra-light); flex-wrap: wrap;
}
.pr-pageinfo { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--el-text-color-secondary); }
.pr-total { white-space: nowrap; }
.pr-zoom { font-size: 12px; color: var(--el-text-color-secondary); min-width: 44px; text-align: center; }
.pr-body {
  position: relative; flex: 1; overflow: auto; padding: 12px; text-align: center;
  background: #525659; border-radius: 0 0 8px 8px;
  border: 1px solid var(--el-border-color-extra-light); min-height: 300px;
}
.pr-canvas { box-shadow: 0 2px 10px rgba(0,0,0,.4); background: #fff; margin: 0 auto; }
.pr-loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.pr-error { color: #ffd9a0; padding: 20px; font-size: 13px; }
</style>