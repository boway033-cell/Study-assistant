<template>
  <div class="chat-page study-page">
    <el-row :gutter="16" class="chat-row">
      <!-- 左：提问范围 + 历史 -->
      <el-col :span="4">
        <el-card shadow="never" class="side-card">
          <template #header>提问范围</template>
          <el-select v-model="bookId" placeholder="全部书籍" clearable filterable remote :remote-method="searchBooks" :loading="booksLoading" style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
          <el-divider />
          <div class="history-title">历史记录</div>
          <div v-for="h in history" :key="h.id" class="history-item" @click="viewHistory(h)">
            <div class="history-q">{{ h.question }}</div>
            <div class="history-time">{{ formatTime(h.created_at) }} · {{ h.model }}</div>
          </div>
          <el-button v-if="history.length < historyTotal" link class="history-more" :loading="historyLoading" @click="loadMoreHistory">加载更早记录</el-button>
        </el-card>
      </el-col>

      <!-- 中：对话 -->
      <el-col :span="11">
        <el-card shadow="never" class="chat-card">
          <template #header>
            <div class="chat-header">
              <span>AI 问答（按设置中的模型路由）</span>
            </div>
          </template>
          <div ref="msgBox" class="msg-box">
            <StudyEmptyState v-if="!messages.length" compact title="从一个可核验问题开始" description="答案会标注书目、章节与页码，选择引用后可在右侧核对原文。" />
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="msg-label">{{ m.role === 'user' ? '我' : 'AI' }}</div>
              <div class="msg-content">
                <div v-if="m.streaming" class="streaming" v-html="sanitizeHtml(m.content)" />
                <div v-else>{{ m.content }}</div>
                <div v-if="m.sources?.length" class="sources">
                  <el-tag v-for="(s, j) in m.sources" :key="j" size="small" type="info"
                    :effect="activeSourceIndex === i && activeSourceIdx === j ? 'dark' : 'plain'"
                    class="source-tag" @click="showSource(m, s, j)">
                    {{ (s.book_title ? '《' + s.book_title + '》' : '') + (s.chapter_title ? ' ' + s.chapter_title : '') }} {{ s.page_start && s.page_end && s.page_end !== s.page_start ? '第' + s.page_start + '-' + s.page_end + '页' : '第' + (s.page || s.page_start || '?') + '页' }} 📄
                  </el-tag>
                </div>
              </div>
            </div>
          </div>
          <div class="input-row">
            <el-input
              v-model="question"
              type="textarea"
              :rows="2"
              placeholder="输入你的问题，Enter 发送（Shift+Enter 换行）"
              @keydown.enter.exact.prevent="send"
            />
            <el-button type="primary" :loading="sending" @click="send" style="margin-left: 8px">
              {{ sending ? '生成中' : '发送' }}
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右：原文展示面板 -->
      <el-col :span="9">
        <el-card shadow="never" class="source-card">
          <template #header>📖 出处原文</template>
          <div v-if="sourceLoading" v-loading="true" style="height: 160px" />
          <template v-else-if="source.text">
            <div class="source-meta">
              <el-tag size="small" type="info">《{{ source.book_title || '' }}》</el-tag>
              <el-tag size="small" type="warning" v-if="source.chapter_title">{{ source.chapter_title }}</el-tag>
              <el-tag size="small" type="success">第 {{ source.page_start }} - {{ source.page_end }} 页</el-tag>
              <el-button link size="small" type="primary" @click="goFullRead" style="margin-left: auto">⛶ 全屏阅读</el-button>
              <el-radio-group v-model="sourceView" size="small">
                <el-radio-button value="text">文本</el-radio-button>
                <el-radio-button value="pdf" v-if="sourceBookType === 'pdf'">PDF 原文</el-radio-button>
              </el-radio-group>
            </div>
            <div v-if="sourceView === 'text'" class="source-text">{{ source.text }}</div>
            <div v-else-if="sourceView === 'pdf'" class="pdf-box">
              <PdfReader :src="pdfUrl" :book-id="source.book_id" :initial-page="source.page_start || 1" :use-saved-pos="false" />
            </div>
          </template>
          <el-empty v-else description="提问后，答案引用的原文会自动显示在这里" :image-size="80" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listBooks, chatStream, chatHistory, getChat, getChunkOriginal, getBook, bookFileUrl } from '../api'
import PdfReader from '../components/PdfReader.vue'
import StudyEmptyState from '../components/StudyEmptyState.vue'
import { sanitizeHtml } from '../utils/markdown'

const router = useRouter()
const route = useRoute()
const books = ref([])
const booksLoading = ref(false)
const bookId = ref(null)
const question = ref('')
const messages = ref([])
const sending = ref(false)
const history = ref([])
const historyPage = ref(1)
const historyTotal = ref(0)
const historyLoading = ref(false)
const msgBox = ref(null)

// 右侧原文面板状态
const source = ref({})
const sourceLoading = ref(false)
const sourceView = ref('text')
const sourceBookType = ref('')

const pdfUrl = computed(() => {
  if (!source.value.book_id) return ''
  return bookFileUrl(source.value.book_id)
})
const activeSourceIndex = ref(null)
const activeSourceIdx = ref(null)

const scrollBottom = () => {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

const loadSourcePanel = async (bookIdVal, chunkId) => {
  if (!bookIdVal || !chunkId) return
  sourceLoading.value = true
  try {
    const data = await getChunkOriginal(bookIdVal, chunkId)
    const book = books.value.find((b) => b.id === bookIdVal)
    const chapter = data.chapter_id ? (await fetchChapterTitle(bookIdVal, data.chapter_id)) : ''
    source.value = {
      book_id: bookIdVal,
      book_title: book?.title || '',
      chapter_title: chapter,
      page_start: data.page_start || null,
      page_end: data.page_end || null,
      text: data.content || '',
    }
    sourceBookType.value = book?.file_type || ''
    sourceView.value = 'text'
  } catch (e) {
    ElMessage.error('加载原文失败：' + e.message)
  } finally {
    sourceLoading.value = false
  }
}

const fetchChapterTitle = async (bookIdVal, chapterId) => {
  try {
    const detail = await getBook(bookIdVal)
    const walk = (nodes) => {
      for (const n of nodes) {
        if (n.id === chapterId) return n.title
        if (n.children?.length) {
          const r = walk(n.children)
          if (r) return r
        }
      }
      return ''
    }
    return walk(detail.chapters || [])
  } catch {
    return ''
  }
}

const showSource = (m, s, idx) => {
  const bid = s.book_id || m.bookId || bookId.value
  if (!bid) {
    ElMessage.warning('请先选择提问的书籍')
    return
  }
  activeSourceIndex.value = messages.value.indexOf(m)
  activeSourceIdx.value = idx
  loadSourcePanel(bid, s.chunk_id)
}

const goFullRead = () => {
  if (!source.value.book_id) return
  router.push('/reader/' + source.value.book_id + '?page=' + (source.value.page_start || 1))
}

const send = async () => {
  const q = question.value.trim()
  if (!q || sending.value) return
  messages.value.push({ role: 'user', content: q })
  const aiMsg = ref({ role: 'assistant', content: '', streaming: true, sources: [], bookId: bookId.value })
  messages.value.push(aiMsg.value)
  question.value = ''
  sending.value = true
  scrollBottom()
  try {
    await chatStream({ book_id: bookId.value || null, question: q }, (event, data) => {
      if (event === 'token') {
        aiMsg.value.content += data.text
        scrollBottom()
      } else if (event === 'done') {
        aiMsg.value.streaming = false
        aiMsg.value.sources = data.sources || []
        scrollBottom()
        loadHistory()
        // 自动在右侧展示第一个出处的原文
        if (data.sources?.length) {
          const s0 = data.sources[0]
          activeSourceIndex.value = messages.value.length - 1
          activeSourceIdx.value = 0
          loadSourcePanel(s0.book_id || aiMsg.value.bookId || bookId.value, s0.chunk_id)
        }
      } else if (event === 'error') {
        aiMsg.value.content = '⚠️ ' + data.message
        aiMsg.value.streaming = false
      }
    })
  } catch (e) {
    aiMsg.value.content = '⚠️ 请求失败：' + e.message
    aiMsg.value.streaming = false
  } finally {
    sending.value = false
  }
}

const loadHistory = async (page = 1, append = false) => {
  historyLoading.value = true
  try {
    const resp = await chatHistory({ page, page_size: 20 })
    history.value = append ? [...history.value, ...resp.items] : resp.items
    historyPage.value = page
    historyTotal.value = resp.total
  } catch { /* ignore */ } finally { historyLoading.value = false }
}

const loadMoreHistory = () => loadHistory(historyPage.value + 1, true)

const viewHistory = async (summary) => {
  let h
  try { h = await getChat(summary.id) } catch (error) { ElMessage.error('历史详情加载失败：' + error.message); return }
  messages.value.push({ role: 'user', content: h.question })
  const msg = { role: 'assistant', content: h.answer, sources: h.sources || [], bookId: bookId.value }
  messages.value.push(msg)
  if (h.sources?.length) {
    activeSourceIndex.value = messages.value.length - 1
    activeSourceIdx.value = 0
    loadSourcePanel(h.sources[0].book_id || bookId.value, h.sources[0].chunk_id)
  }
  scrollBottom()
}

const searchBooks = async (query = '') => {
  booksLoading.value = true
  try {
    const resp = await listBooks({ status: 'ready', q: query.trim() || undefined, page_size: 30 })
    const current = books.value.find(book => book.id === bookId.value)
    books.value = current && !resp.items.some(book => book.id === current.id) ? [current, ...resp.items] : resp.items
  } finally { booksLoading.value = false }
}

const formatTime = (t) => (t || '').replace('T', ' ').slice(5, 16)

onMounted(async () => {
  try {
      await searchBooks('')
      const requestedBook = Number(route.query.bookId)
      if (requestedBook && books.value.some((book) => book.id === requestedBook)) bookId.value = requestedBook
  } catch { /* ignore */ }
  loadHistory()
})
</script>

<style scoped>
.chat-page { height: calc(100vh - 76px); }
.chat-row { height: 100%; }
.chat-row > .el-col { height: 100%; }
.side-card, .chat-card, .source-card { height: 100%; display: flex; flex-direction: column; }
.chat-header { display: flex; justify-content: space-between; align-items: center; }
.msg-box { flex: 1; overflow-y: auto; padding: 8px; }
.msg { margin-bottom: 16px; display: flex; gap: 10px; }
.msg.user { flex-direction: row-reverse; }
.msg-label {
  width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: #fff; background: var(--el-color-primary);
}
.msg.user .msg-label { background: var(--el-color-success); }
.msg-content {
  max-width: 70%; padding: 10px 14px; border-radius: 8px;
  background: var(--el-fill-color-lighter); font-size: 14px; line-height: 1.7; white-space: pre-wrap;
}
.msg.user .msg-content { background: var(--el-color-primary-light-9); }
.streaming::after { content: '▌'; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }
.sources { margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap; }
.source-tag { cursor: pointer; }
.input-row { display: flex; align-items: flex-end; margin-top: 12px; }
.history-title { font-weight: 600; margin-bottom: 8px; color: var(--el-text-color-primary); }
.history-item { padding: 8px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.history-item:hover { background: var(--el-fill-color-lighter); }
.history-q { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-time { font-size: 11px; color: var(--el-text-color-placeholder); }
.history-more{width:100%;min-height:36px}
.source-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.source-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 2; color: var(--el-text-color-primary);
  max-height: 560px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light);
}
.pdf-box { height: 480px; border-radius: 8px; overflow: hidden; border: 1px solid var(--el-border-color-extra-light); }
.side-card,.chat-card,.source-card{border-radius:var(--study-radius-md)}
@media(max-width:1200px){.chat-page{height:auto}.chat-row{display:grid;grid-template-columns:230px minmax(0,1fr);gap:10px}.chat-row:before,.chat-row:after{display:none}.chat-row>.el-col{width:auto;max-width:none;height:620px;padding:0!important}.chat-row>.el-col:last-child{grid-column:1/-1;height:520px}}
@media(max-width:760px){.chat-row{grid-template-columns:1fr}.chat-row>.el-col,.chat-row>.el-col:last-child{grid-column:auto;height:auto;min-height:460px}.chat-row>.el-col:first-child{min-height:240px}.msg-content{max-width:84%}}
</style>
