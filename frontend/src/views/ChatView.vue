<template>
  <div class="chat-page">
    <el-row :gutter="16" class="chat-row">
      <!-- 左：提问范围 + 历史 -->
      <el-col :span="5">
        <el-card shadow="never" class="side-card">
          <template #header>提问范围</template>
          <el-select v-model="bookId" placeholder="全部书籍" clearable style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
          <el-divider />
          <div class="history-title">历史记录</div>
          <div v-for="h in history" :key="h.id" class="history-item" @click="viewHistory(h)">
            <div class="history-q">{{ h.question }}</div>
            <div class="history-time">{{ formatTime(h.created_at) }} · {{ h.model }}</div>
          </div>
        </el-card>
      </el-col>

      <!-- 中：对话 -->
      <el-col :span="12">
        <el-card shadow="never" class="chat-card">
          <template #header>
            <div class="chat-header">
              <span>AI 问答（DeepSeek 云端）</span>
              <el-radio-group v-model="model" size="small">
                <el-radio-button value="flash">⚡ flash</el-radio-button>
                <el-radio-button value="pro">🧠 pro</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div ref="msgBox" class="msg-box">
            <div v-if="!messages.length" class="empty-tip">
              💡 对着教材提问，答案会标注出处页码，原文自动显示在右侧。<br />
              试试：「解释一下拉格朗日中值定理的几何意义」
            </div>
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="msg-label">{{ m.role === 'user' ? '我' : 'AI' }}</div>
              <div class="msg-content">
                <div v-if="m.streaming" class="streaming" v-html="m.content" />
                <div v-else>{{ m.content }}</div>
                <div v-if="m.sources?.length" class="sources">
                  <el-tag v-for="(s, j) in m.sources" :key="j" size="small" type="info"
                    :effect="activeSourceIndex === i && activeSourceIdx === j ? 'dark' : 'plain'"
                    class="source-tag" @click="showSource(m, s, j)">
                    第 {{ s.page || '?' }} 页 📄
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
      <el-col :span="7">
        <el-card shadow="never" class="source-card">
          <template #header>📖 出处原文</template>
          <div v-if="sourceLoading" v-loading="true" style="height: 160px" />
          <template v-else-if="source.text">
            <div class="source-meta">
              <el-tag size="small" type="info">《{{ source.book_title || '' }}》</el-tag>
              <el-tag size="small" type="warning" v-if="source.chapter_title">{{ source.chapter_title }}</el-tag>
              <el-tag size="small" type="success">第 {{ source.page_start }} - {{ source.page_end }} 页</el-tag>
              <el-radio-group v-model="sourceView" size="small" style="margin-left: auto">
                <el-radio-button value="text">文本</el-radio-button>
                <el-radio-button value="pdf" v-if="sourceBookType === 'pdf'">PDF 原文</el-radio-button>
              </el-radio-group>
            </div>
            <div v-if="sourceView === 'text'" class="source-text">{{ source.text }}</div>
            <div v-else-if="sourceView === 'pdf'" class="pdf-box">
              <PdfReader :src="pdfUrl" :initial-page="source.page_start || 1" />
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
import { ElMessage } from 'element-plus'
import { listBooks, chatStream, chatHistory, getChunkOriginal, getBook, bookFileUrl } from '../api'
import PdfReader from '../components/PdfReader.vue'

const books = ref([])
const bookId = ref(null)
const model = ref('flash')
const question = ref('')
const messages = ref([])
const sending = ref(false)
const history = ref([])
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
    await chatStream({ book_id: bookId.value || null, question: q, model: model.value }, (event, data) => {
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

const loadHistory = async () => {
  try {
    const resp = await chatHistory({ page_size: 20 })
    history.value = resp.items
  } catch { /* ignore */ }
}

const viewHistory = (h) => {
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

const formatTime = (t) => (t || '').replace('T', ' ').slice(5, 16)

onMounted(async () => {
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items.filter((b) => b.status === 'ready')
  } catch { /* ignore */ }
  loadHistory()
})
</script>

<style scoped>
.chat-page { height: calc(100vh - 120px); }
.chat-row { height: 100%; }
.chat-row > .el-col { height: 100%; }
.side-card, .chat-card, .source-card { height: 100%; display: flex; flex-direction: column; }
.chat-header { display: flex; justify-content: space-between; align-items: center; }
.msg-box { flex: 1; overflow-y: auto; padding: 8px; }
.empty-tip { color: var(--el-text-color-secondary); text-align: center; margin-top: 60px; line-height: 2; }
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
.source-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.source-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 12px;
  font-size: 13px; line-height: 1.9; color: var(--el-text-color-primary);
  max-height: 400px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light);
}
.pdf-box { height: 480px; border-radius: 8px; overflow: hidden; border: 1px solid var(--el-border-color-extra-light); }
</style>