<template>
  <el-drawer v-model="visible" :title="title" size="48%" destroy-on-close>
    <div v-if="loading" v-loading="true" style="height: 200px" />
    <template v-else>
      <div class="loc-meta">
        <el-button v-if="identity?.showContent" size="small" type="primary" plain @click="openFull">⛶ 全屏阅读</el-button>
        <el-tag v-if="meta.chapter" size="small" type="info">{{ meta.chapter }}</el-tag>
        <el-tag v-if="meta.pageStart" size="small" type="warning">第 {{ meta.pageStart }} - {{ meta.pageEnd || meta.pageStart }} 页</el-tag>
        <el-tag size="small" type="success">{{ meta.bookType }}</el-tag>
      </div>

      <el-alert v-if="identity" :type="identity.tone" :title="identity.message" :closable="false" show-icon class="identity-alert" />

      <!-- 原文文本：身份不可信（invalid/needs_review/missing）时绝不显示当前正文 -->
      <div v-if="identity?.showContent" class="orig-text">{{ original.content }}</div>
      <div v-else-if="identity?.snippet" class="orig-snippet">
        <div class="orig-snippet-hint">引用片段（仅作定位线索，不能当作当前正文）</div>
        <blockquote class="orig-snippet-text">{{ identity.snippet }}</blockquote>
      </div>
      <el-empty v-else-if="identity" description="该引用没有可展示的原文；请回到引用出处核对" :image-size="60" />

      <!-- PDF 原文：pdf.js 内置阅读器（无需下载，直接内嵌渲染）。仅在引用原文可信时展示当前 PDF。 -->
      <div v-if="identity?.showContent && meta.bookType === 'pdf'" class="pdf-view">
        <div class="pdf-tip">📄 PDF 原文（第 {{ meta.pageStart }} 页起）—— 内置阅读器，滚轮翻页 / Ctrl+滚轮缩放</div>
        <PdfReader :src="pdfUrl" :book-id="meta.bookId" :initial-page="meta.pageStart || 1" :use-saved-pos="false" />
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
import { buildChunkOriginalQuery, resolveCitationOriginal } from '../utils/citationIdentity.js'

const router = useRouter()
const visible = ref(false)
const loading = ref(false)
const original = ref({})
const identity = ref(null)
const meta = ref({ bookId: null, chunkId: null, chapter: '', pageStart: null, pageEnd: null, bookType: '' })

const title = computed(() => meta.value.chapter ? `原文定位：${meta.value.chapter}` : '原文定位')
const pdfUrl = computed(() => {
  if (meta.value.bookType !== 'pdf' || !meta.value.bookId) return ''
  return bookFileUrl(meta.value.bookId)
})

const open = async ({
  bookId, chunkId, chapter = '', pageStart = null, pageEnd = null, bookType = 'pdf',
  contentHash = '', sourceVersionUid = '', snippet = '',
}) => {
  meta.value = { bookId, chunkId, chapter, pageStart, pageEnd, bookType }
  original.value = {}
  identity.value = null
  visible.value = true
  loading.value = true
  try {
    // 把引用保存的指纹一并传给后端，后端据此判断是否仍是同一段原文。
    const data = await getChunkOriginal(bookId, chunkId, {
      params: buildChunkOriginalQuery({ content_hash: contentHash, source_version_uid: sourceVersionUid }),
    })
    original.value = data || {}
    identity.value = resolveCitationOriginal({ data, snippet })
    if (!pageStart && data?.page_start) {
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
.identity-alert { margin-bottom: 12px; }
.identity-alert :deep(.el-alert__title) { line-height: 1.6; }
.orig-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 1.9; color: var(--el-text-color-primary);
  max-height: 260px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light); margin-bottom: 16px;
}
.orig-snippet { background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px; border: 1px solid var(--el-border-color-extra-light); margin-bottom: 16px; }
.orig-snippet-hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; margin-bottom: 8px; }
.orig-snippet-text { margin: 0; padding-left: 12px; border-left: 3px solid var(--el-border-color); color: var(--el-text-color-regular); font-size: 14px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }
.pdf-view { border-top: 1px solid var(--el-border-color-light); padding-top: 12px; }
.pdf-tip { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 8px; }
</style>
