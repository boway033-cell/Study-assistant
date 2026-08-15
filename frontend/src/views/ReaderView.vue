<template>
  <div class="reader-page">
    <div class="reader-top">
      <el-button size="small" @click="$router.push('/library')">← 返回资料库</el-button>
      <span class="reader-title">📖 {{ book?.title || 'PDF 阅读器' }}</span>
      <span class="reader-tip">本地渲染 · 选中文字可 AI 解释/翻译/高亮 · 深色模式 · 目录跳转 · 阅读位置自动记忆</span>
    </div>
    <div class="reader-body">
      <PdfReader v-if="book" :src="fileUrl" :book-id="book.id" :initial-page="initialPage"
        :toc="tocFlat" show-toc show-ai />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PdfReader from '../components/PdfReader.vue'
import { getBook, bookFileUrl } from '../api'

const route = useRoute()
const book = ref(null)
const tocFlat = ref([])
const initialPage = ref(1)

const fileUrl = computed(() => book.value ? bookFileUrl(book.value.id) : '')

onMounted(async () => {
  const bookId = Number(route.params.bookId)
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
})
</script>

<style scoped>
.reader-page { display: flex; flex-direction: column; height: calc(100vh - 120px); }
.reader-top { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.reader-title { font-size: 15px; font-weight: 600; color: var(--el-text-color-primary); }
.reader-tip { font-size: 12px; color: var(--el-text-color-secondary); }
.reader-body { flex: 1; min-height: 0; }
</style>
