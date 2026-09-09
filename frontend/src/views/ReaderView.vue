<template>
  <div class="reader-page" :class="{ 'focus-reading': focusMode }">
    <header class="reader-top">
      <div class="reader-primary-row">
        <div class="reader-identity">
          <el-button circle plain aria-label="返回文献知识库" @click="$router.push('/library')">←</el-button>
          <div class="reader-heading">
            <div class="reader-title" :title="book?.title || '阅读器'">{{ book?.title || '阅读器' }}</div>
            <div class="reader-meta">
              {{ [book?.archive?.authors, book?.archive?.journal, book?.archive?.published_year].filter(Boolean).join(' · ') || '本地文献' }}
            </div>
          </div>
        </div>
        <div v-if="book" class="reader-actions">
          <el-button size="small" @click="askKnowledgeBase">围绕本文提问</el-button>
          <el-button size="small" type="primary" plain @click="sendToDeck">选段生成汇报</el-button>
          <el-tooltip content="专注阅读"><el-button size="small" circle aria-label="切换专注阅读" @click="focusMode = !focusMode">{{ focusMode ? '↙' : '⛶' }}</el-button></el-tooltip>
        </div>
      </div>
      <div class="reader-secondary-row">
        <el-radio-group v-model="mode" size="small" aria-label="阅读视图" @change="onModeChange">
          <el-radio-button value="source">原版阅读</el-radio-button>
          <el-radio-button value="deep">结构精读</el-radio-button>
          <el-radio-button value="card">证据卡片</el-radio-button>
        </el-radio-group>
        <div v-if="book" class="reader-status-actions">
          <el-tag size="small" type="info">{{ sourceMap.locator_mode === 'page-grounded' ? '页码可回溯' : '结构定位' }}</el-tag>
          <el-select v-model="readingStatus" size="small" aria-label="阅读状态" style="width: 96px" @change="saveReadingStatus">
            <el-option label="未读" value="unread" />
            <el-option label="阅读中" value="reading" />
            <el-option label="已读完" value="read" />
          </el-select>
          <el-button class="toc-review-btn" size="small" @click="openTocEditor">校正目录</el-button>
          <el-button size="small" :type="book.archive?.favorite ? 'warning' : ''" plain @click="toggleFavorite">
            {{ book.archive?.favorite ? '★ 已收藏' : '☆ 收藏' }}
          </el-button>
        </div>
      </div>
    </header>

    <section v-if="deepTaskMessage" class="deep-task-monitor" aria-live="polite">
      <span>{{ deepTaskMessage }}</span>
      <el-progress v-if="runningDeep" :percentage="deepTaskProgress" :stroke-width="6" />
      <el-button v-if="runningDeep && deepTaskId" size="small" @click="stopDeep">取消研读</el-button>
      <el-button v-else size="small" @click="runDeep">重新研读</el-button>
    </section>
    <main class="reader-body" v-loading="!book">
      <template v-if="book && mode === 'source'">
        <DocReader v-if="book.file_type !== 'pdf'" :book-id="book.id" />
        <PdfReader v-else :key="pdfReaderKey" :src="fileUrl" :book-id="book.id" :initial-page="initialPage"
          :toc="tocFlat" show-toc show-ai :use-saved-pos="!hasQueryPage" @page-change="onPageChange"
          @request-toc-review="openTocEditor" />
      </template>

      <section v-else-if="book" class="artifact-view">
        <aside class="artifact-aside">
          <div class="aside-label">{{ mode === 'card' ? 'PAPER CARD' : 'DOCUMENT MAP' }}</div>
          <template v-if="mode === 'deep'">
            <button v-for="(item, i) in deepData.toc || []" :key="i" class="toc-link"
              :style="{ paddingLeft: 12 + (item.level - 1) * 13 + 'px' }" @click="jumpArtifact(i)">
              <span>{{ item.title }}</span><small>p.{{ item.page }}</small>
            </button>
          </template>
          <template v-else>
            <button v-for="i in 16" :key="i" class="toc-link" @click="jumpArtifact(i - 1)">
              <span>{{ String(i).padStart(2, '0') }}</span>
            </button>
          </template>
          <div class="source-summary">
            <strong>{{ sourceMap.blocks?.length || 0 }}</strong>
            <span>个来源块已建立映射</span>
          </div>
        </aside>
        <article ref="artifactContent" class="artifact-content markdown-body" :style="artifactStyle">
          <div class="artifact-banner">
            <div>
              <span>{{ mode === 'card' ? '证据约束的深度阅读' : '校正断句与标题层级' }}</span>
              <small v-if="deepData.updated_at">更新于 {{ formatDate(deepData.updated_at) }}</small>
            </div>
            <el-tag v-if="mode === 'card' && deepData.card_audit"
              :type="deepData.card_audit.ok ? 'success' : 'warning'" size="small">
              {{ deepData.card_audit.ok ? '来源审计通过' : '需要复核' }}
            </el-tag>
            <div class="type-controls">
              <button @click="fontSize = Math.max(14, fontSize - 1)">A−</button><span>{{ fontSize }}</span><button @click="fontSize = Math.min(22, fontSize + 1)">A＋</button>
              <button @click="wideText = !wideText">{{ wideText ? '窄栏' : '宽栏' }}</button>
            </div>
          </div>
          <div v-if="artifactText" v-html="renderMarkdown(artifactText)"></div>
          <div v-else-if="mdLoading" v-loading="true" class="artifact-loading" />
          <el-empty v-else description="尚未生成结构化阅读材料">
            <el-button type="primary" :loading="runningDeep" @click="runDeep">开始深度分析</el-button>
            <p class="privacy-tip">将目录候选与相关正文发送至研究功能当前使用的模型，用于结构补缺与来源约束分析。</p>
          </el-empty>
        </article>
      </section>
    </main>

    <el-dialog v-model="tocEditorOpen" title="目录结构工作台" width="min(1480px, 96vw)" append-to-body destroy-on-close>
      <div v-loading="tocEditorLoading || tocRebuilding" :element-loading-text="tocRebuildMessage" class="toc-editor">
        <div class="toc-audit-bar">
          <div>
            <strong>{{ tocAudit.summary?.total || 0 }} 项目录</strong>
            <span>高置信 {{ tocAudit.summary?.high || 0 }} · 需复核 {{ tocAudit.summary?.review || 0 }} · 低置信 {{ tocAudit.summary?.low || 0 }}</span>
          </div>
          <div class="toc-audit-actions">
            <el-button v-if="book?.file_type === 'pdf'" :disabled="tocSaving" @click="rebuildCurrentToc">从已解析数据重识别</el-button>
            <el-tag :type="tocAudit.ok ? 'success' : 'warning'">
              {{ tocAudit.ok ? '编号链通过' : `${tocAudit.summary?.unresolved || 0} 项需人工判断` }}
            </el-tag>
            <el-button :disabled="!tocAudit.summary?.safe_repairs || !!tocAudit.summary?.unresolved" @click="applySafeTocRepair">
              安全自修正 {{ tocAudit.summary?.safe_repairs || 0 }} 项
            </el-button>
          </div>
        </div>
        <div class="toc-workspace-actions">
          <span>选择左侧节点，在右侧修改；中间始终显示对应原页。拖动 ⋮⋮ 可调整顺序或层级；AI 建议不会自动覆盖人工目录。</span>
          <el-checkbox v-model="tocReviewOnly">仅看待复核</el-checkbox>
          <el-button size="small" :disabled="!tocIssueItems.length" @click="selectNextTocIssue">下一处问题</el-button>
          <el-button size="small" :disabled="!tocHistory.length" @click="undoToc">撤销</el-button>
        </div>
        <div v-if="tocAudit.missing_candidates?.length" class="toc-missing-suggestions">
          <b>疑似缺项 {{ tocAudit.missing_candidates.length }} 组</b>
          <span>系统只给出编号与页区间，不编造标题；添加后请对照中间原页补全。</span>
          <el-button v-for="(candidate,index) in tocAudit.missing_candidates.slice(0,8)" :key="index" size="small" plain @click="insertMissingCandidate(candidate)">
            添加 {{ missingCandidateLabel(candidate) }} · 第 {{ candidate.page_range?.join('–') }} 页
          </el-button>
        </div>
        <div v-if="tocAudit.academic_structure?.classified" class="toc-academic-audit">
          <div><b>{{ tocAudit.academic_structure.profile_label }}</b><span>已识别：{{ tocAudit.academic_structure.detected_role_labels?.join('、') }}</span></div>
          <div v-if="tocAudit.academic_structure.missing_core_roles?.length"><small>尚未识别，请对照原页核对</small><el-tag v-for="role in tocAudit.academic_structure.missing_core_roles" :key="role" size="small" type="warning" effect="plain">{{ role }}</el-tag></div>
          <el-tag v-else size="small" type="success" effect="plain">核心结构已覆盖</el-tag>
        </div>
        <div class="toc-workspace">
          <section class="toc-tree-panel">
            <div class="toc-panel-title">目录树 <small>{{ visibleTocTreeCount }} 项</small></div>
            <el-tree-v2 :data="tocVisibleTree" :props="tocTreeProps" :height="520" :item-size="36"
              :current-node-key="selectedTocKey" highlight-current @node-click="selectTocNode">
              <template #default="{ data }">
                <div class="toc-tree-node" :class="[`status-${data.review_status}`, {
                  'is-dragging': tocDrag?.key === data.client_key,
                  'drop-before': tocDrop?.key === data.client_key && tocDrop.position === 'before',
                  'drop-inside': tocDrop?.key === data.client_key && tocDrop.position === 'inside',
                  'drop-after': tocDrop?.key === data.client_key && tocDrop.position === 'after',
                }]"
                  :draggable="!tocReviewOnly"
                  :aria-label="`拖拽调整目录：${data.title}`"
                  @dragstart.stop="startTocDrag(data, $event)"
                  @dragover.prevent.stop="overTocDrag(data, $event)"
                  @drop.prevent.stop="dropTocDrag(data, $event)"
                  @dragend="endTocDrag">
                  <span class="toc-drag-handle" aria-hidden="true">⋮⋮</span>
                  <span class="toc-node-title">{{ data.title }}</span>
                  <span class="toc-node-page">{{ data.start_page }}</span>
                  <span v-if="data.edited" class="toc-node-edited">改</span>
                </div>
              </template>
            </el-tree-v2>
          </section>
          <section class="toc-page-panel">
            <div class="toc-panel-title">原页核对 <small v-if="selectedToc">第 {{ selectedToc.start_page }} 页</small></div>
            <iframe v-if="selectedToc" :key="tocPreviewUrl" class="toc-page-frame" :src="tocPreviewUrl" title="目录原页预览" />
            <el-empty v-else description="请从左侧选择目录节点" :image-size="70" />
          </section>
          <section class="toc-inspector-panel">
            <div class="toc-panel-title">节点检查器</div>
            <template v-if="selectedToc">
              <div class="toc-breadcrumb">{{ selectedTocBreadcrumb }}</div>
              <el-alert v-if="selectedToc.issueText" type="warning" :closable="false" :title="selectedToc.issueText" />
              <el-form label-position="top" class="toc-inspector-form">
                <el-form-item label="目录标题"><el-input v-model="selectedToc.title" type="textarea" :rows="2" @focus="checkpointToc" @input="selectedToc.edited = true" /></el-form-item>
                <div class="toc-inspector-fields">
                  <el-form-item label="层级"><el-input-number v-model="selectedToc.level" :min="1" :max="4" controls-position="right" @focus="checkpointToc" @change="changeSelectedLevel" /></el-form-item>
                  <el-form-item label="页码"><el-input-number v-model="selectedToc.start_page" :min="1" :max="book?.total_pages || 99999" controls-position="right" @focus="checkpointToc" @change="selectedToc.edited = true" /></el-form-item>
                </div>
              </el-form>
              <div class="toc-structure-actions">
                <el-button @click="moveSelectedToc(-1)">上移整组</el-button><el-button @click="moveSelectedToc(1)">下移整组</el-button>
                <el-button @click="shiftSelectedToc(1)">降为子级</el-button><el-button @click="shiftSelectedToc(-1)">提升一级</el-button>
                <el-button @click="addSelectedToc">新增同级</el-button><el-button type="danger" plain :disabled="tocDraft.length === 1" @click="removeSelectedToc">删除</el-button>
              </div>
              <el-button class="toc-open-reader" plain @click="inspectTocPage(selectedToc)">在完整阅读器打开此页</el-button>
            </template>
            <el-empty v-else description="尚未选择节点" :image-size="70" />
          </section>
        </div>
        <div v-if="tocRevisions.length" class="toc-revisions">
          <span>最近修订</span>
          <el-button v-for="revision in tocRevisions.slice(0, 4)" :key="revision.id" size="small" text @click="restoreRevision(revision)">
            {{ revision.source === 'auto' ? '自修正' : '人工' }} {{ formatDate(revision.created_at) }} · 恢复前一版
          </el-button>
        </div>
      </div>
      <template #footer>
        <el-button @click="tocEditorOpen = false">取消</el-button>
        <el-button type="primary" :loading="tocSaving" @click="saveTocEditor">保存并重建来源映射</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PdfReader from '../components/PdfReader.vue'
import DocReader from '../components/DocReader.vue'
import {
  getBook, bookFileUrl, renderedBookFileUrl, getBookDeep, deepAnalyze,
  getSourceMap, updateArchiveProfile,
  getTocReview, autoRepairToc, replaceBookToc, listTocRevisions, restoreTocRevision,
  rebuildBookToc, subscribeTask, cancelTask,
} from '../api'
import { renderMarkdown } from '../utils/markdown'
import { notifyTaskSubmitted } from '../stores/taskCenter'

const route = useRoute()
const router = useRouter()
const book = ref(null)
const tocFlat = ref([])
const sourceMap = ref({ locator_mode: 'unavailable', blocks: [] })
const hasQueryPage = ref(route.query.page != null)
const initialPage = ref(parseInt(route.query.page) || 1)
// key 必须绑定 book.id：切换书籍时强制重建 PdfReader。
// 只靠 ref 递增时，切书不会重建，画布会停留在上一本，标注与阅读进度也会写到错误的 bookId。
const pdfReloadSeq = ref(0)
const pdfReaderKey = computed(() => `${book.value?.id ?? 'none'}-${pdfReloadSeq.value}`)
const mode = ref(route.query.mode === 'card' ? 'card' : route.query.mode === 'deep' ? 'deep' : 'source')
const readingStatus = ref('unread')
const deepData = ref({ toc: [], markdown: '', paper_card: '' })
const mdLoading = ref(false)
const runningDeep = ref(false)
const deepTaskMessage = ref('')
const deepTaskProgress = ref(0)
const deepTaskId = ref('')
let deepAbort = null
const artifactContent = ref(null)
const focusMode = ref(false)
const fontSize = ref(Number(localStorage.getItem('readerFontSize')) || 16)
const wideText = ref(localStorage.getItem('readerWideText') === 'true')
let progressTimer = null
// 离开页面后必须停止 SSE/轮询，否则会占满浏览器每域 6 条连接上限。
const rebuildAbort = new AbortController()
const tocEditorOpen = ref(false)
const tocEditorLoading = ref(false)
const tocSaving = ref(false)
const tocRebuilding = ref(false), tocRebuildMessage = ref('正在读取目录数据…')
const tocAudit = ref({ items: [], issues: [], summary: {} })
const tocDraft = ref([])
const tocRevisions = ref([])
const selectedTocKey = ref('')
const tocReviewOnly = ref(false)
const tocHistory = ref([])
const tocDrag = ref(null)
const tocDrop = ref(null)
const tocLevelBefore = ref(null)
let tocTempId = 0

const fileUrl = computed(() => book.value ? bookFileUrl(book.value.id) : '')
const tocTreeProps = { children: 'children', label: 'title', value: 'client_key' }
const selectedToc = computed(() => tocDraft.value.find(item => item.client_key === selectedTocKey.value) || null)
const tocIssueItems = computed(() => tocDraft.value.filter(item => item.review_status !== 'high' || item.issueText))
const makeTocTree = (items) => {
  const roots = []; const stack = []
  for (const item of items) {
    const node = { ...item, children: [] }
    while (stack.length >= node.level) stack.pop()
    if (node.level > 1 && stack[node.level - 2]) stack[node.level - 2].children.push(node)
    else roots.push(node)
    stack[node.level - 1] = node
  }
  return roots
}
const tocVisibleTree = computed(() => makeTocTree(tocReviewOnly.value ? tocIssueItems.value : tocDraft.value))
const visibleTocTreeCount = computed(() => tocReviewOnly.value ? tocIssueItems.value.length : tocDraft.value.length)
const selectedTocBreadcrumb = computed(() => {
  if (!selectedToc.value) return ''
  const index = tocDraft.value.findIndex(item => item.client_key === selectedToc.value.client_key)
  const parents = []
  let targetLevel = selectedToc.value.level - 1
  for (let i = index - 1; i >= 0 && targetLevel > 0; i--) {
    if (tocDraft.value[i].level === targetLevel) { parents.unshift(tocDraft.value[i].title); targetLevel-- }
  }
  return [...parents, selectedToc.value.title].join('  /  ')
})
const tocPreviewUrl = computed(() => {
  if (!book.value || !selectedToc.value) return ''
  const base = book.value.file_type === 'pdf' ? bookFileUrl(book.value.id) : renderedBookFileUrl(book.value.id)
  return `${base}#page=${selectedToc.value.start_page}&zoom=page-width`
})
const artifactText = computed(() => mode.value === 'card' ? deepData.value.paper_card : deepData.value.markdown)
const artifactStyle = computed(() => ({ '--reading-font-size': `${fontSize.value}px`, '--reading-max-width': wideText.value ? '1080px' : '820px' }))

watch(fontSize, v => localStorage.setItem('readerFontSize', String(v)))
watch(wideText, v => localStorage.setItem('readerWideText', String(v)))

const sendToDeck = () => {
  const selected = window.getSelection()?.toString().trim() || ''
  if (selected) sessionStorage.setItem('deckSelectedText', selected.slice(0, 20000))
  router.push({ path: '/literature-workbench', query: { bookId: book.value.id } })
  if (!selected) ElMessage.info('已带入当前文献；可在工作台选择章节或粘贴选段')
}
const askKnowledgeBase = () => router.push({ path: '/chat', query: { bookId: book.value.id } })

const draftItemsFromAudit = (audit) => (audit?.items || []).map(item => ({
  client_key: `id:${item.id}`, id: item.id, title: item.title,
  level: item.declared_level ?? item.level ?? 1, start_page: item.page ?? item.start_page ?? 1,
  review_status: item.review_status,
  issueText: item.issues?.map(issue => issue.message).join('；') || '', edited: false,
}))
const setTocDraftFromAudit = (audit, preserveSelection = true) => {
  const previousKey = preserveSelection ? selectedTocKey.value : ''
  tocLevelBefore.value = null
  tocAudit.value = audit || { items: [], issues: [], summary: {} }
  tocDraft.value = draftItemsFromAudit(tocAudit.value)
  if (!tocDraft.value.some(item => item.client_key === previousKey)) {
    selectedTocKey.value = tocIssueItems.value[0]?.client_key || tocDraft.value[0]?.client_key || ''
  } else {
    selectedTocKey.value = previousKey
  }
}
const flattenChapters = (chapters) => {
  const flat = []
  const walk = (nodes, level) => {
    for (const node of nodes || []) {
      flat.push({ id: node.id, title: node.title, level: node.level || level, start_page: node.start_page })
      if (node.children?.length) walk(node.children, level + 1)
    }
  }
  walk(chapters, 1)
  return flat
}
const applyTocResultInPlace = async (result) => {
  if (result?.chapters && book.value) {
    book.value.chapters = result.chapters
    tocFlat.value = flattenChapters(result.chapters)
  }
  if (result?.audit) setTocDraftFromAudit(result.audit)
  tocRevisions.value = await listTocRevisions(book.value.id)
  tocHistory.value = []
}
const loadTocEditor = async () => {
  tocEditorLoading.value = true
  try {
    const [audit, revisions] = await Promise.all([getTocReview(book.value.id), listTocRevisions(book.value.id)])
    tocRevisions.value = revisions
    setTocDraftFromAudit(audit)
    tocHistory.value = []
  } catch (e) { ElMessage.error(e.message) }
  finally { tocEditorLoading.value = false }
}
const openTocEditor = async () => { tocEditorOpen.value = true; await loadTocEditor() }

const selectTocNode = (data) => { selectedTocKey.value = data.client_key }
const checkpointToc = () => {
  tocLevelBefore.value = selectedToc.value?.level ?? null
  const snapshot = JSON.stringify(tocDraft.value)
  if (tocHistory.value.at(-1) !== snapshot) {
    tocHistory.value.push(snapshot)
    if (tocHistory.value.length > 20) tocHistory.value.shift()
  }
}
const undoToc = () => {
  const snapshot = tocHistory.value.pop()
  if (!snapshot) return
  tocDraft.value = JSON.parse(snapshot)
  tocLevelBefore.value = null
}
const selectNextTocIssue = () => {
  if (!tocIssueItems.value.length) return
  const current = tocIssueItems.value.findIndex(item => item.client_key === selectedTocKey.value)
  selectedTocKey.value = tocIssueItems.value[(current + 1) % tocIssueItems.value.length].client_key
}

const normalizeAllTocLevels = () => {
  const stack = []
  for (let i = 0; i < tocDraft.value.length; i++) {
    const requested = Math.max(1, Math.min(4, Number(tocDraft.value[i].level) || 1))
    while (stack.length >= requested) stack.pop()
    const level = Math.min(requested, stack.length + 1)
    tocDraft.value[i].level = level
    stack[level - 1] = tocDraft.value[i]
    stack.length = level
  }
}
const normalizeTocLevels = (index, requestedLevel = tocDraft.value[index]?.level, originalLevel = tocLevelBefore.value) => {
  const item = tocDraft.value[index]
  if (!item) return
  const baseLevel = Number(originalLevel) || item.level
  const maxLevel = index ? Math.min(4, tocDraft.value[index - 1].level + 1) : 1
  const requested = Math.max(1, Math.min(maxLevel, Number(requestedLevel) || 1))
  const delta = requested - baseLevel
  if (!delta) {
    item.level = baseLevel
    tocLevelBefore.value = null
    return
  }
  let end = index + 1
  while (end < tocDraft.value.length && tocDraft.value[end].level > baseLevel) end++
  for (let i = index; i < end; i++) {
    tocDraft.value[i].level = i === index
      ? requested
      : Math.max(1, Math.min(4, tocDraft.value[i].level + delta))
    tocDraft.value[i].edited = true
  }
  tocLevelBefore.value = null
}
const subtreeEnd = (index) => {
  const level = tocDraft.value[index].level
  let end = index + 1
  while (end < tocDraft.value.length && tocDraft.value[end].level > level) end++
  return end
}
const moveTocGroup = (index, direction) => {
  const end = subtreeEnd(index)
  const group = tocDraft.value.splice(index, end - index)
  if (direction < 0) {
    let target = index - 1
    const level = group[0].level
    while (target > 0 && tocDraft.value[target].level > level) target--
    tocDraft.value.splice(target, 0, ...group)
  } else {
    let target = index
    while (target < tocDraft.value.length && tocDraft.value[target].level > group[0].level) target++
    if (target < tocDraft.value.length) target = subtreeEnd(target)
    tocDraft.value.splice(target, 0, ...group)
  }
  group.forEach(item => { item.edited = true })
  normalizeAllTocLevels()
}
const selectedTocIndex = () => tocDraft.value.findIndex(item => item.client_key === selectedTocKey.value)
const moveSelectedToc = (direction) => {
  const index = selectedTocIndex(); if (index < 0) return
  checkpointToc(); moveTocGroup(index, direction)
}

const tocDropPosition = (event) => {
  const rect = event.currentTarget?.getBoundingClientRect?.()
  if (!rect || !rect.height) return 'inside'
  const ratio = (event.clientY - rect.top) / rect.height
  return ratio < 0.28 ? 'before' : ratio > 0.72 ? 'after' : 'inside'
}
const startTocDrag = (data, event) => {
  if (tocReviewOnly.value) return
  const index = tocDraft.value.findIndex(item => item.client_key === data.client_key)
  if (index < 0) return
  tocDrag.value = { key: data.client_key, index }
  tocDrop.value = null
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', data.client_key)
  }
}
const overTocDrag = (data, event) => {
  if (!tocDrag.value || tocReviewOnly.value) return
  const sourceIndex = tocDraft.value.findIndex(item => item.client_key === tocDrag.value.key)
  const targetIndex = tocDraft.value.findIndex(item => item.client_key === data.client_key)
  if (sourceIndex < 0 || targetIndex < 0) return
  const sourceEnd = subtreeEnd(sourceIndex)
  if (targetIndex >= sourceIndex && targetIndex < sourceEnd) {
    tocDrop.value = null
    return
  }
  tocDrop.value = { key: data.client_key, position: tocDropPosition(event) }
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}
const endTocDrag = () => { tocDrag.value = null; tocDrop.value = null }
const dropTocDrag = (data, event) => {
  if (!tocDrag.value || tocReviewOnly.value) return endTocDrag()
  const sourceIndex = tocDraft.value.findIndex(item => item.client_key === tocDrag.value.key)
  const targetKey = data.client_key
  const targetIndexBefore = tocDraft.value.findIndex(item => item.client_key === targetKey)
  if (sourceIndex < 0 || targetIndexBefore < 0) return endTocDrag()
  const sourceEnd = subtreeEnd(sourceIndex)
  if (targetIndexBefore >= sourceIndex && targetIndexBefore < sourceEnd) return endTocDrag()
  const position = tocDrop.value?.position || tocDropPosition(event)
  checkpointToc()
  const group = tocDraft.value.splice(sourceIndex, sourceEnd - sourceIndex)
  const targetIndex = tocDraft.value.findIndex(item => item.client_key === targetKey)
  if (targetIndex < 0) return endTocDrag()
  const target = tocDraft.value[targetIndex]
  const desiredLevel = position === 'inside' ? Math.min(4, target.level + 1) : target.level
  const insertAt = position === 'after' || position === 'inside' ? subtreeEnd(targetIndex) : targetIndex
  const delta = desiredLevel - group[0].level
  group.forEach(item => {
    item.level = Math.max(1, Math.min(4, item.level + delta))
    item.edited = true
  })
  tocDraft.value.splice(insertAt, 0, ...group)
  normalizeAllTocLevels()
  selectedTocKey.value = group[0].client_key
  endTocDrag()
}
const shiftSelectedToc = (delta) => {
  const index = selectedTocIndex(); if (index < 0) return
  checkpointToc()
  const item = tocDraft.value[index]
  normalizeTocLevels(index, item.level + delta)
}
const changeSelectedLevel = (value, oldValue) => {
  const index = selectedTocIndex(); if (index < 0) return
  selectedToc.value.edited = true
  normalizeTocLevels(index, value, Number(oldValue) || tocLevelBefore.value)
}
const addTocAfter = (index) => {
  const current = tocDraft.value[index]
  const insertAt = subtreeEnd(index)
  tocDraft.value.splice(insertAt, 0, { client_key: `new:${++tocTempId}`, id: null,
    title: '新目录项', level: current.level, start_page: current.start_page,
    review_status: 'review', issueText: '请核对原文标题和页码', edited: true })
  normalizeAllTocLevels()
}
const addSelectedToc = () => {
  const index = selectedTocIndex(); if (index < 0) return
  checkpointToc(); addTocAfter(index)
  selectedTocKey.value = `new:${tocTempId}`
}
const cnNumbers=['零','一','二','三','四','五','六','七','八','九','十']
const missingMarker=(scheme,number)=>scheme==='cn_paren'?`（${cnNumbers[number]||number}）`:`（${number}）`
const missingCandidateLabel=candidate=>(candidate.numbers||[]).map(number=>missingMarker(candidate.scheme,number)).join('、')
const insertMissingCandidate = async candidate => {
  const numbers=candidate.numbers||[]
  if(!numbers.length)return
  try{
    await ElMessageBox.confirm(`将在目录中加入 ${missingCandidateLabel(candidate)} 的“标题待核对”占位。保存前仍可修改或删除；系统不会生成虚构标题。`,'添加缺项占位',{confirmButtonText:'加入草稿',cancelButtonText:'取消',type:'warning'})
    checkpointToc()
    const target=Math.max(0,Math.min(tocDraft.value.length,Number(candidate.before_index)||0))
    const reference=tocDraft.value[target]||tocDraft.value.at(-1)
    const rows=numbers.map(number=>({client_key:`new:${++tocTempId}`,id:null,title:`${missingMarker(candidate.scheme,number)}【标题待核对】`,level:reference?.level||1,start_page:candidate.page_range?.[0]||reference?.start_page||1,review_status:'review',issueText:'编号链提示此处可能漏识或错序；请对照原页补全标题',edited:true}))
    tocDraft.value.splice(target,0,...rows);normalizeAllTocLevels();selectedTocKey.value=rows[0].client_key
    ElMessage.info('已加入目录草稿，核对标题后再保存')
  }catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.message||String(e))}
}
const inspectTocPage = async (item) => {
  tocEditorOpen.value = false
  mode.value = 'source'
  initialPage.value = item.start_page || 1
  hasQueryPage.value = true
  pdfReloadSeq.value++
  await router.replace({ path: `/reader/${book.value.id}`, query: { page: initialPage.value } })
}
const removeTocItem = (index) => {
  const level = tocDraft.value[index].level
  tocDraft.value.splice(index, 1)
  while (index < tocDraft.value.length && tocDraft.value[index].level > level) {
    tocDraft.value[index].level = Math.max(1, tocDraft.value[index].level - 1)
    tocDraft.value[index].edited = true
    index++
  }
  normalizeAllTocLevels()
}
const removeSelectedToc = () => {
  const index = selectedTocIndex(); if (index < 0) return
  checkpointToc(); removeTocItem(index)
  selectedTocKey.value = tocDraft.value[Math.min(index, tocDraft.value.length - 1)]?.client_key || ''
}
const buildTocPayload = () => {
  normalizeAllTocLevels()
  const stack = []
  return tocDraft.value.map(item => {
    while (stack.length >= item.level) stack.pop()
    const parent = item.level > 1 ? stack[item.level - 2] : null
    const payload = { client_key: item.client_key, id: item.id, parent_key: parent?.client_key || null,
      title: item.title.trim(), level: item.level, start_page: item.start_page }
    stack[item.level - 1] = item
    return payload
  })
}
const saveTocEditor = async () => {
  if (tocDraft.value.some(item => !item.title.trim())) return ElMessage.warning('目录标题不能为空')
  tocSaving.value = true
  try {
    const result = await replaceBookToc(book.value.id, buildTocPayload(), '阅读器人工校正')
    await applyTocResultInPlace(result)
    ElMessage.success('目录、分块归属与来源映射已同步')
  } catch (e) { ElMessage.error(e.message) }
  finally { tocSaving.value = false }
}
const applySafeTocRepair = async () => {
  try {
    const result = await autoRepairToc(book.value.id, false)
    if (!result.preview?.can_apply) return ElMessage.warning(result.preview?.blocked_reason || '当前目录不能安全自动修正')
    const changes = result.preview.changes || []
    await ElMessageBox.confirm(`将修改 ${changes.length} 项可由编号证明的层级或父级。写入前已完成未决冲突检查，并会保存可恢复快照。`, '预览安全自修正')
    const applied = await autoRepairToc(book.value.id, true)
    ElMessage.success('安全修正已应用')
    await applyTocResultInPlace(applied)
  } catch (e) { if (e !== 'cancel') ElMessage.error(e.message || String(e)) }
}
const rebuildCurrentToc = async () => {
  const bookId = book.value.id
  try {
    await ElMessageBox.confirm('将根据本书已保存的正文与 OCR 坐标重新生成目录，保留可恢复的旧目录修订。本窗口未保存的修改会被替换。', '重新识别本书目录', { confirmButtonText: '重新识别', cancelButtonText: '取消' })
    tocRebuilding.value = true
    const response = await rebuildBookToc(bookId)
    notifyTaskSubmitted()
    const task = await subscribeTask(response.task_id, next => { tocRebuildMessage.value = next.message || '正在重识别目录…' }, { signal: rebuildAbort.signal })
    if (task.status !== 'done') throw new Error(task.error || task.message || '重识别未完成')
    if (task.result?.status !== 'rebuilt') return ElMessage.warning(task.result?.reason || '缺少可用目录证据')
    if (book.value?.id !== bookId) return
    const [detail, audit] = await Promise.all([getBook(bookId), getTocReview(bookId)])
    await applyTocResultInPlace({ chapters: detail.chapters, audit })
    ElMessage.success('目录已原位更新，可在修订记录中恢复旧版本')
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || String(e)) }
  finally { tocRebuilding.value = false }
}
const restoreRevision = async (revision) => {
  try {
    await ElMessageBox.confirm('将恢复到该次修订发生前的目录，并重新建立章节来源映射。当前版本也会保存为可恢复修订。', '恢复目录版本')
    const restored = await restoreTocRevision(book.value.id, revision.id)
    ElMessage.success('目录已恢复，当前版本已保留在修订记录中')
    await applyTocResultInPlace(restored)
  } catch (e) { if (e !== 'cancel') ElMessage.error(e.message || String(e)) }
}

const formatDate = (value) => value ? new Date(value).toLocaleString('zh-CN') : ''

const loadDeep = async () => {
  if (!book.value) return
  const id = book.value.id
  mdLoading.value = true
  try { const data = await getBookDeep(id); if (book.value?.id === id) deepData.value = data }
  catch (e) { ElMessage.error(e.message) }
  finally { if (book.value?.id === id) mdLoading.value = false }
}

const followDeep = async (id, taskId) => {
  deepAbort?.abort()
  const controller = new AbortController()
  deepAbort = controller
  deepTaskId.value = taskId
  runningDeep.value = true
  let refreshedAt = 0
  let refreshing = false
  const current = () => !controller.signal.aborted && book.value?.id === id
  const refresh = async () => {
    if (refreshing || !current()) return
    refreshing = true
    try { const data = await getBookDeep(id); if (current()) deepData.value = data }
    catch { /* A transient read error must not interrupt the persisted task. */ }
    finally { refreshing = false }
  }
  try {
    const task = await subscribeTask(taskId, next => {
      if (!current()) return
      deepTaskMessage.value = next.message || '正在研读，完成章节会自动保存'
      deepTaskProgress.value = Math.round(Math.max(0, Math.min(1, next.progress || 0)) * 100)
      if (Date.now() - refreshedAt >= 5000) { refreshedAt = Date.now(); void refresh() }
    }, { signal: controller.signal })
    if (!current()) return
    localStorage.removeItem(`reader-deep-task:${id}`)
    const data = await getBookDeep(id)
    if (!current()) return
    deepData.value = data
    deepTaskMessage.value = task.status === 'done' ? (task.message || '研读完成')
      : `${task.status === 'cancelled' ? '已取消' : '研读中断'}，已完成章节已保留。${task.error || ''}`
  } catch (e) { if (current()) deepTaskMessage.value = `进度连接中断，可返回页面重连：${e.message}` }
  finally { if (current()) { runningDeep.value = false; deepTaskId.value = '' } }
}

const stopDeep = async () => {
  try { await cancelTask(deepTaskId.value); deepTaskMessage.value = '正在取消，保留已完成章节…' }
  catch (e) { ElMessage.error(e.message) }
}

const onModeChange = async (next) => {
  if (next !== 'source' && !deepData.value.markdown && !deepData.value.paper_card) await loadDeep()
}

const runDeep = async () => {
  if (!book.value || runningDeep.value) return
  runningDeep.value = true
  const id = book.value.id
  try {
    const response = await deepAnalyze(id)
    localStorage.setItem(`reader-deep-task:${id}`, response.task_id)
    notifyTaskSubmitted()
    if (book.value?.id === id) void followDeep(id, response.task_id)
  } catch (e) { if (book.value?.id === id) { runningDeep.value = false; ElMessage.error(e.message) } }
}

const jumpArtifact = async (index) => {
  await nextTick()
  const selector = mode.value === 'card' ? 'h2' : 'h2,h3,h4'
  const headings = artifactContent.value?.querySelectorAll(selector)
  headings?.[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const saveReadingStatus = async () => {
  if (!book.value) return
  book.value.archive = await updateArchiveProfile(book.value.id, { reading_status: readingStatus.value })
}

const toggleFavorite = async () => {
  const next = !book.value.archive?.favorite
  book.value.archive = await updateArchiveProfile(book.value.id, { favorite: next })
}

const onPageChange = ({ page }) => {
  if (!book.value) return
  clearTimeout(progressTimer)
  progressTimer = setTimeout(() => {
    updateArchiveProfile(book.value.id, {
      progress_page: page,
      reading_status: readingStatus.value === 'unread' ? 'reading' : readingStatus.value,
    }).catch(() => {})
  }, 800)
}

// 快速切换书籍时，先发的慢响应可能后到并覆盖新书详情；用序号丢弃过期结果。
let bookRequestSeq = 0
const loadBook = async (bookId) => {
  const seq = ++bookRequestSeq
  deepAbort?.abort()
  runningDeep.value = false
  deepTaskMessage.value = ''
  deepTaskId.value = ''
  book.value = null
  deepData.value = { toc: [], markdown: '', paper_card: '' }
  try {
    const [detail, map] = await Promise.all([getBook(bookId), getSourceMap(bookId)])
    if (seq !== bookRequestSeq) return
    book.value = detail
    sourceMap.value = map
    readingStatus.value = detail.archive?.reading_status || 'unread'
    const flat = []
    const walk = (nodes, level) => {
      for (const node of nodes) {
        flat.push({ id: node.id, title: node.title, level: node.level || level, start_page: node.start_page })
        if (node.children?.length) walk(node.children, level + 1)
      }
    }
    walk(detail.chapters || [], 1)
    tocFlat.value = flat
    if (detail.archive?.progress_page > 1 && !hasQueryPage.value) initialPage.value = detail.archive.progress_page
    if (mode.value !== 'source') await loadDeep()
    if (seq === bookRequestSeq) {
      const taskId = localStorage.getItem(`reader-deep-task:${bookId}`)
      if (taskId) void followDeep(Number(bookId), taskId)
    }
  } catch (e) { ElMessage.error(e.message) }
}

watch(() => route.params.bookId, (id) => { if (id) loadBook(Number(id)) })
onMounted(async () => {
  await loadBook(Number(route.params.bookId))
  if (route.query.toc === 'review') await openTocEditor()
})
onUnmounted(() => {
  // 未清理的定时器会在 book.value 置空后解引用报错，并丢失最后一次阅读进度。
  clearTimeout(progressTimer)
  rebuildAbort.abort()
  deepAbort?.abort()
})
</script>

<style scoped>
.deep-task-monitor { display:flex; flex-wrap:wrap; align-items:center; gap:12px; padding:8px 12px; margin-bottom:6px; border-radius:var(--study-radius-md); background:var(--study-surface-paper); color:var(--study-text-secondary); font-size:13px; }
.deep-task-monitor > span { flex:1; min-width:180px; }
.deep-task-monitor :deep(.el-progress) { width:160px; }
.reader-page { display: flex; flex-direction: column; height: calc(100vh - 64px); max-width: 1800px; margin: 0 auto; }
.reader-top { display:flex; flex-direction:column; gap:6px; padding:7px 10px; margin-bottom:6px; background:var(--study-surface-paper); border:1px solid var(--study-card-border); border-radius:var(--study-radius-md); box-shadow:var(--study-shadow-sm); }
.reader-primary-row,.reader-secondary-row { display:flex; align-items:center; justify-content:space-between; gap:16px; min-width:0; }
.reader-identity, .reader-actions, .reader-status-actions { display: flex; align-items: center; gap: 8px; min-width: 0; }
.reader-identity { flex:1; }
.reader-heading { min-width:0; max-width:860px; }
.reader-actions,.reader-status-actions { flex:none; justify-content:flex-end; }
.reader-title { display:-webkit-box; overflow:hidden; -webkit-box-orient:vertical; -webkit-line-clamp:2; font-family:var(--study-font-display); font-size:17px; line-height:1.4; font-weight:600; color:var(--el-text-color-primary); }
.toc-review-btn { flex:none; }
.reader-meta { margin-top: 2px; font-size: var(--study-font-size-xs); color: var(--el-text-color-secondary); }
.reader-body { flex: 1; min-height: 0; }
.artifact-view { display: grid; grid-template-columns: 240px minmax(0, 1fr); height: 100%; overflow: hidden; border: 1px solid var(--el-border-color-lighter); border-radius: 14px; background: #f8f4ec; }
.artifact-aside { overflow-y: auto; padding: 18px 10px; border-right: 1px solid var(--el-border-color-lighter); background: #e9e2d5; }
.aside-label { padding: 0 12px 12px; color: #7f694d; font-size: 10px; letter-spacing: 2px; font-weight: 700; }
.toc-link { width: 100%; display: flex; justify-content: space-between; gap: 8px; padding: 7px 10px; border: 0; border-radius: 7px; background: transparent; color: #4d4b43; text-align: left; cursor: pointer; }
.toc-link:hover { background: rgba(255,255,255,.65); color: #6f4721; }
.toc-link span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.toc-link small { flex-shrink: 0; color: #9a8d7a; }
.source-summary { display: flex; flex-direction: column; margin: 18px 10px 0; padding: 12px; border-top: 1px solid rgba(111,71,33,.15); color: #756958; }
.source-summary strong { font: 700 22px Georgia, serif; }
.source-summary span { font-size: 11px; }
.artifact-content { overflow-y: auto; padding: 24px clamp(28px, 6vw, 92px) 80px; background: #f8f4ec; color: #2f302d; font-family: var(--study-font-reading); }
.artifact-content :deep(p), .artifact-content :deep(li), .artifact-content :deep(blockquote) { max-width: var(--reading-max-width); font-size: var(--reading-font-size); line-height: 2; text-align: justify; }
.artifact-banner { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 22px; padding: 12px 14px; border-left: 3px solid #8b5a2b; background: rgba(139,90,43,.06); }
.artifact-banner div { display: flex; flex-direction: column; gap: 3px; }
.artifact-banner small { color: var(--el-text-color-secondary); }
.type-controls { margin-left:auto; display:flex!important; flex-direction:row!important; align-items:center; gap:4px!important; }
.type-controls button { border:1px solid rgba(111,71,33,.16); border-radius:6px; padding:4px 7px; background:rgba(255,255,255,.55); color:#6f4721; cursor:pointer; }
.type-controls span { min-width:22px; text-align:center; font-size:11px; color:#887762; }
.focus-reading { position:fixed; inset:0; z-index:2000; height:100vh; max-width:none; padding:10px; background:#eee8dd; }
.focus-reading .reader-top { border-radius:10px; }
.artifact-loading { height: 240px; }
.toc-editor { min-height: 280px; }
.toc-audit-bar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:12px; padding:12px 14px; border:1px solid #e5e7eb; border-radius:10px; background:#f7f2e9; box-shadow:0 1px 2px rgba(15,23,42,.06); }
.toc-audit-bar > div:first-child { display:flex; flex-direction:column; gap:4px; }
.toc-audit-bar span, .toc-editor-help, .toc-revisions { color:var(--el-text-color-secondary); font-size:12px; }
.toc-audit-actions { display:flex; flex-direction:row!important; align-items:center; gap:8px!important; }
.toc-editor-help { margin:10px 2px; }
.toc-workspace-actions { display:flex; align-items:center; gap:10px; min-height:38px; color:var(--el-text-color-secondary); font-size:12px; }
.toc-workspace-actions > span { flex:1; }
.toc-missing-suggestions { display:flex; align-items:center; gap:7px; margin-bottom:10px; padding:8px 10px; overflow-x:auto; border:1px solid #ead6b8; border-radius:9px; background:#fff8ed; color:#766552; font-size:12px; white-space:nowrap; }
.toc-missing-suggestions>span { color:var(--el-text-color-secondary); }
.toc-workspace { display:grid; grid-template-columns:minmax(280px,.8fr) minmax(420px,1.3fr) minmax(300px,.85fr); gap:10px; height:570px; }
.toc-tree-panel,.toc-page-panel,.toc-inspector-panel { min-width:0; overflow:hidden; border:1px solid #e5e7eb; border-radius:10px; background:#fbf8f1; box-shadow:0 1px 2px rgba(15,23,42,.06); }
.toc-panel-title { display:flex; justify-content:space-between; align-items:center; height:40px; padding:0 12px; border-bottom:1px solid #e5e7eb; color:#635744; background:#e9e2d5; font-size:13px; font-weight:700; }
.toc-panel-title small { color:#8f806b; font-weight:400; }
.toc-tree-panel :deep(.el-tree-node__content) { height:36px; border-bottom:1px solid rgba(229,231,235,.5); }
.toc-tree-panel :deep(.el-tree-node__content:hover),.toc-tree-panel :deep(.el-tree-node.is-current > .el-tree-node__content) { background:#f1e8d9; }
.toc-tree-node { display:flex; align-items:center; min-width:0; flex:1; gap:6px; padding:0 8px 0 3px; border-left:2px solid transparent; border-top:2px solid transparent; border-bottom:2px solid transparent; border-radius:4px; transition:background .12s ease, border-color .12s ease, opacity .12s ease; }
.toc-tree-node[draggable="true"] { cursor:grab; }.toc-tree-node[draggable="true"]:active { cursor:grabbing; }
.toc-tree-node.is-dragging { opacity:.45; }.toc-tree-node.drop-before { border-top-color:#8b5a2b; }.toc-tree-node.drop-after { border-bottom-color:#8b5a2b; }.toc-tree-node.drop-inside { background:#f1e1c9; border-color:#c58a4b; }
.toc-tree-node.status-review { border-left-color:#d6a04d; }.toc-tree-node.status-low { border-left-color:#c96055; }
.toc-academic-audit{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0;padding:10px 12px;border:1px solid #ded3c2;border-radius:9px;background:#f8f3e9}.toc-academic-audit>div{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.toc-academic-audit b{color:#624727;font-size:13px}.toc-academic-audit span,.toc-academic-audit small{color:#80715f;font-size:11px}
.toc-node-title { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#423f38; font-family:var(--study-font-ui); font-size:13px; }
.toc-drag-handle { flex:none; width:14px; color:#ae9a7f; font-size:14px; line-height:1; letter-spacing:-3px; user-select:none; }
.toc-node-page { flex:none; color:#988a77; font:11px Georgia,serif; }.toc-node-edited { flex:none; padding:1px 4px; border-radius:4px; background:#8b5a2b; color:#fff; font-size:9px; }
.toc-page-panel { display:flex; flex-direction:column; }.toc-page-frame { width:100%; flex:1; border:0; background:#d8d5ce; }
.toc-inspector-panel { overflow-y:auto; }.toc-inspector-panel > :not(.toc-panel-title) { margin-left:14px; margin-right:14px; }
.toc-breadcrumb { margin-top:12px!important; padding:8px 10px; border-radius:7px; background:#f3ede2; color:#776a58; font-size:11px; line-height:1.6; }
.toc-inspector-panel :deep(.el-alert) { margin-top:10px; }.toc-inspector-form { margin-top:12px!important; }
.toc-inspector-fields { display:grid; grid-template-columns:1fr 1fr; gap:10px; }.toc-inspector-fields :deep(.el-input-number) { width:100%; }
.toc-structure-actions { display:grid; grid-template-columns:1fr 1fr; gap:7px; }.toc-structure-actions .el-button { margin:0; }
.toc-open-reader { width:calc(100% - 28px); margin-top:12px!important; }
@media(max-width:1050px){.toc-workspace{grid-template-columns:minmax(260px,.8fr) 1.2fr}.toc-inspector-panel{grid-column:1/-1;height:300px}.toc-workspace{height:auto}.toc-tree-panel,.toc-page-panel{height:480px}}
.toc-table-wrap { max-height:55vh; overflow:auto; border:1px solid #e5e7eb; border-radius:10px; }
.toc-edit-table { width:100%; border-collapse:collapse; background:#fbf8f1; }
.toc-edit-table th { position:sticky; top:0; z-index:1; padding:9px 8px; background:#e9e2d5; color:#635744; text-align:left; font-size:12px; }
.toc-edit-table td { padding:6px 8px; border-top:1px solid #e5e7eb; }
.toc-edit-table th:nth-child(1), .toc-edit-table td:nth-child(1) { width:42px; text-align:center; }
.toc-edit-table th:nth-child(2), .toc-edit-table td:nth-child(2) { width:72px; }
.toc-edit-table th:nth-child(4), .toc-edit-table td:nth-child(4) { width:120px; }
.toc-edit-table th:nth-child(5), .toc-edit-table td:nth-child(5) { width:120px; }
.toc-edit-table th:nth-child(6), .toc-edit-table td:nth-child(6) { width:220px; }
.toc-row-actions { white-space:nowrap; }
.toc-revisions { margin-top:10px; }
.privacy-tip { max-width: 520px; margin-top: 12px; color: var(--el-text-color-secondary); font-size: 12px; text-align: center; }
@media (max-width: 1280px) {
  .reader-primary-row { align-items:flex-start; }
  .reader-heading { max-width:none; }
  .reader-actions .el-button:not(:last-child) { padding-inline:9px; }
  .reader-secondary-row { overflow-x:auto; padding-bottom:2px; }
  .reader-status-actions { white-space:nowrap; }
  .artifact-view { grid-template-columns: 190px minmax(0, 1fr); }
}
@media (max-width: 900px) {
  .reader-primary-row { flex-direction:column; }
  .reader-actions { width:100%; justify-content:flex-start; }
  .reader-secondary-row { align-items:flex-start; flex-direction:column; overflow:visible; }
  .reader-status-actions { width:100%; overflow-x:auto; justify-content:flex-start; padding-bottom:2px; }
}
@media (max-width: 720px) {
  .reader-actions :deep(.el-button), .reader-status-actions :deep(.el-button), .reader-mode :deep(.el-radio-button__inner) { min-height:40px; padding:8px 11px; }
  .reader-title { font-size:15px; }
  .artifact-view { grid-template-columns: 1fr; }
  .artifact-aside { display: none; }
  .artifact-content { padding: 20px 18px 60px; }
}
</style>
