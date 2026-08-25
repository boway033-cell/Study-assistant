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
      <aside class="bookshelf-panel">
        <div class="bookshelf-head"><div><b>我的书架</b><small>虚拟归档，不移动文件</small></div><el-button class="create-shelf-head" plain size="small" @click="createBookshelf(null)">＋ 新建</el-button></div>
        <button class="shelf-static" :class="{active:selectedShelf==='all'}" @click="selectedShelf='all'"><span>全部资料</span><b>{{ books.length }}</b></button>
        <button class="shelf-static" :class="{active:selectedShelf==='unfiled'}" @click="selectedShelf='unfiled'"><span>未归档</span><b>{{ books.filter(b=>!b.shelf_ids?.length).length }}</b></button>
        <el-tree v-if="shelves.length" :data="shelfTree" node-key="id" default-expand-all :expand-on-click-node="false" class="shelf-tree">
          <template #default="{data}"><div class="shelf-node" :class="{active:selectedShelf===data.id}" @click.stop="selectedShelf=data.id"><span><i :style="{background:data.color}"></i>{{ data.name }}</span><div><small>{{ data.book_count }}</small><el-dropdown trigger="click" @command="cmd=>shelfCommand(cmd,data)"><el-button text size="small">···</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="child">新建子书架</el-dropdown-item><el-dropdown-item command="rename">重命名</el-dropdown-item><el-dropdown-item command="delete" divided>删除书架</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div></template>
        </el-tree>
        <div v-else class="shelf-empty"><span>还没有自建书架</span><small>按主题、课程或项目整理文献</small><el-button plain size="small" @click="createBookshelf(null)">建立第一个书架</el-button></div>
      </aside>
      <section>
        <el-card shadow="never" class="materials-card">
          <template #header>
            <div class="card-header">
              <div class="header-title">
                <div>
                  <span>我的资料</span>
                  <small>{{ currentShelfName }} · {{ filteredBooks.length }} 篇</small>
                </div>
                <el-tag v-if="activeFilterCount" size="small" effect="plain">{{ activeFilterCount }} 项筛选</el-tag>
              </div>
              <div class="header-actions">
              <el-upload
                :show-file-list="false"
                :before-upload="handleUpload"
                accept=".pdf,.docx,.pptx"
                :disabled="uploading"
              >
                <el-button type="primary" :loading="uploading">
                  {{ uploading ? '导入中…' : '＋ 导入文献' }}
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
                <el-button plain :loading="uploading">
                  {{ uploading ? '批量导入中…' : '批量导入' }}
                </el-button>
              </el-upload>
              <el-button plain :loading="classifying" @click="classifyAll">智能归类</el-button>
              </div>
            </div>
          </template>

          <div class="library-filters">
            <el-input v-model="libraryQ" clearable placeholder="搜索当前书架中的题名、作者、期刊或 DOI" />
            <el-select v-model="libraryCategory" clearable placeholder="全部分类">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
            <el-select v-model="readingFilter" clearable placeholder="阅读状态">
              <el-option label="未读" value="unread" />
              <el-option label="阅读中" value="reading" />
              <el-option label="已读完" value="read" />
            </el-select>
            <el-checkbox v-model="favoriteOnly">仅收藏</el-checkbox>
            <el-button v-if="activeFilterCount" link class="clear-filter" @click="clearLibraryFilters">清除筛选</el-button>
          </div>
          <div v-if="batchFiles.length" class="import-queue"><div><b>已选择 {{ batchFiles.length }} 个文件</b><small>确认后将进入全局任务中心依次解析</small></div><el-button type="primary" :loading="uploading" @click="submitBatch">开始导入</el-button><el-button link @click="batchFiles=[]">取消</el-button></div>
          <div v-if="selectedBookIds.length" class="shelf-batch"><span>已选择 {{ selectedBookIds.length }} 篇</span><el-select v-model="targetShelfId" placeholder="选择目标书架" size="small"><el-option v-for="s in shelves" :key="s.id" :label="s.name" :value="s.id" /></el-select><el-button type="primary" size="small" :disabled="!targetShelfId" @click="assignSelectedToShelf">加入书架</el-button></div>

          <div class="paper-list" v-loading="loading">
            <div v-if="filteredBooks.length" class="paper-list-head">
              <el-checkbox :model-value="allFilteredSelected" @change="toggleAllFiltered" />
              <span>文献与来源</span><span>阅读进度</span><span>知识加工</span><span></span>
            </div>
            <article v-for="row in filteredBooks" :key="row.id" class="paper-row">
              <div class="paper-select"><el-checkbox :model-value="selectedBookIds.includes(row.id)" @change="checked=>toggleBookSelection(row.id,checked)" /><button class="star" :class="{ active: row.favorite }" title="收藏" @click="toggleFavorite(row)">★</button></div>
              <div class="paper-identity" @click="openBook(row)">
                <div class="paper-title">{{ row.title }}</div>
                <div class="paper-meta">{{ [row.authors, row.journal, row.published_year].filter(Boolean).join(' · ') || '等待补充书目信息' }}</div>
                <div class="paper-facts"><span>{{ (row.file_type || 'file').toUpperCase() }}</span><span v-if="row.total_pages">{{ row.total_pages }} 页</span><span v-if="row.quiz_count">{{ row.quiz_count }} 道题</span></div>
              </div>
              <div class="paper-reading">
                <el-tag size="small" :type="readingTagType(row.reading_status)" effect="plain">{{ readingLabel(row.reading_status) }}</el-tag>
                <span v-if="row.status === 'ready'" class="muted-state">结构已就绪</span>
                <span v-else-if="row.status === 'failed'" class="danger-state">解析失败</span>
                <span v-else-if="row.status === 'needs_ocr'" class="warning-state">等待 OCR</span>
                <el-tooltip v-else :content="row.task_message || '解析中…'" placement="top"><span class="muted-state">{{ row.task_message ? extractProgress(row.task_message) : '解析中' }}</span></el-tooltip>
              </div>
              <div class="paper-knowledge">
                <button class="category-link" @click="editCategory(row)">{{ row.category || '添加分类' }}</button>
                <span v-if="row.deep_status === 'done'" class="deep-done">已完成深度分析</span>
                <span v-else-if="row.deep_status === 'running'" class="warning-state">深度分析中</span>
                <button v-else class="analysis-link" @click="runDeep(row)">开始深度分析</button>
              </div>
              <div class="paper-actions">
                <el-button type="primary" plain size="small" @click="readBook(row)">{{ row.reading_status === 'reading' ? '继续阅读' : '阅读' }}</el-button>
                <el-dropdown trigger="click" @command="cmd=>paperCommand(cmd,row)">
                  <el-button size="small">更多 ···</el-button>
                  <template #dropdown><el-dropdown-menu><el-dropdown-item command="detail">资料详情</el-dropdown-item><el-dropdown-item command="deck">生成文献汇报</el-dropdown-item><el-dropdown-item command="delete" divided>删除资料</el-dropdown-item></el-dropdown-menu></template>
                </el-dropdown>
              </div>
            </article>
            <el-empty v-if="!filteredBooks.length && !loading" description="当前书架暂无资料，试试清除筛选或导入文献" :image-size="72" />
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
import { listBooks, uploadBook, uploadBookBatch, deleteBook, getBook, searchBooks, classifyAllBooks, setBookCategory, deepAnalyze, updateArchiveProfile,
  listShelves, createShelf, updateShelf, deleteShelf as deleteShelfApi, putShelfBooks } from '../api'
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
const shelves = ref([])
const selectedShelf = ref('all')
const selectedBookIds = ref([])
const targetShelfId = ref(null)
const currentBook = ref(null)
const detailVisible = ref(false)
const chapterTree = ref([])
const originalViewer = ref(null)
const originalViewerComponent = shallowRef(null)
const shelfTree = computed(() => {
  const nodes = new Map(shelves.value.map(s => [s.id, {...s, children:[]}]))
  const roots = []
  for (const node of nodes.values()) {
    if (node.parent_id && nodes.has(node.parent_id)) nodes.get(node.parent_id).children.push(node)
    else roots.push(node)
  }
  return roots
})
const filteredBooks = computed(() => {
  const q = libraryQ.value.trim().toLowerCase()
  return books.value.filter((b) => {
    const hay = [b.title, b.authors, b.journal, b.doi].filter(Boolean).join(' ').toLowerCase()
    const inShelf = selectedShelf.value === 'all' || (selectedShelf.value === 'unfiled' ? !b.shelf_ids?.length : b.shelf_ids?.includes(selectedShelf.value))
    return inShelf && (!q || hay.includes(q))
      && (!libraryCategory.value || b.category === libraryCategory.value)
      && (!readingFilter.value || b.reading_status === readingFilter.value)
      && (!favoriteOnly.value || b.favorite)
  })
})
const currentShelfName = computed(() => {
  if (selectedShelf.value === 'all') return '全部资料'
  if (selectedShelf.value === 'unfiled') return '未归档'
  return shelves.value.find(s => s.id === selectedShelf.value)?.name || '当前书架'
})
const activeFilterCount = computed(() => [libraryQ.value.trim(), libraryCategory.value, readingFilter.value, favoriteOnly.value].filter(Boolean).length)
const allFilteredSelected = computed(() => filteredBooks.value.length > 0 && filteredBooks.value.every(book => selectedBookIds.value.includes(book.id)))
const readingLabel = (status) => ({ unread: '未读', reading: '阅读中', read: '已读完' }[status] || '未读')
const readingTagType = (status) => ({ unread: 'info', reading: 'warning', read: 'success' }[status] || 'info')
const clearLibraryFilters = () => { libraryQ.value = ''; libraryCategory.value = null; readingFilter.value = null; favoriteOnly.value = false }
const toggleBookSelection = (bookId, checked) => {
  selectedBookIds.value = checked ? [...new Set([...selectedBookIds.value, bookId])] : selectedBookIds.value.filter(id => id !== bookId)
}
const toggleAllFiltered = (checked) => {
  const visibleIds = filteredBooks.value.map(book => book.id)
  selectedBookIds.value = checked
    ? [...new Set([...selectedBookIds.value, ...visibleIds])]
    : selectedBookIds.value.filter(id => !visibleIds.includes(id))
}

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
const loadShelves = async () => { shelves.value = await listShelves() }
const createBookshelf = async (parentId) => {
  try {
    const parent = parentId ? shelves.value.find(s => s.id === parentId) : null
    const { value } = await ElMessageBox.prompt(
      parent ? `将在“${parent.name}”下建立子书架` : '按课程、主题或研究项目命名，之后可随时修改。',
      parent ? '新建子书架' : '新建书架',
      { confirmButtonText: '创建并进入', cancelButtonText: '取消', inputPlaceholder: '例如：公共管理课程', inputPattern: /^\s*\S(?:.{0,118}\S)?\s*$/, inputErrorMessage: '请输入 1–120 个字符的书架名称', autofocus: true }
    )
    const created = await createShelf({ name: value.trim(), parent_id: parentId, color: '#8B5A2B' })
    await loadShelves()
    selectedShelf.value = created.id
    ElMessage.success(`书架“${created.name}”已创建`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('创建书架失败：' + e.message)
  }
}
const shelfCommand = async (command,shelf) => { if(command==='child') return createBookshelf(shelf.id); if(command==='rename'){try{const {value}=await ElMessageBox.prompt('输入新名称','重命名书架',{inputValue:shelf.name});await updateShelf(shelf.id,{name:value.trim()});await loadShelves()}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.message)}} else if(command==='delete'){try{await ElMessageBox.confirm('只删除书架归属，不会删除其中的文献。','删除书架',{type:'warning'});await deleteShelfApi(shelf.id);if(selectedShelf.value===shelf.id)selectedShelf.value='all';await Promise.all([loadShelves(),loadBooks()]);ElMessage.success('书架已删除，文献仍在资料库')}catch(e){if(e!=='cancel'&&e!=='close')ElMessage.error(e.message)}} }
const assignSelectedToShelf = async () => { if(!targetShelfId.value || !selectedBookIds.value.length)return; try{await putShelfBooks(targetShelfId.value,selectedBookIds.value,'add');await Promise.all([loadBooks(),loadShelves()]);selectedBookIds.value=[];ElMessage.success('已加入书架；原文件没有移动或复制')}catch(e){ElMessage.error(e.message)} }

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

const paperCommand = async (command, row) => {
  if (command === 'detail') return openBook(row)
  if (command === 'deck') return openWorkbench(row)
  if (command === 'delete') {
    try {
      await ElMessageBox.confirm(`确认删除《${row.title}》？其解析、笔记与分析数据也会被删除。`, '删除资料', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
      await removeBook(row)
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message)
    }
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

onMounted(() => Promise.all([loadBooks(), loadShelves()]))
</script>

<style scoped>
.library-page { max-width: 1680px; margin: 0 auto; }
.library-workspace{display:grid;grid-template-columns:228px minmax(0,1fr);align-items:start;gap:16px;min-width:0}.bookshelf-panel{position:sticky;top:76px;padding:14px;border:1px solid #e5e7eb;border-radius:14px;background:#f7f2e9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.bookshelf-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.bookshelf-head>div{display:flex;flex-direction:column}.bookshelf-head small{margin-top:3px;color:#8b8174;font-size:10px}.create-shelf-head{padding-inline:9px}.shelf-static{display:flex;width:100%;align-items:center;justify-content:space-between;padding:9px 10px;border:0;border-radius:8px;color:#57534b;background:transparent;cursor:pointer;transition:background .16s ease,color .16s ease,transform .16s ease}.shelf-static:hover{color:#6f4721;background:#f0e7d9;transform:translateX(2px)}.shelf-static.active{color:#6f4721;background:#ebe1d1}.shelf-tree{margin-top:5px;background:transparent}.shelf-tree :deep(.el-tree-node__content){height:36px;background:transparent}.shelf-node{display:flex;flex:1;min-width:0;align-items:center;justify-content:space-between;padding:4px 5px;border-radius:7px}.shelf-node.active{color:#6f4721;background:#ebe1d1}.shelf-node>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.shelf-node i{display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%}.shelf-node>div{display:flex;align-items:center}.shelf-node small{color:#978a78}.shelf-empty{display:flex;align-items:center;flex-direction:column;margin-top:12px;padding:16px 10px;border:1px dashed #d8c9b5;border-radius:10px;color:#776b5d;background:rgba(255,255,255,.28);text-align:center}.shelf-empty small{margin:4px 0 11px;color:#9a8e7f;font-size:10px}.shelf-batch,.import-queue{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:9px 12px;margin-bottom:10px;border:1px solid #e5e7eb;border-radius:9px;background:#faf7f0;box-shadow:0 1px 2px rgba(15,23,42,.04)}.shelf-batch :deep(.el-select){width:180px}.import-queue{justify-content:flex-start;border-color:#dfc9a9;background:#fbf4e7}.import-queue>div{display:flex;flex:1;flex-direction:column}.import-queue small{margin-top:2px;color:#8b8174}.drawer-eyebrow{margin-bottom:5px;font-size:9px;letter-spacing:2px;color:#9a7a58}.paper-detail{padding-bottom:28px}.detail-actions{position:sticky;top:0;z-index:2;display:flex;padding:2px 0 14px;background:#fff}.paper-detail :deep(.el-tree){padding:8px 4px 16px;border-radius:10px;background:#f7f3ea}
.library-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 20px 24px; margin-bottom: 14px; color: #f5f0e8; background: linear-gradient(120deg, rgba(28,52,54,.96), rgba(67,85,72,.9)); border: 1px solid rgba(245,240,232,.16); border-radius: 16px; box-shadow: 0 12px 32px rgba(10,24,25,.2); }
.library-hero h1 { margin: 4px 0 6px; font-family: Georgia, 'STSong', serif; font-size: 28px; letter-spacing: 2px; }
.library-hero p { color: rgba(245,240,232,.72); }
.eyebrow { color: #d3b58f; font-size: 10px; letter-spacing: 2.5px; }
.hero-stats { display: flex; gap: 28px; }
.hero-stats div { display: flex; flex-direction: column; text-align: right; }
.hero-stats strong { font: 700 24px Georgia, serif; color: #f5f0e8; }
.hero-stats span { font-size: 11px; color: rgba(245,240,232,.58); }
.materials-card{border-color:#e5e7eb;box-shadow:0 1px 2px rgba(15,23,42,.06)}
.materials-card :deep(.el-card__header){padding:16px 18px;border-bottom-color:#e8dfd1}.materials-card :deep(.el-card__body){padding:14px 18px 8px}
.card-header, .header-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.header-title { display: flex; align-items:center;gap:10px;font-weight:700}.header-title>div{display:flex;flex-direction:column;gap:3px}.header-title span{font-size:16px}.header-title small { color: var(--el-text-color-secondary); font-weight: 400;font-size:11px }
.library-filters { display: grid; grid-template-columns: minmax(260px, 1fr) 132px 122px auto auto; gap: 10px; align-items: center; margin-bottom: 14px; }
.clear-filter{justify-self:end}
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
.paper-list{min-height:160px}.paper-list-head,.paper-row{display:grid;grid-template-columns:58px minmax(260px,1fr) 112px 140px 164px;column-gap:14px;align-items:center}.paper-list-head{padding:8px 12px;color:#918678;font-size:11px;border-top:1px solid #eee6da;border-bottom:1px solid #e5dbcc;background:#f8f4ec}.paper-list-head>span:last-child{text-align:right}.paper-row{position:relative;min-height:92px;padding:12px;border-bottom:1px solid #e8dfd2;transition:background .16s ease,box-shadow .16s ease,transform .16s ease}.paper-row:hover{z-index:1;background:#fcfaf5;box-shadow:0 2px 10px rgba(85,65,42,.07);transform:translateY(-1px)}.paper-select{display:flex;align-items:center;gap:12px}.star{padding:0;border:0;background:transparent}.paper-identity{min-width:0;cursor:pointer}.paper-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;font-family:'Noto Serif SC','STSong',serif;font-size:14px}.paper-facts{display:flex;gap:10px;margin-top:7px;color:#9b8f80;font-size:10px}.paper-facts span+span:before{content:'·';margin-right:10px}.paper-reading,.paper-knowledge{display:flex;align-items:flex-start;flex-direction:column;gap:7px;font-size:11px}.muted-state{color:#8a8175}.danger-state{color:#b4473d}.warning-state{color:#a86e27}.category-link,.analysis-link{max-width:100%;padding:0;border:0;background:transparent;color:#765331;font-size:11px;cursor:pointer;text-align:left}.analysis-link{color:#8d755a}.deep-done{color:#47806b}.paper-actions{display:flex;align-items:center;justify-content:flex-end;gap:7px}.paper-actions :deep(.el-button+.el-button){margin-left:0}
@media (max-width: 900px) {
  .library-workspace{grid-template-columns:1fr}.bookshelf-panel{position:static}.shelf-tree{max-height:190px;overflow:auto}
  .library-hero { align-items: flex-start; flex-direction: column; }
  .hero-stats { width: 100%; justify-content: space-between; }
  .hero-stats div { text-align: left; }
  .library-filters { grid-template-columns: 1fr 1fr; }
  .header-actions{width:100%;justify-content:flex-start}.paper-list-head{display:none}.paper-row{grid-template-columns:48px minmax(0,1fr) 140px}.paper-reading{grid-column:2;margin-top:10px;flex-direction:row;align-items:center}.paper-knowledge{grid-column:3;grid-row:1 / span 2}.paper-actions{grid-column:2 / -1;margin-top:10px;justify-content:flex-start}
}
@media (max-width: 620px){.library-hero{padding:16px}.library-hero h1{font-size:23px}.library-filters{grid-template-columns:1fr}.hero-stats{gap:12px}.hero-stats strong{font-size:20px}.header-actions :deep(.el-button){margin-left:0}.paper-row{grid-template-columns:38px minmax(0,1fr);padding:14px 4px}.paper-knowledge,.paper-reading,.paper-actions{grid-column:2}.paper-knowledge{grid-row:auto;margin-top:9px;flex-direction:row;align-items:center}.paper-actions{flex-wrap:wrap}.paper-meta{max-width:100%}.import-queue{align-items:flex-start;flex-wrap:wrap}.import-queue>div{flex-basis:100%}.materials-card :deep(.el-card__body){padding:12px}}
</style>
