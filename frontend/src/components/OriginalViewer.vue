<template>
  <el-drawer v-model="visible" :title="title" size="45%" destroy-on-close>
    <div v-if="loading" v-loading="true" style="height: 200px" />
    <template v-else>
      <div class="loc-meta">
        <el-tag v-if="meta.chapter" size="small" type="info">{{ meta.chapter }}</el-tag>
        <el-tag v-if="meta.pageStart" size="small" type="warning">第 {{ meta.pageStart }} - {{ meta.pageEnd }} 页</el-tag>
        <el-tag size="small" type="success">{{ meta.bookType }}</el-tag>
      </div>

      <!-- 原文文本 -->
      <div class="orig-text">{{ original.content }}</div>

      <!-- PDF 原文渲染：iframe 定位到对应页 -->
      <div v-if="meta.bookType === 'pdf'" class="pdf-view">
        <div class="pdf-tip">📄 PDF 原文（第 {{ meta.pageStart }} 页）——浏览器内置查看器，可缩放/滚动</div>
        <iframe
          :src="pdfUrl"
          class="pdf-frame"
          frameborder="0"
        />
        <div class="pdf-actions">
          <el-button size="small" @click="goPage(-1)">上一页</el-button>
          <el-button size="small" @click="goPage(1)">下一页</el-button>
        </div>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed } from 'vue'
import { getChunkOriginal, getPageText, bookFileUrl } from '../api'
import { ElMessage } from 'element-plus'

const visible = ref(false)
const loading = ref(false)
const original = ref({})
const meta = ref({ bookId: null, chunkId: null, chapter: '', pageStart: null, pageEnd: null, bookType: '' })
const currentPage = ref(null)

const title = computed(() => meta.value.chapter ? `原文定位：${meta.value.chapter}` : '原文定位')
const pdfUrl = computed(() => {
  if (meta.value.bookType !== 'pdf' || !currentPage.value) return ''
  return `${bookFileUrl(meta.value.bookId)}#page=${currentPage.value}`
})

const open = async ({ bookId, chunkId, chapter = '', pageStart = null, pageEnd = null, bookType = 'pdf' }) => {
  meta.value = { bookId, chunkId, chapter, pageStart, pageEnd, bookType }
  currentPage.value = pageStart || 1
  visible.value = true
  loading.value = true
  try {
    const data = await getChunkOriginal(bookId, chunkId)
    original.value = data
    if (!pageStart && data.page_start) {
      meta.value.pageStart = data.page_start
      meta.value.pageEnd = data.page_end
      currentPage.value = data.page_start
    }
  } catch (e) {
    ElMessage.error('获取原文失败：' + e.message)
  } finally {
    loading.value = false
  }
}

const goPage = (delta) => {
  const p = (currentPage.value || 1) + delta
  if (p >= 1) {
    currentPage.value = p
    meta.value.pageStart = p
    meta.value.pageEnd = p
  }
}

defineExpose({ open })
</script>

<style scoped>
.loc-meta { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.orig-text {
  background: #f8f9fa; border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 1.9; color: #303133;
  max-height: 320px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid #ebeef5; margin-bottom: 16px;
}
.pdf-view { border-top: 1px solid #e4e7ed; padding-top: 12px; }
.pdf-tip { color: #909399; font-size: 13px; margin-bottom: 8px; }
.pdf-frame { width: 100%; height: 460px; border-radius: 8px; border: 1px solid #dcdfe6; }
.pdf-actions { margin-top: 8px; display: flex; gap: 8px; }
</style>
