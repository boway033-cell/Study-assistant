<template>
  <el-drawer v-model="visible" :title="title" size="48%" destroy-on-close>
    <div v-if="loading" v-loading="true" style="height: 200px" />
    <template v-else>
      <div class="loc-meta">
        <el-button size="small" type="primary" plain @click="openFull">⛶ 全屏阅读</el-button>
        <el-tag v-if="meta.chapter" size="small" type="info">{{ meta.chapter }}</el-tag>
        <el-tag v-if="meta.pageStart" size="small" type="warning">第 {{ meta.pageStart }} - {{ meta.pageEnd }} 页</el-tag>
        <el-tag size="small" type="success">{{ meta.bookType }}</el-tag>
      </div>

      <!-- 原文文本 -->
      <div class="orig-text">{{ original.content }}</div>

      <!-- PDF 原文：pdf.js 内置阅读器（无需下载，直接内嵌渲染） -->
      <div v-if="meta.bookType === 'pdf'" class="pdf-view">
        <div class="pdf-tip">📄 PDF 原文（第 {{ meta.pageStart }} 页起）—— 内置阅读器，滚轮翻页 / Ctrl+滚轮缩放</div>
        <PdfReader :src="pdfUrl" :book-id="meta.bookId" :initial-page="meta.pageStart || 1" />
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import PdfReader from './PdfReader.vue'
import { getChunkOriginal, bookFileUrl } from '../api'
import { ElMessage } from 'element-plus'

const router = useRouter()
const visible = ref(false)
const loading = ref(false)
const original = ref({})
const meta = ref({ bookId: null, chunkId: null, chapter: '', pageStart: null, pageEnd: null, bookType: '' })

const title = computed(() => meta.value.chapter ? `原文定位：${meta.value.chapter}` : '原文定位')
const pdfUrl = computed(() => {
  if (meta.value.bookType !== 'pdf' || !meta.value.bookId) return ''
  return bookFileUrl(meta.value.bookId)
})

const open = async ({ bookId, chunkId, chapter = '', pageStart = null, pageEnd = null, bookType = 'pdf' }) => {
  meta.value = { bookId, chunkId, chapter, pageStart, pageEnd, bookType }
  visible.value = true
  loading.value = true
  try {
    const data = await getChunkOriginal(bookId, chunkId)
    original.value = data
    if (!pageStart && data.page_start) {
      meta.value.pageStart = data.page_start
      meta.value.pageEnd = data.page_end
    }
  } catch (e) {
    ElMessage.error('获取原文失败：' + e.message)
  } finally {
    loading.value = false
  }
}

const openFull = () => {
  visible.value = false
  router.push('/reader/' + meta.value.bookId + '?page=' + (meta.value.pageStart || 1))
}

defineExpose({ open })
</script>

<style scoped>
.loc-meta { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.orig-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 1.9; color: var(--el-text-color-primary);
  max-height: 260px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light); margin-bottom: 16px;
}
.pdf-view { border-top: 1px solid var(--el-border-color-light); padding-top: 12px; }
.pdf-tip { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 8px; }
</style>