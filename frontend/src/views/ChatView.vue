<template>
  <div class="chat-page">
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>提问范围</template>
          <el-select v-model="bookId" placeholder="全部书籍" clearable style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
          <el-divider />
          <div class="history-title">历史记录</div>
          <div v-for="h in history" :key="h.id" class="history-item" @click="viewHistory(h)">
            <div class="history-q">{{ h.question }}</div>
            <div class="history-time">{{ formatTime(h.created_at) }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="18">
        <el-card shadow="never" class="chat-card">
          <div ref="msgBox" class="msg-box">
            <div v-if="!messages.length" class="empty-tip">
              💡 对着教材提问，答案会标注出处页码。<br />
              试试：「解释一下拉格朗日中值定理的几何意义」
            </div>
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="msg-label">{{ m.role === 'user' ? '我' : 'AI' }}</div>
              <div class="msg-content">
                <div v-if="m.streaming" class="streaming" v-html="m.content" />
                <div v-else>{{ m.content }}</div>
                <div v-if="m.sources?.length" class="sources">
                  <el-tag v-for="(s, j) in m.sources" :key="j" size="small" type="info">
                    第 {{ s.page || '?' }} 页
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
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { listBooks, chatStream, chatHistory } from '../api'

const books = ref([])
const bookId = ref(null)
const question = ref('')
const messages = ref([])
const sending = ref(false)
const history = ref([])
const msgBox = ref(null)

const scrollBottom = () => {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

const send = async () => {
  const q = question.value.trim()
  if (!q || sending.value) return
  messages.value.push({ role: 'user', content: q })
  const aiMsg = ref({ role: 'assistant', content: '', streaming: true, sources: [] })
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
  messages.value.push({ role: 'assistant', content: h.answer, sources: h.sources || [] })
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
.chat-card { height: 100%; display: flex; flex-direction: column; }
.msg-box { flex: 1; overflow-y: auto; padding: 8px; }
.empty-tip { color: #909399; text-align: center; margin-top: 60px; line-height: 2; }
.msg { margin-bottom: 16px; display: flex; gap: 10px; }
.msg.user { flex-direction: row-reverse; }
.msg-label {
  width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: #fff; background: #409eff;
}
.msg.user .msg-label { background: #67c23a; }
.msg-content {
  max-width: 70%; padding: 10px 14px; border-radius: 8px;
  background: #f5f7fa; font-size: 14px; line-height: 1.7; white-space: pre-wrap;
}
.msg.user .msg-content { background: #ecf5ff; }
.streaming::after { content: '▌'; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }
.sources { margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap; }
.input-row { display: flex; align-items: flex-end; margin-top: 12px; }
.history-title { font-weight: 600; margin-bottom: 8px; color: #303133; }
.history-item { padding: 8px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.history-item:hover { background: #f5f7fa; }
.history-q { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-time { font-size: 11px; color: #c0c4cc; }
</style>
