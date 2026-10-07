<template>
  <div class="chat-page study-page">
    <el-row :gutter="16" class="chat-row">
      <!-- 左：提问范围 + 历史 -->
      <el-col :span="5">
        <el-card shadow="never" class="side-card">
          <template #header>提问范围</template>
          <el-select v-model="scopeValue" placeholder="选择资料范围" filterable :disabled="sending" style="width: 100%">
            <el-option label="全部资料" value="all" />
            <el-option-group label="书架"><el-option v-for="s in shelves" :key="`shelf:${s.id}`" :label="s.name" :value="`shelf:${s.id}`" /></el-option-group>
            <el-option-group label="项目"><el-option v-for="p in projects" :key="`project:${p.id}`" :label="p.name" :value="`project:${p.id}`" /></el-option-group>
            <el-option-group label="单本资料"><el-option v-for="b in books" :key="`book:${b.id}`" :label="b.title" :value="`book:${b.id}`" /></el-option-group>
          </el-select>
          <div class="scope-actions"><el-button link type="primary" @click="openProjectDialog()">＋ 项目</el-button><el-button v-if="activeScopeType==='project'" link @click="openProjectDialog(true)">编辑项目</el-button></div>
          <div v-if="activeScopeType" class="assistant-scope-status">
            <template v-if="assistantStatus">
              <b>{{ assistantStatus.name }}</b>
              <small>全文研读 {{ assistantStatus.prepared }}/{{ assistantStatus.total }} 本；问答始终限定在此范围</small>
              <div v-if="assistantStatus.books.some(b=>b.state==='needs_source'||b.state==='no_text')" class="scope-warning">部分资料尚未解析或没有可读正文</div>
              <div v-if="assistantStatus.books.some(b=>b.state==='stale')" class="scope-warning">资料已变化，可重新研读</div>
              <div v-if="assistantStatus.books.some(b=>b.state==='processing')" class="scope-progress">正在后台研读，可继续提问</div>
              <el-button size="small" plain :loading="preparing" :disabled="!assistantStatus.books.some(b=>['ready_to_prepare','stale','failed'].includes(b.state))" @click="startPrepare">研读范围资料</el-button>
              <el-button size="small" plain @click="openMemoryDialog()">我的记忆（{{ memories.length }}）</el-button>
              <el-button size="small" type="primary" plain @click="archiveDialog=true">项目档案</el-button>
              <el-button v-if="connections.length" size="small" plain type="success" @click="connectionsDialog=true">可能关联（{{ connections.length }}）</el-button>
              <el-popover placement="right" :width="300" trigger="click"><template #reference><el-button size="small" link>逐本状态</el-button></template>
                <div v-for="b in assistantStatus.books" :key="b.book_id" class="scope-book-row"><b>{{ b.title }}</b><span>{{ bookStateLabel(b) }}</span><small v-if="b.coverage?.complete">已处理 {{ b.coverage.processed_chunks }}/{{ b.coverage.total_chunks }} 个文本块</small></div>
                <div v-if="!assistantStatus.books.length">此范围尚无资料。</div>
              </el-popover>
            </template>
          </div>
          <el-divider />
          <div class="history-title">历史记录</div>
          <div v-for="h in history" :key="h.id" class="history-item" role="button" tabindex="0"
            :aria-disabled="sending"
            @click="viewHistory(h)" @keydown.enter.prevent="viewHistory(h)" @keydown.space.prevent="viewHistory(h)">
            <div class="history-q">{{ h.question }}</div>
            <div class="history-time">{{ formatTime(h.created_at) }} · {{ h.model }}</div>
          </div>
          <el-button v-if="history.length < historyTotal" link class="history-more" :loading="historyLoading" @click="loadMoreHistory">加载更早记录</el-button>
        </el-card>
      </el-col>

      <!-- 中：对话 -->
      <el-col :span="10">
        <el-card shadow="never" class="chat-card">
          <template #header>
            <div class="chat-header">
              <span>我的资料助手</span>
              <el-button link size="small" :disabled="sending" @click="newConversation">新对话</el-button>
            </div>
          </template>
          <div ref="msgBox" class="msg-box">
            <StudyEmptyState v-if="!messages.length" compact title="从一个可核验问题开始" description="答案会标注书目、章节与页码，选择引用后可在右侧核对原文。" />
            <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
              <div class="msg-label" :title="m.model || ''">{{ m.role === 'user' ? '我' : (m.qa?.intent === 'clarify' ? '本地' : 'AI') }}</div>
              <div class="msg-content">
                <small v-if="m.role === 'assistant' && m.model" class="msg-model">{{ m.model }}</small>
                <div v-if="m.streaming" class="streaming">{{ m.content }}</div>
                <div v-else v-html="sanitizeHtml(m.content)"></div>
                <el-button v-if="m.role==='user' && activeScopeType" link size="small" @click="rememberMessage(m)">记到我的记忆</el-button>
                <p v-if="m.citationAudit" class="citation-audit" :class="{ 'citation-audit-warn': !m.citationAudit.verified }">
                  {{ citationAuditLabel(m.citationAudit) }}
                </p>
                <p v-if="m.qa" class="qa-line">
                  <el-tag size="small" :type="intentTagType(m.qa.intent)">{{ intentLabel(m.qa.intent) }}</el-tag>
                  <span class="qa-timing">{{ formatTiming(m.qa) }}</span>
                  <span v-if="m.qa.citation_support_rate !== null && m.qa.citation_support_rate !== undefined"
                        class="qa-timing">词面筛查 {{ Math.round(m.qa.citation_support_rate * 100) }}%</span>
                  <span v-if="m.qa.abstained" class="qa-timing">已明确说明资料未覆盖</span>
                </p>
                <el-popover v-if="m.styleAudit?.issue_count" placement="top" :width="340" trigger="click">
                  <template #reference><el-button link size="small">文字规范待复核（{{ m.styleAudit.issue_count }}）</el-button></template>
                  <div class="style-audit-list">
                    <p v-for="(issue, k) in m.styleAudit.issues" :key="k">第 {{ issue.line }} 行：{{ issue.message }}</p>
                    <small>自动检查只标出可能的格式问题；事实和引用仍需对照原文。</small>
                  </div>
                </el-popover>
                <div v-if="m.clarifyOptions?.length" class="clarify-options">
                  <span class="clarify-hint">补充对象后继续：</span>
                  <el-button v-for="(opt, k) in m.clarifyOptions" :key="k" size="small" :disabled="sending" @click="continueClarification(m, opt)">{{ opt }}</el-button>
                </div>
                <div v-if="m.sources?.length" class="sources">
                  <el-tag v-for="(s, j) in m.sources" :key="j" size="small" type="info"
                    :effect="activeSourceIndex === i && activeSourceIdx === j ? 'dark' : 'plain'"
                    class="source-tag" @click="showSource(m, s, j)">
                    {{ (s.book_title ? '《' + s.book_title + '》' : '') + (s.chapter_title ? ' ' + s.chapter_title : '') }} {{ s.page_start && s.page_end && s.page_end !== s.page_start ? '第' + s.page_start + '-' + s.page_end + '页' : '第' + (s.page || s.page_start || '?') + '页' }} 📄
                  </el-tag>
                  <template v-if="activeScopeType && !m.streaming"><el-button v-for="(s, j) in m.sources" :key="`archive-${j}`" link size="small" @click="prepareArchiveEvidence(m, s)">引用 {{ j + 1 }} 加入档案</el-button></template>
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
            <el-button v-if="sending" @click="stopStream" style="margin-left: 8px">停止生成</el-button>
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
    <el-dialog v-model="memoryDialog" title="我的记忆" width="560px">
      <p class="dialog-hint">只有你确认保存的内容会进入此范围的 AI 对话。可随时编辑或删除。</p>
      <el-select v-model="memoryKind" style="width: 130px; margin-right: 8px"><el-option label="我的观点" value="opinion" /><el-option label="长期问题" value="question" /><el-option label="目标" value="goal" /><el-option label="偏好" value="preference" /></el-select>
      <el-input v-model="memoryContent" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="写下希望助手记住的内容" class="memory-input" />
      <el-button type="primary" :disabled="memoryContent.trim().length < 2" :loading="memorySaving" @click="saveMemory">{{ editingMemoryId ? '保存修改' : '确认记住' }}</el-button>
      <el-button v-if="editingMemoryId" @click="resetMemoryForm">取消编辑</el-button>
      <el-divider />
      <div v-if="!memories.length" class="dialog-hint">此范围还没有个人记忆。</div>
      <div v-for="m in memories" :key="m.id" class="memory-item"><div><el-tag size="small">{{ memoryKindLabel(m.kind) }}</el-tag> {{ m.content }}</div><div><el-button link @click="editMemory(m)">编辑</el-button><el-button link type="danger" @click="removeMemory(m)">删除</el-button></div></div>
    </el-dialog>
    <el-drawer v-model="archiveDialog" title="项目理解档案" size="min(850px, 90vw)" destroy-on-close>
      <ResearchArchivePanel v-if="activeScopeType" :scope-type="activeScopeType" :scope-id="activeScopeId" :candidate-evidence="archiveCandidate" @open-source="openArchiveSource" />
    </el-drawer>
    <el-dialog v-model="connectionsDialog" title="资料与我的问题" width="600px">
      <p class="dialog-hint">以下是全文研读结果提示的可能关联。请点击原文核对，AI 尚未判断它是否真正支持你的观点。</p>
      <div v-for="item in connections" :key="`${item.memory_id}:${item.source.chunk_id}`" class="connection-item">
        <b>{{ item.memory }}</b><small>可能关联：《{{ item.source.book_title }}》{{ item.source.page_start ? `第 ${item.source.page_start} 页` : '' }}</small>
        <p>{{ item.source.snippet }}</p><el-button link type="primary" @click="openConnectionSource(item)">核对原文 →</el-button>
      </div>
    </el-dialog>
    <el-dialog v-model="projectDialog" :title="editingProjectId ? '编辑项目' : '新建项目'" width="560px">
      <el-form label-width="88px"><el-form-item label="项目名称"><el-input v-model="projectForm.name" maxlength="120" /></el-form-item>
        <el-form-item label="当前目标"><el-input v-model="projectForm.goal" type="textarea" :rows="3" maxlength="2000" placeholder="这个项目希望完成什么？" /></el-form-item>
        <el-form-item label="引用书架"><el-select v-model="projectForm.shelf_ids" multiple filterable collapse-tags placeholder="选择书架，资料会随书架更新" style="width:100%"><el-option v-for="s in shelves" :key="s.id" :label="s.name" :value="s.id" /></el-select></el-form-item>
        <el-form-item label="单独引用"><el-select v-model="projectForm.book_ids" multiple filterable collapse-tags placeholder="可补充单本资料" style="width:100%"><el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button v-if="editingProjectId" type="danger" plain @click="removeProject">删除项目</el-button><el-button @click="projectDialog=false">取消</el-button><el-button type="primary" :loading="projectSaving" :disabled="!projectForm.name.trim()" @click="saveProject">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { clarCandidates, clarifiedQuestion, citationAuditLabel } from '../utils/chatQa.js'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBooks, listShelves, chatStream, chatHistory, getChat, getChunkOriginal, getBook, bookFileUrl,
  listAssistantProjects, createAssistantProject, updateAssistantProject, deleteAssistantProject,
  getAssistantStatus, getAssistantConnections, estimateAssistantPrepare, prepareAssistant,
  listAssistantMemories, createAssistantMemory, updateAssistantMemory, deleteAssistantMemory } from '../api'
import PdfReader from '../components/PdfReader.vue'
import StudyEmptyState from '../components/StudyEmptyState.vue'
import ResearchArchivePanel from '../components/ResearchArchivePanel.vue'
import { sanitizeHtml } from '../utils/markdown'

const router = useRouter()
const route = useRoute()
const books = ref([])
const booksLoading = ref(false)
const shelves = ref([])
const projects = ref([])
const scopeValue = ref('all')
const conversationId = ref('')
const activeScopeType = computed(() => ['shelf', 'project'].includes(scopeValue.value.split(':')[0]) ? scopeValue.value.split(':')[0] : '')
const activeScopeId = computed(() => activeScopeType.value ? Number(scopeValue.value.split(':')[1]) : null)
const bookId = computed(() => scopeValue.value.startsWith('book:') ? Number(scopeValue.value.split(':')[1]) : null)
const assistantStatus = ref(null)
const preparing = ref(false)
const memories = ref([])
const connections = ref([])
const connectionsDialog = ref(false)
let connectionsKey = ''
const memoryDialog = ref(false)
const archiveDialog = ref(false)
const archiveCandidate = ref(null)
const archiveRouteHandled = ref(false)
const memoryKind = ref('opinion')
const memoryContent = ref('')
const memorySaving = ref(false)
const editingMemoryId = ref(null)
const projectDialog = ref(false)
const editingProjectId = ref(null)
const projectSaving = ref(false)
const projectForm = ref({ name: '', goal: '', shelf_ids: [], book_ids: [] })
let statusPollTimer = null
const question = ref('')
const messages = ref([])
const sending = ref(false)
const history = ref([])
const historyPage = ref(1)
const historyTotal = ref(0)
const historyLoading = ref(false)
const msgBox = ref(null)
// 当前流式请求的取消控制器；停止生成 / 卸载页面时 abort
const streamAbort = ref(null)

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

const loadSourcePanel = async (bookIdVal, chunkId, sourceMeta = null) => {
  if (!bookIdVal || !chunkId) return
  sourceLoading.value = true
  try {
    const data = await getChunkOriginal(bookIdVal, chunkId)
    const book = books.value.find((b) => b.id === bookIdVal)
    const chapter = data.chapter_id ? (await fetchChapterTitle(bookIdVal, data.chapter_id)) : ''
    source.value = {
      book_id: bookIdVal,
      chunk_id: chunkId,
      book_title: book?.title || sourceMeta?.book_title || '',
      chapter_title: chapter || sourceMeta?.chapter_title || '',
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
  loadSourcePanel(bid, s.chunk_id, s)
}

const goFullRead = () => {
  if (!source.value.book_id) return
  router.push('/reader/' + source.value.book_id + '?page=' + (source.value.page_start || 1))
}

const send = async () => {
  const q = question.value.trim()
  if (!q || sending.value) return
  if (activeScopeType.value && assistantStatus.value?.total === 0) {
    ElMessage.warning('当前范围没有资料，请先加入书籍')
    return
  }
  messages.value.push({ role: 'user', content: q })
  const aiMsg = ref({ role: 'assistant', content: '', originalQuestion: q, streaming: true, sources: [], bookId: bookId.value })
  messages.value.push(aiMsg.value)
  question.value = ''
  sending.value = true
  scrollBottom()
  const controller = new AbortController()
  streamAbort.value = controller
  try {
    await chatStream({ book_id: bookId.value || null,
      scope_type: activeScopeType.value || null, scope_id: activeScopeId.value,
      conversation_id: conversationId.value, question: q }, (event, data) => {
      if (event === 'meta') {
        aiMsg.value.model = data.model || ''
        if (data.conversation_id) { conversationId.value = data.conversation_id; saveConversationId() }
      } else if (event === 'token') {
        aiMsg.value.content += data.text
        scrollBottom()
      } else if (event === 'done') {
        aiMsg.value.streaming = false
        aiMsg.value.model = data.model || aiMsg.value.model
        aiMsg.value.sources = data.sources || []
        aiMsg.value.citationAudit = data.citation_audit || null
        aiMsg.value.styleAudit = data.style_audit || null
        aiMsg.value.qa = data.qa || null
        // 澄清轮：把候选对象做成可点击按钮，点一下即以该对象重新提问。
        aiMsg.value.clarifyOptions = data.qa?.intent === 'clarify' ? clarCandidates(data) : []
        scrollBottom()
        loadHistory()
        // 自动在右侧展示第一个出处的原文
        if (data.sources?.length) {
          const s0 = data.sources[0]
          activeSourceIndex.value = messages.value.length - 1
          activeSourceIdx.value = 0
          loadSourcePanel(s0.book_id || aiMsg.value.bookId || bookId.value, s0.chunk_id, s0)
        }
      } else if (event === 'error') {
        aiMsg.value.content += '\n\n⚠️ 生成未完成：' + data.message
        aiMsg.value.streaming = false
      }
    }, { signal: controller.signal })
  } catch (e) {
    if (controller.signal.aborted) {
      // 用户主动停止：保留已生成内容，仅结束流式态
      aiMsg.value.streaming = false
    } else {
      aiMsg.value.content += '\n\n⚠️ 请求失败：' + e.message
      aiMsg.value.streaming = false
    }
  } finally {
    sending.value = false
    streamAbort.value = null
  }
}

const INTENT_LABELS = {
  new_question: '新问题',
  followup: '追问',
  summarize: '摘要',
  clarify: '澄清'
}

const continueClarification = (message, option) => {
  if (sending.value) return
  question.value = clarifiedQuestion(message, option)
  send()
}

const intentLabel = (intent) => INTENT_LABELS[intent] || '问答'

const intentTagType = (intent) => ({
  new_question: 'info', followup: 'primary', summarize: 'success', clarify: 'warning'
}[intent] || 'info')

const formatDuration = (ms) => {
  if (ms === null || ms === undefined) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

const formatTiming = (qa) => {
  const parts = []
  if (qa.ttft_ms != null && qa.intent !== 'clarify') parts.push(`首字 ${formatDuration(qa.ttft_ms)}`)
  if (qa.e2e_ms) parts.push(`总耗时 ${formatDuration(qa.e2e_ms)}`)
  return parts.join(' · ')
}

const stopStream = () => {
  streamAbort.value?.abort()
}

const loadHistory = async (page = 1, append = false) => {
  historyLoading.value = true
  try {
    const params = { page, page_size: 20 }
    if (activeScopeType.value) { params.scope_type = activeScopeType.value; params.scope_id = activeScopeId.value }
    else if (bookId.value) params.book_id = bookId.value
    const resp = await chatHistory(params)
    history.value = append ? [...history.value, ...resp.items] : resp.items
    historyPage.value = page
    historyTotal.value = resp.total
  } catch { /* ignore */ } finally { historyLoading.value = false }
}

const loadMoreHistory = () => loadHistory(historyPage.value + 1, true)

const viewHistory = async (summary) => {
  if (sending.value) return
  let h
  try { h = await getChat(summary.id) } catch (error) { ElMessage.error('历史详情加载失败：' + error.message); return }
  conversationId.value = h.conversation_id || freshConversationId()
  saveConversationId()
  messages.value = [{ role: 'user', content: h.question }]
  const msg = { role: 'assistant', content: h.answer, originalQuestion: h.question, model: h.model, qa: h.qa || null,
    clarifyOptions: h.qa?.intent === 'clarify' ? clarCandidates(h) : [], sources: h.sources || [],
    citationAudit: h.citation_audit || null, styleAudit: h.style_audit || null, bookId: bookId.value }
  messages.value.push(msg)
  if (h.sources?.length) {
    activeSourceIndex.value = messages.value.length - 1
    activeSourceIdx.value = 0
    loadSourcePanel(h.sources[0].book_id || bookId.value, h.sources[0].chunk_id, h.sources[0])
  }
  scrollBottom()
}

const searchBooks = async (query = '') => {
  booksLoading.value = true
  try {
    const resp = await listBooks({ status: 'ready', q: query.trim() || undefined, page_size: 100 })
    const current = books.value.find(book => book.id === bookId.value)
    books.value = current && !resp.items.some(book => book.id === current.id) ? [current, ...resp.items] : resp.items
  } finally { booksLoading.value = false }
}

const formatTime = (t) => (t || '').replace('T', ' ').slice(5, 16)
const freshConversationId = () => crypto.randomUUID().replaceAll('-', '')
const savedConversationMap = () => {
  try { return JSON.parse(localStorage.getItem('assistantConversationIds') || '{}') || {} } catch { return {} }
}

const prepareArchiveEvidence = async (message, citation) => {
  const bid = citation.book_id || message.bookId || bookId.value
  if (!bid || !citation.chunk_id) { ElMessage.warning('这条引用没有可核对的原文定位'); return }
  try {
    const original = await getChunkOriginal(bid, citation.chunk_id)
    const suggested = (citation.quote || citation.snippet || '').trim()
    archiveCandidate.value = { book_id: bid, chunk_id: citation.chunk_id,
      quote: suggested && original.content?.includes(suggested) ? suggested : (original.content || '').slice(0, 180).trim() }
    archiveDialog.value = true
  } catch (e) { ElMessage.error(e.message) }
}
const openArchiveRoute = async () => {
  const bid = Number(route.query.archiveBookId)
  const chunkId = Number(route.query.archiveChunkId)
  if (!bid || !chunkId || !activeScopeType.value || archiveRouteHandled.value) return
  try {
    const status = await getAssistantStatus(activeScopeType.value, activeScopeId.value)
    if (!status.books?.some(book => book.book_id === bid)) {
      ElMessage.warning('请选择包含该文献的书架或项目，再加入档案')
      return
    }
    const original = await getChunkOriginal(bid, chunkId)
    const suggested = String(route.query.archiveQuote || '').trim()
    archiveCandidate.value = { book_id: bid, chunk_id: chunkId,
      quote: suggested && original.content?.includes(suggested) ? suggested : (original.content || '').slice(0, 180).trim(),
      statement: String(route.query.archiveStatement || '') }
    archiveDialog.value = true
    archiveRouteHandled.value = true
  } catch (e) { ElMessage.error(e.message) }
}
const openArchiveSource = evidence => {
  archiveDialog.value = false
  loadSourcePanel(evidence.book_id, evidence.chunk_id, { book_title: evidence.book_title })
  if (evidence.page_start) router.push('/reader/' + evidence.book_id + '?page=' + evidence.page_start)
}
const saveConversationId = () => {
  const map = savedConversationMap()
  map[scopeValue.value] = conversationId.value
  localStorage.setItem('assistantConversationIds', JSON.stringify(map))
}
const restoreConversationId = () => {
  conversationId.value = savedConversationMap()[scopeValue.value] || freshConversationId()
  saveConversationId()
}
const newConversation = () => {
  if (sending.value) return
  conversationId.value = freshConversationId()
  saveConversationId()
  messages.value = []
  source.value = {}
}

const loadAssistantScope = async () => {
  if (!activeScopeType.value) { assistantStatus.value = null; memories.value = []; connections.value = []; connectionsKey = ''; return }
  const type = activeScopeType.value
  const id = activeScopeId.value
  try {
    const [status, ownMemory] = await Promise.all([getAssistantStatus(type, id), listAssistantMemories(type, id)])
    if (activeScopeType.value === type && activeScopeId.value === id) {
      assistantStatus.value = status
      memories.value = ownMemory
      const key = JSON.stringify([type, id, status.books.filter(b => b.state === 'prepared').map(b => b.artifact_id), ownMemory.map(m => [m.id, m.updated_at]), type === 'project' ? Math.floor(Date.now() / 60000) : 0])
      if (key !== connectionsKey) {
        const found = status.prepared ? await getAssistantConnections(type, id) : []
        if (activeScopeType.value === type && activeScopeId.value === id) {
          connections.value = found
          connectionsKey = key
        }
      }
    }
  } catch (e) { ElMessage.error('助手范围加载失败：' + e.message) }
}

const startPrepare = async () => {
  if (!activeScopeType.value || preparing.value) return
  preparing.value = true
  try {
    const estimate = await estimateAssistantPrepare(activeScopeType.value, activeScopeId.value)
    const eligible = estimate.books.filter(item => item.window_count && item.within_budget).slice(0, estimate.batch_limit)
    if (!eligible.length) { ElMessage.warning('没有可研读的资料；请检查解析状态与 AI 任务预算'); return }
    const calls = eligible.reduce((sum, item) => sum + item.estimated_calls, 0)
    const tokens = eligible.reduce((sum, item) => sum + item.estimated_tokens, 0)
    const cost = eligible.some(item => item.estimated_cost_cny !== null)
      ? `按设置中的费率粗估约 ¥${eligible.reduce((sum, item) => sum + (item.estimated_cost_cny || 0), 0).toFixed(2)}。`
      : '未填写该模型费率，无法估算金额。'
    await ElMessageBox.confirm(`本批逐本研读 ${eligible.length} 本资料；模型：${estimate.provider || '未指定'} / ${estimate.model || '未指定'}；预计约 ${calls} 次调用、${tokens.toLocaleString()} Token。${cost}实际以供应商账单为准，每本书仍受任务预算限制。确认开始？`, '全文研读预算', { type: 'warning' })
    const result = await prepareAssistant(activeScopeType.value, activeScopeId.value, eligible.map(item => item.book_id))
    ElMessage.success(`已提交 ${result.jobs.length} 本资料的后台研读任务`)
    await loadAssistantScope()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message) }
  finally { preparing.value = false }
}

const memoryKindLabel = kind => ({ opinion: '我的观点', question: '长期问题', goal: '目标', preference: '偏好' }[kind] || kind)
const openConnectionSource = item => {
  connectionsDialog.value = false
  loadSourcePanel(item.source.book_id, item.source.chunk_id, item.source)
}
const bookStateLabel = book => ({ prepared: '已研读', processing: `研读中 ${Math.round((book.task_progress || 0) * 100)}%`,
  stale: '原文有更新', ready_to_prepare: '待研读', needs_source: '等待解析', no_text: '缺少可读正文', failed: '研读失败' }[book.state] || book.state)
const resetMemoryForm = () => { editingMemoryId.value = null; memoryKind.value = 'opinion'; memoryContent.value = '' }
const openMemoryDialog = () => { resetMemoryForm(); memoryDialog.value = true }
const rememberMessage = message => {
  resetMemoryForm()
  memoryKind.value = /[?？]$/.test(message.content.trim()) ? 'question' : 'opinion'
  memoryContent.value = message.content.slice(0, 2000)
  memoryDialog.value = true
}
const editMemory = memory => { editingMemoryId.value = memory.id; memoryKind.value = memory.kind; memoryContent.value = memory.content }
const saveMemory = async () => {
  if (!activeScopeType.value || memoryContent.value.trim().length < 2) return
  memorySaving.value = true
  try {
    const data = { kind: memoryKind.value, content: memoryContent.value.trim() }
    if (editingMemoryId.value) await updateAssistantMemory(editingMemoryId.value, data)
    else await createAssistantMemory({ ...data, scope_type: activeScopeType.value, scope_id: activeScopeId.value })
    memories.value = await listAssistantMemories(activeScopeType.value, activeScopeId.value)
    connectionsKey = ''
    loadAssistantScope()
    resetMemoryForm()
    ElMessage.success('个人记忆已保存')
  } catch (e) { ElMessage.error(e.message) } finally { memorySaving.value = false }
}
const removeMemory = async memory => {
  try {
    await ElMessageBox.confirm('删除这条个人记忆？', '删除记忆')
    await deleteAssistantMemory(memory.id)
    memories.value = await listAssistantMemories(activeScopeType.value, activeScopeId.value)
    connectionsKey = ''
    loadAssistantScope()
    if (editingMemoryId.value === memory.id) resetMemoryForm()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message) }
}

const openProjectDialog = (edit = false) => {
  const project = edit ? projects.value.find(item => item.id === activeScopeId.value) : null
  editingProjectId.value = project?.id || null
  projectForm.value = project ? { name: project.name, goal: project.goal,
    shelf_ids: [...project.shelf_ids], book_ids: [...project.book_ids] }
    : { name: '', goal: '', shelf_ids: activeScopeType.value === 'shelf' ? [activeScopeId.value] : [], book_ids: [] }
  projectDialog.value = true
}
const saveProject = async () => {
  if (!projectForm.value.name.trim()) return
  projectSaving.value = true
  try {
    const data = { ...projectForm.value, name: projectForm.value.name.trim() }
    const project = editingProjectId.value
      ? await updateAssistantProject(editingProjectId.value, data)
      : await createAssistantProject(data)
    projects.value = await listAssistantProjects()
    projectDialog.value = false
    scopeValue.value = `project:${project.id}`
  } catch (e) { ElMessage.error(e.message) } finally { projectSaving.value = false }
}
const removeProject = async () => {
  try {
    await ElMessageBox.confirm('删除项目及其专属记忆？资料和书架仍会保留。', '删除项目', { type: 'warning' })
    await deleteAssistantProject(editingProjectId.value)
    projects.value = await listAssistantProjects()
    projectDialog.value = false
    scopeValue.value = 'all'
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message) }
}

watch(scopeValue, () => {
  archiveDialog.value = false
  archiveCandidate.value = null
  localStorage.setItem('assistantScope', scopeValue.value)
  restoreConversationId()
  messages.value = []
  source.value = {}
  history.value = []
  connections.value = []
  connectionsKey = ''
  loadHistory()
  loadAssistantScope()
  openArchiveRoute()
  if (route.query.archive === '1' && activeScopeType.value) archiveDialog.value = true
})

onMounted(async () => {
  try {
      await Promise.all([searchBooks(''), listShelves().then(rows => { shelves.value = rows }),
        listAssistantProjects().then(rows => { projects.value = rows })])
      const requestedBook = Number(route.query.bookId)
      const requestedShelf = Number(route.query.shelfId)
      const requestedProject = Number(route.query.projectId)
      if (requestedShelf && shelves.value.some(s => s.id === requestedShelf)) scopeValue.value = `shelf:${requestedShelf}`
      else if (requestedProject && projects.value.some(p => p.id === requestedProject)) scopeValue.value = `project:${requestedProject}`
      else if (requestedBook && books.value.some(b => b.id === requestedBook)) scopeValue.value = `book:${requestedBook}`
      else {
        const saved = localStorage.getItem('assistantScope')
        if (saved && (saved === 'all' || shelves.value.some(s => saved === `shelf:${s.id}`)
          || projects.value.some(p => saved === `project:${p.id}`)
          || books.value.some(b => saved === `book:${b.id}`))) scopeValue.value = saved
      }
  } catch { /* ignore */ }
  if (!conversationId.value) restoreConversationId()
  loadHistory()
  loadAssistantScope()
  openArchiveRoute()
  if (route.query.archive === '1' && activeScopeType.value) archiveDialog.value = true
  if (route.query.archiveChunkId && !activeScopeType.value) ElMessage.info('请选择书架或项目后加入档案')
  statusPollTimer = setInterval(() => { if (activeScopeType.value) loadAssistantScope() }, 15000)
})

watch(() => [route.query.archiveBookId, route.query.archiveChunkId], () => {
  archiveRouteHandled.value = false
  openArchiveRoute()
})

watch(() => [route.query.shelfId, route.query.projectId, route.query.bookId], async ([shelfId, projectId, requestedBook]) => {
  if (Number(shelfId) && shelves.value.some(s => s.id === Number(shelfId))) scopeValue.value = `shelf:${Number(shelfId)}`
  else if (Number(projectId) && projects.value.some(p => p.id === Number(projectId))) scopeValue.value = `project:${Number(projectId)}`
  else if (Number(requestedBook)) {
    const id = Number(requestedBook)
    if (!books.value.some(b => b.id === id)) {
      try { books.value.unshift(await getBook(id)) } catch { return }
    }
    scopeValue.value = `book:${id}`
  }
})

onBeforeUnmount(() => { streamAbort.value?.abort(); clearInterval(statusPollTimer) })
</script>

<style scoped>
.chat-page { height: calc(100vh - 76px); }
.chat-row { height: 100%; }
.chat-row > .el-col { height: 100%; }
.side-card, .chat-card, .source-card { height: 100%; display: flex; flex-direction: column; }
.chat-card :deep(.el-card__body) { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.side-card :deep(.el-card__body), .source-card :deep(.el-card__body) { flex: 1; min-height: 0; overflow-y: auto; }
.chat-card :deep(.el-card__header) { flex-shrink: 0; }
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
.msg-label { flex-shrink: 0; }
.msg-content { min-width: 0; overflow-wrap: anywhere; }
.msg-model { display: block; color: var(--study-text-secondary); font-size: 11px; line-height: 1.5; margin-bottom: 4px; }
.input-row { flex-shrink: 0; }
.msg.user .msg-content { background: var(--el-color-primary-light-9); }
.streaming::after { content: '▌'; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }
.sources { margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap; }
.citation-audit { margin-top: 8px; color: var(--study-text-secondary); font-size: 12px; line-height: 1.5; }
.citation-audit-warn { color: var(--el-color-warning-dark-2); }
.qa-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 6px 0 0; }
.qa-timing { color: var(--study-text-secondary); font-size: 12px; }
.clarify-options { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.clarify-options .el-button { max-width: 100%; height: auto; white-space: normal; padding: 7px 10px; line-height: 1.5; margin-left: 0; }
.clarify-hint { color: var(--study-text-secondary); font-size: 12px; }
.source-tag { cursor: pointer; }
.style-audit-list { max-height: 260px; overflow: auto; line-height: 1.5; }
.style-audit-list p { margin: 0 0 8px; }
.style-audit-list small { color: var(--el-text-color-secondary); }
.input-row { display: flex; align-items: flex-end; margin-top: 12px; }
.history-title { font-weight: 600; margin-bottom: 8px; color: var(--el-text-color-primary); }
.history-item { padding: 8px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.history-item:hover { background: var(--el-fill-color-lighter); }
.history-item:focus-visible { outline: 2px solid var(--study-ink-soft, #B98A58); outline-offset: 1px; }
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
.scope-actions{display:flex;gap:8px;margin-top:5px}.assistant-scope-status{display:flex;flex-direction:column;gap:8px;margin-top:12px;padding:10px;border-radius:8px;background:var(--el-fill-color-light)}
.assistant-scope-status small,.dialog-hint{color:var(--el-text-color-secondary);line-height:1.5}.scope-warning{font-size:12px;color:var(--el-color-warning-dark-2)}.scope-progress{font-size:12px;color:var(--el-color-primary)}
.memory-input{margin:10px 0}.memory-item{padding:10px 0;border-bottom:1px solid var(--el-border-color-lighter);line-height:1.6;overflow-wrap:anywhere}.memory-item>div:last-child{text-align:right}
.scope-book-row{display:flex;flex-direction:column;padding:7px 0;border-bottom:1px solid var(--el-border-color-lighter)}.scope-book-row b{font-size:12px}.scope-book-row span,.scope-book-row small{color:var(--el-text-color-secondary);font-size:11px}
.connection-item{padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}.connection-item b,.connection-item small{display:block}.connection-item small{color:var(--el-text-color-secondary);margin-top:4px}.connection-item p{max-height:85px;overflow:auto;font-size:13px;line-height:1.6}
@media(max-width:1200px){.chat-page{height:auto}.chat-row{display:grid;grid-template-columns:230px minmax(0,1fr);gap:10px}.chat-row:before,.chat-row:after{display:none}.chat-row>.el-col{width:auto;max-width:none;height:620px;padding:0!important}.chat-row>.el-col:last-child{grid-column:1/-1;height:520px}}
@media(max-width:760px){.chat-row{grid-template-columns:1fr}.chat-row>.el-col,.chat-row>.el-col:last-child{grid-column:auto;height:620px;min-height:460px}.chat-row>.el-col:first-child{height:360px;min-height:240px}.msg-content{max-width:84%}}
</style>
