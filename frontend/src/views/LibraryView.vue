<template>
  <div class="library-page">
    <section class="library-hero">
      <div>
        <div class="eyebrow">LOCAL RESEARCH LIBRARY</div>
        <h1>文献知识库</h1>
        <p>导入、校正结构、归档、精读与回溯来源，在一个连续工作流中完成。</p>
      </div>
      <div class="hero-stats">
        <div><strong>{{ books.length }}</strong><span>全部资料</span></div>
        <div><strong>{{ books.filter(b => b.deep_status === 'done').length }}</strong><span>已精读</span></div>
        <div><strong>{{ books.filter(b => b.reading_status === 'read').length }}</strong><span>已读完</span></div>
      </div>
    </section>
    <div class="library-workspace">
      <section>
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <div class="header-title">
                <span>我的资料</span>
                <small>{{ filteredBooks.length }} / {{ books.length }}</small>
              </div>
              <div class="header-actions">
              <el-button type="success" plain :loading="classifying" @click="classifyAll">🤖 自动分类</el-button>
              <el-upload
                :show-file-list="false"
                :before-upload="handleUpload"
                accept=".pdf,.docx,.pptx"
                :disabled="uploading"
              >
                <el-button type="primary" :loading="uploading">
                  {{ uploading ? '上传中…' : '上传 PDF / Word / PPT' }}
                </el-button>
              </el-upload>
              <el-upload
                :show-file-list="false"
                :auto-upload="false"
                :on-change="handleBatchSelect"
                multiple
                accept=".pdf,.docx,.pptx"
                :disabled="uploading"
              >
                <el-button type="warning" plain :loading="uploading">
                  {{ uploading ? '批量上传中…' : '📁 选择批量文件' }}
                </el-button>
              </el-upload>
              <el-button v-if="batchFiles.length" type="warning" @click="submitBatch">
                开始导入 {{ batchFiles.length }} 篇
              </el-button>
              </div>
            </div>
          </template>

          <div class="library-filters">
            <el-input v-model="libraryQ" clearable placeholder="检索题名、作者、期刊或 DOI" />
            <el-select v-model="libraryCategory" clearable placeholder="全部分类">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
            <el-select v-model="readingFilter" clearable placeholder="阅读状态">
              <el-option label="未读" value="unread" />
              <el-option label="阅读中" value="reading" />
              <el-option label="已读完" value="read" />
            </el-select>
            <el-checkbox v-model="favoriteOnly">仅收藏</el-checkbox>
          </div>

          <el-table :data="filteredBooks" v-loading="loading" empty-text="还没有符合条件的资料">
            <el-table-column label="" width="44">
              <template #default="{ row }"><span class="star" :class="{ active: row.favorite }" @click="toggleFavorite(row)">★</span></template>
            </el-table-column>
            <el-table-column label="文献" min-width="230">
              <template #default="{ row }">
                <div class="paper-title">{{ row.title }}</div>
                <div class="paper-meta">{{ [row.authors, row.journal, row.published_year].filter(Boolean).join(' · ') || '等待补充书目信息' }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="file_type" label="类型" width="70">
              <template #default="{ row }">
                <el-tag size="small">{{ row.file_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <el-tag v-if="row.status === 'ready'" type="success" size="small">已就绪</el-tag>
                <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">解析失败</el-tag>
                <el-tag v-else-if="row.status === 'needs_ocr'" type="warning" size="small" effect="dark">需 OCR</el-tag>
                <el-tooltip v-else :content="row.task_message || '解析中…'" placement="top">
                  <el-tag type="info" size="small">{{ row.task_message ? extractProgress(row.task_message) : '解析中' }}</el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column prop="total_pages" label="页数" width="70" />
            <el-table-column label="阅读" width="86">
              <template #default="{ row }">
                <el-tag size="small" :type="readingTagType(row.reading_status)">{{ readingLabel(row.reading_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="分类" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.category" size="small" type="info" class="cat-tag" @click="editCategory(row)">{{ row.category }}</el-tag>
                <el-button v-else link type="primary" size="small" @click="editCategory(row)">未分类</el-button>
              </template>
            </el-table-column>
            <el-table-column label="深度分析" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.deep_status === 'done'" size="small" type="success">✓ 已精读</el-tag>
                <el-tag v-else-if="row.deep_status === 'running'" size="small" type="warning">分析中</el-tag>
                <el-button v-else link type="primary" size="small" @click="runDeep(row)">深度分析</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="quiz_count" label="题目" width="70" />
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openBook(row)">详情</el-button>
                <el-button link type="success" size="small" @click="readBook(row)">阅读</el-button>
                <el-popconfirm title="确认删除？将删除该书的全部解析数据" @confirm="removeBook(row)">
                  <template #reference>
                    <el-button link type="danger" size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
          <div class="mobile-paper-list">
            <article v-for="row in filteredBooks" :key="row.id" class="mobile-paper-card">
              <div class="mobile-paper-head"><span class="star" :class="{ active: row.favorite }" @click="toggleFavorite(row)">★</span><div><b>{{ row.title }}</b><small>{{ [row.authors, row.journal, row.published_year].filter(Boolean).join(' · ') || '等待补充书目信息' }}</small></div></div>
              <div class="mobile-paper-tags"><el-tag size="small">{{ row.file_type }}</el-tag><el-tag size="small" :type="readingTagType(row.reading_status)">{{ readingLabel(row.reading_status) }}</el-tag><el-tag v-if="row.category" size="small" type="info">{{ row.category }}</el-tag></div>
              <div class="mobile-paper-actions"><el-button type="primary" size="small" @click="readBook(row)">阅读</el-button><el-button size="small" @click="openBook(row)">详情</el-button><el-button size="small" @click="openWorkbench(row)">生成汇报</el-button></div>
            </article>
            <el-empty v-if="!filteredBooks.length && !loading" description="先导入一篇文献，开始建立个人知识库" :image-size="72" />
          </div>
        </el-card>

        <el-card shadow="never" style="margin-top: 16px">
          <template #header>全文搜索（跨资料混合检索）</template>
          <div class="search-filters">
            <el-select v-model="searchCategory" placeholder="全部分类" clearable size="small" style="width: 120px">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
          <el-input
            v-model="searchQ"
            placeholder="输入关键词，跨全部资料搜索（向量+全文+子串三路融合）"
            clearable
            @keyup.enter="doSearch()"
            @clear="results = null"
            style="margin-top: 8px"
          >
            <template #append>
              <el-button @click="doSearch()">搜索</el-button>
            </template>
          </el-input>

          <div v-if="searching" v-loading="true" style="height: 60px" />
          <div v-else-if="results" style="margin-top: 12px">
            <div class="result-count">共 {{ results.total }} 条结果</div>
            <el-card v-for="r in results.items" :key="r.chunk_id" shadow="never" class="result-item">
              <div class="result-meta">
                <el-tag size="small" type="info">《{{ r.book_title }}》</el-tag>
                <span v-if="r.chapter_title" class="result-chapter">{{ r.chapter_title }}</span>
                <span v-if="r.page_start && r.page_end && r.page_end !== r.page_start" class="result-page">第 {{ r.page_start }}-{{ r.page_end }} 页</span>
                <span v-else-if="r.page || r.page_start" class="result-page">第 {{ r.page || r.page_start }} 页</span>
                <el-button link type="primary" size="small" style="margin-left: auto"
                  @click="viewOriginal(r)">📄 查看原文</el-button>
              </div>
              <div class="result-snippet" v-html="sanitizeHtml(r.snippet)" />
            </el-card>
          </div>
        </el-card>
      </section>

      <el-drawer v-model="detailVisible" size="min(520px, 94vw)" append-to-body class="paper-drawer">
        <template #header><div><div class="drawer-eyebrow">KNOWLEDGE SOURCE</div><b>{{ currentBook?.title }}</b></div></template>
        <div v-if="currentBook" class="paper-detail">
          <div class="detail-actions">
            <el-button type="primary" @click="readBook(currentBook)">进入阅读</el-button>
            <el-button @click="openWorkbench(currentBook)">生成汇报</el-button>
          </div>
          <el-divider content-position="left">文献结构</el-divider>
          <el-tree
            :data="chapterTree"
            :props="{ label: 'title', children: 'children' }"
            default-expand-all
            empty-text="暂无章节"
          />
          <template v-if="currentBook.archive">
            <el-divider content-position="left">文献归档</el-divider>
            <div class="archive-grid">
              <label>作者<el-input v-model="currentBook.archive.authors" size="small" /></label>
              <label>期刊<el-input v-model="currentBook.archive.journal" size="small" /></label>
              <label>年份<el-input-number v-model="currentBook.archive.published_year" :min="1000" :max="3000" size="small" /></label>
              <label>DOI<el-input v-model="currentBook.archive.doi" size="small" /></label>
              <label>状态
                <el-select v-model="currentBook.archive.reading_status" size="small">
                  <el-option label="未读" value="unread" /><el-option label="阅读中" value="reading" /><el-option label="已读完" value="read" />
                </el-select>
              </label>
            </div>
            <el-button type="primary" plain size="small" style="width: 100%; margin-top: 10px" @click="saveArchive">保存归档信息</el-button>
          </template>
          <el-alert v-if="currentBook.status === 'failed'" type="error" :title="'解析失败：' + (currentBook.error_msg || '')" style="margin-top: 12px" />
          <el-alert v-else-if="currentBook.status === 'needs_ocr'" type="warning" :closable="false" :title="currentBook.error_msg || '扫描版 PDF，需安装 OCR 引擎'" description="可点击「阅读」用内置阅读器直接查看原文件；如需检索/问答，请安装 OCR 引擎后重新解析" style="margin-top: 12px" />

          <!-- 智能分析结果 -->
          <template v-if="currentBook.analysis">
            <el-divider content-position="left">📊 智能分析</el-divider>

            <div v-if="currentBook.analysis.theorems?.length" class="analysis-section">
              <div class="analysis-title">定理 / 公式</div>
              <el-tag v-for="(t, i) in currentBook.analysis.theorems" :key="i"
                size="small" type="warning" class="analysis-tag">{{ t.type }}</el-tag>
            </div>

            <div v-if="currentBook.analysis.definitions?.length" class="analysis-section">
              <div class="analysis-title">关键定义</div>
              <div v-for="(d, i) in currentBook.analysis.definitions.slice(0, 8)" :key="i" class="analysis-item">
                <b>{{ d.term }}</b>：{{ d.definition }}
              </div>
            </div>

            <div v-if="currentBook.analysis.keywords?.length" class="analysis-section">
              <div class="analysis-title">关键词（{{ currentBook.analysis.keywords.length }}）</div>
              <span v-for="(k, i) in currentBook.analysis.keywords.slice(0, 20)" :key="i"
                class="keyword-chip" @click="searchKeyword(k)">{{ k }}</span>
            </div>

            <div class="analysis-section analysis-meta">
              正文字号 {{ currentBook.analysis.body_size }} · 表格 {{ currentBook.analysis.table_pages?.length || 0 }} 处
            </div>
          </template>
        </div>
      </el-drawer>
    </div>

    <!-- 原文定位面板 -->
    <component :is="originalViewerComponent" v-if="originalViewerComponent" ref="originalViewer" />
  </div>
</template>

<script setup>
import { ref, shallowRef, computed, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBooks, uploadBook, uploadBookBatch, deleteBook, getBook, searchBooks, classifyAllBooks, setBookCategory, deepAnalyze, updateArchiveProfile } from '../api'
import { sanitizeHtml } from '../utils/markdown'
import { notifyTaskSubmitted } from '../stores/taskCenter'

const router = useRouter()
const books = ref([])
const loading = ref(false)
const uploading = ref(false)
const classifying = ref(false)
const searchQ = ref('')
const results = ref(null)
const searching = ref(false)
const searchCategory = ref(null)
const categories = ref([])
const libraryQ = ref('')
const libraryCategory = ref(null)
const readingFilter = ref(null)
const favoriteOnly = ref(false)
const currentBook = ref(null)
const detailVisible = ref(false)
const chapterTree = ref([])
const originalViewer = ref(null)
const originalViewerComponent = shallowRef(null)
const filteredBooks = computed(() => {
  const q = libraryQ.value.trim().toLowerCase()
  return books.value.filter((b) => {
    const hay = [b.title, b.authors, b.journal, b.doi].filter(Boolean).join(' ').toLowerCase()
    return (!q || hay.includes(q))
      && (!libraryCategory.value || b.category === libraryCategory.value)
      && (!readingFilter.value || b.reading_status === readingFilter.value)
      && (!favoriteOnly.value || b.favorite)
  })
})
const readingLabel = (status) => ({ unread: '未读', reading: '阅读中', read: '已读完' }[status] || '未读')
const readingTagType = (status) => ({ unread: 'info', reading: 'warning', read: 'success' }[status] || 'info')

const loadBooks = async () => {
  loading.value = true
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items
    // 提取唯一分类列表
    const cats = [...new Set(resp.items.map((b) => b.category).filter(Boolean))]
    categories.value = cats
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

const handleUpload = async (file) => {
  uploading.value = true
  try {
    const resp = await uploadBook(file)
    if (resp.duplicate) {
      ElMessage.warning(resp.message || '文件已存在')
    } else {
      ElMessage.success(`已上传，开始解析：${resp.title}`)
      notifyTaskSubmitted()
    }
    await loadBooks()
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
  return false // 阻止默认上传
}

// 从任务 message 提取进度（如 "OCR 识别中 45/173" → "OCR 45/173"）
const extractProgress = (msg) => {
  if (!msg) return ''
  const m = msg.match(/(\S+)\s+(\d+)\/(\d+)/)
  if (m) return m[1] + ' ' + m[2] + '/' + m[3]
  return msg.slice(0, 14)
}

const batchFiles = ref([])
// 选择文件后收集（el-upload on-change，auto-upload=false）
const handleBatchSelect = (file, fileList) => {
  batchFiles.value = fileList.map(f => f.raw)
}

const submitBatch = async () => {
  if (!batchFiles.value.length) return
  uploading.value = true
  try {
    const resp = await uploadBookBatch(batchFiles.value)
    const results = resp.results || []
    const ok = results.filter(r => r.task_id).length
    const dup = results.filter(r => r.duplicate).length
    const fail = results.filter(r => r.error).length
    ElMessage.success(`批量上传完成：${ok} 个解析中，${dup} 个重复跳过，${fail} 个失败`)
    batchFiles.value = []
    notifyTaskSubmitted()
    await loadBooks()
  } catch (e) {
    ElMessage.error('批量上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
}

const removeBook = async (row) => {
  try {
    await deleteBook(row.id)
    ElMessage.success('已删除')
    if (currentBook.value?.id === row.id) { currentBook.value = null; detailVisible.value = false }
    loadBooks()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const classifyAll = async () => {
  classifying.value = true
  try {
    const resp = await classifyAllBooks()
    ElMessage.success('分类完成')
    loadBooks()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    classifying.value = false
  }
}

const editCategory = async (row) => {
  try {
    const { value } = await ElMessageBox.prompt(`《${row.title}》分类（数学/管理学/经济学/…）`, '修改分类', {
      confirmButtonText: '保存', cancelButtonText: '取消',
      inputValue: row.category || '',
    })
    await setBookCategory(row.id, value.trim() || '其他')
    ElMessage.success('已保存')
    loadBooks()
  } catch { /* 取消 */ }
}

const runDeep = async (row) => {
  try {
    const resp = await deepAnalyze(row.id)
    ElMessage.success('深度分析已启动')
    notifyTaskSubmitted()
    loadBooks()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const readBook = (row) => {
  if (row.reading_status === 'unread') updateArchiveProfile(row.id, { reading_status: 'reading' }).catch(() => {})
  router.push('/reader/' + row.id)
}

const openWorkbench = (row) => router.push({ path: '/literature-workbench', query: { bookId: row.id } })

const toggleFavorite = async (row) => {
  try {
    const next = !row.favorite
    await updateArchiveProfile(row.id, { favorite: next })
    row.favorite = next
  } catch (e) { ElMessage.error(e.message) }
}

const saveArchive = async () => {
  try {
    const a = currentBook.value.archive
    currentBook.value.archive = await updateArchiveProfile(currentBook.value.id, {
      authors: a.authors || null, journal: a.journal || null,
      published_year: a.published_year || null, doi: a.doi || null,
      reading_status: a.reading_status,
    })
    ElMessage.success('归档信息已保存')
    loadBooks()
  } catch (e) { ElMessage.error(e.message) }
}

const openBook = async (row) => {
  try {
    const detail = await getBook(row.id)
    currentBook.value = detail
    chapterTree.value = detail.chapters
    detailVisible.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const doSearch = async () => {
  if (!searchQ.value.trim()) return
  searching.value = true
  try {
    const params = { q: searchQ.value, page_size: 20 }
    if (searchCategory.value) params.category = searchCategory.value
    results.value = await searchBooks(params)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    searching.value = false
  }
}

const searchKeyword = (kw) => {
  // 关键词芯片点击：赋值后立即搜索（不用事件对象，避免被当作 kw 传入）
  searchQ.value = kw
  doSearch()
}

const viewOriginal = async (item) => {
  const book = books.value.find((b) => b.id === item.book_id)
  const ps = item.page_start || item.page || null
  const pe = item.page_end || item.page || null
  if (!originalViewerComponent.value) {
    originalViewerComponent.value = (await import('../components/OriginalViewer.vue')).default
    await nextTick()
  }
  originalViewer.value?.open({
    bookId: item.book_id,
    chunkId: item.chunk_id,
    chapter: item.chapter_title || '',
    pageStart: ps,
    pageEnd: pe,
    bookType: book?.file_type || 'pdf',
  })
}

onMounted(loadBooks)
</script>

<style scoped>
.library-page { max-width: 1680px; margin: 0 auto; }
.library-workspace{min-width:0}.drawer-eyebrow{margin-bottom:5px;font-size:9px;letter-spacing:2px;color:#9a7a58}.paper-detail{padding-bottom:28px}.detail-actions{position:sticky;top:0;z-index:2;display:flex;padding:2px 0 14px;background:#fff}.paper-detail :deep(.el-tree){padding:8px 4px 16px;border-radius:10px;background:#f7f3ea}
.library-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 20px 24px; margin-bottom: 14px; color: #f5f0e8; background: linear-gradient(120deg, rgba(28,52,54,.96), rgba(67,85,72,.9)); border: 1px solid rgba(245,240,232,.16); border-radius: 16px; box-shadow: 0 12px 32px rgba(10,24,25,.2); }
.library-hero h1 { margin: 4px 0 6px; font-family: Georgia, 'STSong', serif; font-size: 28px; letter-spacing: 2px; }
.library-hero p { color: rgba(245,240,232,.72); }
.eyebrow { color: #d3b58f; font-size: 10px; letter-spacing: 2.5px; }
.hero-stats { display: flex; gap: 28px; }
.hero-stats div { display: flex; flex-direction: column; text-align: right; }
.hero-stats strong { font: 700 24px Georgia, serif; color: #f5f0e8; }
.hero-stats span { font-size: 11px; color: rgba(245,240,232,.58); }
.card-header, .header-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.header-title { display: flex; align-items: baseline; gap: 8px; font-weight: 700; }
.header-title small { color: var(--el-text-color-secondary); font-weight: 400; }
.library-filters { display: grid; grid-template-columns: minmax(220px, 1fr) 130px 120px auto; gap: 10px; align-items: center; margin-bottom: 12px; }
.paper-title { font-weight: 650; color: var(--el-text-color-primary); line-height: 1.35; }
.paper-meta { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.star { cursor: pointer; color: #c8c2b7; font-size: 18px; transition: .2s; }
.star.active { color: #c08a3e; }
.archive-grid { display: grid; gap: 9px; }
.archive-grid label { display: grid; grid-template-columns: 42px 1fr; gap: 8px; align-items: center; color: var(--el-text-color-secondary); font-size: 12px; }
.search-filters { display: flex; gap: 8px; margin-bottom: 4px; }
.batch-bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 6px 10px; background: var(--el-fill-color-lighter); border-radius: 6px; }
.batch-tip { font-size: 12px; color: var(--el-text-color-secondary); }
.result-item { margin-top: 8px; }
.result-meta { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.result-chapter { color: var(--el-text-color-secondary); font-size: 12px; }
.result-page { color: var(--el-text-color-secondary); font-size: 12px; }
.result-snippet { font-size: 14px; line-height: 1.6; color: var(--el-text-color-primary); }
.result-count { color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 8px; }
.analysis-section { margin-bottom: 12px; }
.analysis-title { font-size: 13px; font-weight: 600; color: var(--el-text-color-regular); margin-bottom: 6px; }
.analysis-tag { margin: 2px 4px 2px 0; cursor: pointer; }
.keyword-chip {
  display: inline-block; padding: 2px 10px; margin: 2px 4px 2px 0;
  background: var(--el-fill-color-lighter); border-radius: 4px; font-size: 12px;
  color: var(--el-text-color-regular); cursor: pointer; user-select: none;
  border: 1px solid var(--el-border-color-extra-light);
}
.keyword-chip:hover { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.analysis-item { font-size: 13px; line-height: 1.6; margin-bottom: 4px; color: var(--el-text-color-primary); }
.analysis-meta { font-size: 12px; color: var(--el-text-color-secondary); }
.mobile-paper-list{display:none}.mobile-paper-card{padding:14px 0;border-bottom:1px solid var(--el-border-color-lighter)}.mobile-paper-head{display:flex;gap:9px;align-items:flex-start}.mobile-paper-head>div{min-width:0;display:flex;flex-direction:column}.mobile-paper-head b{line-height:1.4;color:#353730}.mobile-paper-head small{margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8a8175}.mobile-paper-tags,.mobile-paper-actions{display:flex;gap:6px;margin-top:10px}.mobile-paper-actions{justify-content:flex-end}
@media (max-width: 900px) {
  .library-hero { align-items: flex-start; flex-direction: column; }
  .hero-stats { width: 100%; justify-content: space-between; }
  .hero-stats div { text-align: left; }
  .library-filters { grid-template-columns: 1fr 1fr; }
  .header-actions{width:100%;justify-content:flex-start}.library-page :deep(.el-table){font-size:12px}
}
@media (max-width: 620px){.library-hero{padding:16px}.library-hero h1{font-size:23px}.library-filters{grid-template-columns:1fr}.hero-stats{gap:12px}.hero-stats strong{font-size:20px}.header-actions :deep(.el-button){margin-left:0}.paper-meta{max-width:220px}.library-page :deep(.el-table){display:none}.mobile-paper-list{display:block}}
</style>
