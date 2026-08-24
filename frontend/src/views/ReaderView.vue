<template>
  <div class="reader-page" :class="{ 'focus-reading': focusMode }">
    <header class="reader-top">
      <div class="reader-identity">
        <el-button circle plain @click="$router.push('/library')">←</el-button>
        <div>
          <div class="reader-title">{{ book?.title || '阅读器' }}</div>
          <div class="reader-meta">
            {{ [book?.archive?.authors, book?.archive?.journal, book?.archive?.published_year].filter(Boolean).join(' · ') || '本地文献' }}
          </div>
        </div>
      </div>
      <el-radio-group v-model="mode" size="small" @change="onModeChange">
        <el-radio-button value="source">原版阅读</el-radio-button>
        <el-radio-button value="deep">结构精读</el-radio-button>
        <el-radio-button value="card">证据卡片</el-radio-button>
      </el-radio-group>
      <div class="reader-actions" v-if="book">
        <el-button size="small" @click="askKnowledgeBase">围绕本文提问</el-button>
        <el-button size="small" type="primary" plain @click="sendToDeck">选段生成汇报</el-button>
        <el-tooltip content="专注阅读"><el-button size="small" circle @click="focusMode = !focusMode">{{ focusMode ? '↙' : '⛶' }}</el-button></el-tooltip>
        <el-tag size="small" type="info">{{ sourceMap.locator_mode === 'page-grounded' ? '页码可回溯' : '结构定位' }}</el-tag>
        <el-select v-model="readingStatus" size="small" style="width: 96px" @change="saveReadingStatus">
          <el-option label="未读" value="unread" />
          <el-option label="阅读中" value="reading" />
          <el-option label="已读完" value="read" />
        </el-select>
        <el-button size="small" :type="book.archive?.favorite ? 'warning' : ''" plain @click="toggleFavorite">
          {{ book.archive?.favorite ? '★ 已收藏' : '☆ 收藏' }}
        </el-button>
      </div>
    </header>

    <main class="reader-body" v-loading="!book">
      <template v-if="book && mode === 'source'">
        <DocReader v-if="book.file_type !== 'pdf'" :book-id="book.id" />
        <PdfReader v-else :src="fileUrl" :book-id="book.id" :initial-page="initialPage"
          :toc="tocFlat" show-toc show-ai :use-saved-pos="!hasQueryPage" @page-change="onPageChange" />
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
            <p class="privacy-tip">将目录候选与相关正文发送至已配置的 DeepSeek，用于结构补缺与来源约束分析。</p>
          </el-empty>
        </article>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import PdfReader from '../components/PdfReader.vue'
import DocReader from '../components/DocReader.vue'
import {
  getBook, bookFileUrl, getBookDeep, deepAnalyze,
  getSourceMap, updateArchiveProfile,
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
const mode = ref(route.query.mode === 'card' ? 'card' : route.query.mode === 'deep' ? 'deep' : 'source')
const readingStatus = ref('unread')
const deepData = ref({ toc: [], markdown: '', paper_card: '' })
const mdLoading = ref(false)
const runningDeep = ref(false)
const artifactContent = ref(null)
const focusMode = ref(false)
const fontSize = ref(Number(localStorage.getItem('readerFontSize')) || 16)
const wideText = ref(localStorage.getItem('readerWideText') === 'true')
let progressTimer = null

const fileUrl = computed(() => book.value ? bookFileUrl(book.value.id) : '')
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

const formatDate = (value) => value ? new Date(value).toLocaleString('zh-CN') : ''

const loadDeep = async () => {
  if (!book.value) return
  mdLoading.value = true
  try { deepData.value = await getBookDeep(book.value.id) }
  catch (e) { ElMessage.error(e.message) }
  finally { mdLoading.value = false }
}

const onModeChange = async (next) => {
  if (next !== 'source' && !deepData.value.markdown && !deepData.value.paper_card) await loadDeep()
}

const runDeep = async () => {
  if (!book.value || runningDeep.value) return
  runningDeep.value = true
  try {
    await deepAnalyze(book.value.id)
    notifyTaskSubmitted()
    ElMessage.success('结构复核与精读已进入任务中心，可继续阅读原文')
  } catch (e) { ElMessage.error(e.message) }
  finally { runningDeep.value = false }
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

const loadBook = async (bookId) => {
  book.value = null
  deepData.value = { toc: [], markdown: '', paper_card: '' }
  try {
    const [detail, map] = await Promise.all([getBook(bookId), getSourceMap(bookId)])
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
  } catch (e) { ElMessage.error(e.message) }
}

watch(() => route.params.bookId, (id) => { if (id) loadBook(Number(id)) })
onMounted(() => loadBook(Number(route.params.bookId)))
</script>

<style scoped>
.reader-page { display: flex; flex-direction: column; height: calc(100vh - 70px); max-width: 1800px; margin: 0 auto; }
.reader-top { min-height: 58px; display: grid; grid-template-columns: minmax(260px, 1fr) auto minmax(260px, 1fr); align-items: center; gap: 18px; padding: 8px 14px; margin-bottom: 8px; background: rgba(245,240,232,.96); border: 1px solid var(--el-border-color-lighter); border-radius: 14px; box-shadow: 0 4px 18px rgba(20,30,31,.08); }
.reader-identity, .reader-actions { display: flex; align-items: center; gap: 10px; min-width: 0; }
.reader-actions { justify-content: flex-end; }
.reader-title { max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; color: var(--el-text-color-primary); }
.reader-meta { margin-top: 2px; font-size: 11px; color: var(--el-text-color-secondary); }
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
.artifact-content { overflow-y: auto; padding: 24px clamp(28px, 6vw, 92px) 80px; background: #f8f4ec; color: #2f302d; font-family: Georgia, 'Noto Serif SC', 'STSong', serif; }
.artifact-content :deep(p), .artifact-content :deep(li), .artifact-content :deep(blockquote) { max-width: var(--reading-max-width); font-size: var(--reading-font-size); line-height: 2; text-align: justify; }
.artifact-banner { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 22px; padding: 12px 14px; border-left: 3px solid #8b5a2b; background: rgba(139,90,43,.06); }
.artifact-banner div { display: flex; flex-direction: column; gap: 3px; }
.artifact-banner small { color: var(--el-text-color-secondary); }
.type-controls { margin-left:auto; display:flex!important; flex-direction:row!important; align-items:center; gap:4px!important; }
.type-controls button { border:1px solid rgba(111,71,33,.16); border-radius:6px; padding:4px 7px; background:rgba(255,255,255,.55); color:#6f4721; cursor:pointer; }
.type-controls span { min-width:22px; text-align:center; font-size:11px; color:#887762; }
.focus-reading { position:fixed; inset:0; z-index:2000; height:100vh; max-width:none; padding:10px; background:#eee8dd; }
.focus-reading .reader-top { grid-template-columns:minmax(260px,1fr) auto minmax(340px,1fr); }
.artifact-loading { height: 240px; }
.privacy-tip { max-width: 520px; margin-top: 12px; color: var(--el-text-color-secondary); font-size: 12px; text-align: center; }
@media (max-width: 1050px) {
  .reader-top { grid-template-columns: 1fr auto; }
  .reader-actions { grid-column: 1 / -1; justify-content: flex-start; }
  .artifact-view { grid-template-columns: 190px minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .reader-top { display: flex; align-items: flex-start; flex-direction: column; }
  .artifact-view { grid-template-columns: 1fr; }
  .artifact-aside { display: none; }
  .artifact-content { padding: 20px 18px 60px; }
}
</style>
