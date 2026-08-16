<template>
  <div class="reader-page">
    <div class="reader-top">
      <el-button size="small" @click="$router.push('/library')">← 返回资料库</el-button>
      <span class="reader-title">📖 {{ book?.title || '阅读器' }}</span>
      <el-tag v-if="currentChapter" size="small" type="warning">{{ currentChapter }}</el-tag>
      <el-button v-if="book && book.file_type === 'pdf'" size="small" type="primary" plain @click="toggleMd">📝 Markdown 精读版</el-button>
      <span class="reader-tip">本地渲染 · 选中文字可 AI 解释/翻译/高亮 · 深色模式 · 目录跳转 · 阅读位置自动记忆</span>
    </div>
    <div class="reader-body">
      <DocReader v-if="book && book.file_type !== 'pdf'" :book-id="book.id" />
      <div v-else-if="showMd" class="md-view">
        <div class="md-toolbar">
          <el-button size="small" @click="showMd = false">← 返回 PDF</el-button>
          <span class="reader-title">📝 Markdown 精读版</span>
          <span class="reader-tip">标题目录 + AI 逐章精读总结 + 正文（由深度分析生成）</span>
        </div>
        <div class="md-content markdown-body" v-if="mdText" v-html="renderMarkdown(mdText)"></div>
        <div v-else-if="mdLoading" v-loading="true" style="height: 200px" />
        <div v-else class="md-empty">尚无 Markdown：<el-button size="small" type="primary" plain @click="runDeep">生成（深度分析）</el-button></div>
      </div>
      <PdfReader v-else :src="fileUrl" :book-id="book.id" :initial-page="initialPage"
        :toc="tocFlat" show-toc show-ai :use-saved-pos="!hasQueryPage" @page-change="onPageChange" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PdfReader from '../components/PdfReader.vue'
import DocReader from '../components/DocReader.vue'
import { getBook, bookFileUrl, getBookDeep, deepAnalyze, getTask } from '../api'
import { renderMarkdown } from '../utils/markdown'
import { ElMessage } from 'element-plus'

const route = useRoute()
const book = ref(null)
const tocFlat = ref([])
const hasQueryPage = ref(route.query.page != null)
const initialPage = ref(parseInt(route.query.page) || 1)
const currentChapter = ref('')
const showMd = ref(false)
const mdText = ref('')
const mdLoading = ref(false)

const fileUrl = computed(() => book.value ? bookFileUrl(book.value.id) : '')

const toggleMd = async () => {
  showMd.value = !showMd.value
  if (showMd.value && !mdText.value) await loadMd()
}

const loadMd = async () => {
  if (!book.value) return
  mdLoading.value = true
  try {
    const d = await getBookDeep(book.value.id)
    if (d.status === 'done' && d.markdown) {
      mdText.value = d.markdown
    } else if (d.status === 'running' || d.status === 'pending') {
      mdText.value = '⏳ 深度分析进行中，请稍后刷新…'
    } else {
      mdText.value = ''
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    mdLoading.value = false
  }
}

const runDeep = async () => {
  if (!book.value) return
  try {
    const resp = await deepAnalyze(book.value.id)
    ElMessage.success('深度分析已启动，请稍后刷新查看')
    // 轮询
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 3000))
      const t = await getTask(resp.task_id)
      if (t.status === 'done') { loadMd(); ElMessage.success('深度分析完成'); return }
      if (t.status === 'failed') { ElMessage.error('深度分析失败：' + (t.error || '')); return }
    }
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const onPageChange = ({ page, chapter }) => {
  currentChapter.value = chapter
  document.title = (chapter ? chapter + ' · ' : '') + (book.value?.title || 'PDF 阅读器')
}

const loadBook = async (bookId) => {
  book.value = null
  mdText.value = ''
  tocFlat.value = []
  try {
    book.value = await getBook(bookId)
    const flat = []
    const walk = (nodes, level) => {
      for (const n of nodes) {
        flat.push({ id: n.id, title: n.title, level: n.level || level, start_page: n.start_page })
        if (n.children?.length) walk(n.children, level + 1)
      }
    }
    walk(book.value.chapters || [], 1)
    tocFlat.value = flat
  } catch (e) {
    book.value = null
  }
}

// 路由参数变化时重新加载（docx ⇄ pdf 等切换）
watch(() => route.params.bookId, (id) => {
  if (id) loadBook(Number(id))
})

onMounted(() => {
  loadBook(Number(route.params.bookId))
})
</script>

<style scoped>
.reader-page { display: flex; flex-direction: column; height: calc(100vh - 70px); }
.reader-top { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; flex-wrap: wrap; }
.reader-title { font-size: 15px; font-weight: 600; color: var(--el-text-color-primary); }
.reader-tip { font-size: 12px; color: var(--el-text-color-secondary); }
.reader-body { flex: 1; min-height: 0; }
</style>
