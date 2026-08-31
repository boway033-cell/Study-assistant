<template>
  <div class="library-page study-page">
    <StudyCommandBar compact class="library-commandbar" :title="currentShelfName" description="筛选、归档并进入阅读；解析状态集中显示在任务中心。">
      <div class="library-summary" aria-label="知识库概况">
        <div><strong>{{ libraryTotal }}</strong><span>全部资料</span></div>
        <div><strong>{{ readingCount }}</strong><span>阅读中</span></div>
        <div><strong>{{ attentionCount }}</strong><span>待处理</span></div>
      </div>
      <template #actions><div class="library-primary-actions">
        <el-button :type="searchPanelOpen ? 'primary' : ''" plain @click="searchPanelOpen = !searchPanelOpen">全文检索</el-button>
        <el-upload :show-file-list="false" :auto-upload="false" :on-change="handleBatchSelect" multiple accept=".pdf,.docx,.pptx" :disabled="uploading">
          <el-button plain :loading="uploading">批量导入</el-button>
        </el-upload>
        <el-upload :show-file-list="false" :before-upload="handleUpload" accept=".pdf,.docx,.pptx" :disabled="uploading">
          <el-button type="primary" :loading="uploading">{{ uploading ? '正在导入…' : '＋ 导入文献' }}</el-button>
        </el-upload>
      </div></template>
    </StudyCommandBar>
    <div v-if="batchFiles.length" class="import-queue library-import-queue"><div><b>已选择 {{ batchFiles.length }} 个文件</b><small>确认后进入全局任务中心依次解析，你可以继续使用资料库。</small></div><el-button type="primary" :loading="uploading" @click="submitBatch">导入所选文件</el-button><el-button link @click="batchFiles=[]">取消选择</el-button></div>
    <button class="mobile-shelf-toggle" type="button" @click="shelfPanelOpen=!shelfPanelOpen"><span>范围：{{ currentShelfName }}</span><b>{{ shelfPanelOpen ? '收起' : '切换' }}</b></button>
    <div class="library-workspace">
      <aside class="bookshelf-panel" :class="{ 'mobile-open': shelfPanelOpen }">
        <div class="bookshelf-section-label">智能视图</div>
        <button class="shelf-static" :class="{active:selectedShelf==='all'}" @click="chooseShelf('all')"><span>全部资料</span><b>{{ libraryTotal }}</b></button>
        <button class="shelf-static" :class="{active:selectedShelf==='reading'}" @click="chooseShelf('reading')"><span>阅读中</span><b>{{ readingCount }}</b></button>
        <button class="shelf-static" :class="{active:selectedShelf==='favorite'}" @click="chooseShelf('favorite')"><span>我的收藏</span><b>{{ favoriteCount }}</b></button>
        <button class="shelf-static" :class="{active:selectedShelf==='attention'}" @click="chooseShelf('attention')"><span>待处理</span><b>{{ attentionCount }}</b></button>
        <button class="shelf-static" :class="{active:selectedShelf==='unfiled'}" @click="chooseShelf('unfiled')"><span>未归档</span><b>{{ unfiledCount }}</b></button>
        <div class="bookshelf-head"><div><b>我的书架</b><small>按课程、主题或项目归档</small></div><el-button class="create-shelf-head" plain size="small" @click="createBookshelf(null)">＋ 新建</el-button></div>
        <el-tree v-if="shelves.length" :data="shelfTree" node-key="id" default-expand-all :expand-on-click-node="false" class="shelf-tree">
          <template #default="{data}"><div class="shelf-node" :class="{active:selectedShelf===data.id}" @click.stop="chooseShelf(data.id)"><span><i :style="{background:data.color}"></i>{{ data.name }}</span><div><small>{{ data.book_count }}</small><el-dropdown trigger="click" @command="cmd=>shelfCommand(cmd,data)"><el-button text size="small">···</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="child">新建子书架</el-dropdown-item><el-dropdown-item command="rename">重命名</el-dropdown-item><el-dropdown-item command="delete" divided>删除书架</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div></template>
        </el-tree>
        <div v-else class="shelf-empty"><span>还没有自建书架</span><small>按主题、课程或项目整理文献</small><el-button plain size="small" @click="createBookshelf(null)">建立第一个书架</el-button></div>
      </aside>
      <main class="library-main">
        <el-card shadow="never" class="materials-card">
          <template #header>
            <div class="card-header">
              <div class="header-title">
                <div>
                  <span>{{ currentShelfName }}</span>
                  <small>{{ filteredBooks.length }} 篇 · 点击条目查看结构与加工状态</small>
                </div>
                <el-tag v-if="activeFilterCount" size="small" effect="plain">{{ activeFilterCount }} 项筛选</el-tag>
              </div>
              <div class="header-actions"><el-button plain :loading="classifying" @click="classifyAll">智能归类</el-button></div>
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
            <el-select v-model="sortBy" aria-label="文献排序" placeholder="排序方式">
              <el-option label="自定义顺序" value="custom" />
              <el-option label="最近导入" value="newest" />
              <el-option label="最早导入" value="oldest" />
              <el-option label="题名 A–Z" value="title_asc" />
              <el-option label="题名 Z–A" value="title_desc" />
              <el-option label="作者 A–Z" value="author_asc" />
              <el-option label="年份从新到旧" value="year_desc" />
              <el-option label="年份从旧到新" value="year_asc" />
              <el-option label="最近阅读" value="last_read" />
              <el-option label="阅读进度" value="progress_desc" />
            </el-select>
            <el-checkbox v-model="favoriteOnly">仅收藏</el-checkbox>
            <el-button v-if="activeFilterCount" link class="clear-filter" @click="clearLibraryFilters">清除筛选</el-button>
          </div>
          <div v-if="sortBy === 'custom'" class="reorder-guide" :class="{ disabled: !canDragSort }">
            <span class="drag-dots">⠿</span>
            <span v-if="canDragSort">拖动任意文献调整{{ typeof selectedShelf === 'number' ? '当前书架' : '资料库' }}顺序，放开后自动保存。</span>
            <span v-else-if="activeFilterCount">清除筛选后即可拖拽排序。</span>
            <span v-else>“自定义顺序”仅可在全部资料或自建书架中拖拽调整。</span>
          </div>
          <div v-if="selectedBookIds.length" class="shelf-batch"><span>已选择 {{ selectedBookIds.length }} 篇</span><el-select v-model="targetShelfId" placeholder="选择目标书架" size="small"><el-option v-for="s in shelves" :key="s.id" :label="s.name" :value="s.id" /></el-select><el-button type="primary" size="small" :disabled="!targetShelfId" @click="assignSelectedToShelf">加入书架</el-button></div>

          <div class="paper-list" v-loading="loading">
            <div v-if="filteredBooks.length" class="paper-list-head">
              <el-checkbox :model-value="allFilteredSelected" @change="toggleAllFiltered" />
              <span>文献与来源</span><span>阅读进度</span><span>知识加工</span><span></span>
            </div>
            <StudyListRow v-for="row in filteredBooks" :key="row.id" class="paper-row" :class="{ 'is-dragging': draggingBookId === row.id, 'drop-before': dropTargetId === row.id && dropPosition === 'before', 'drop-after': dropTargetId === row.id && dropPosition === 'after' }" :active="currentBook?.id===row.id" :draggable="canDragSort" tabindex="0" @click="selectBook(row)" @keydown.enter="selectBook(row)" @dragstart="onBookDragStart(row, $event)" @dragover="onBookDragOver(row, $event)" @drop="onBookDrop(row, $event)" @dragend="resetBookDrag">
              <div class="paper-select" @click.stop><span class="drag-handle" :class="{ enabled: canDragSort }" :title="canDragSort ? '按住并拖动调整顺序' : '选择自定义顺序后可拖动'" aria-hidden="true">⠿</span><el-checkbox :model-value="selectedBookIds.includes(row.id)" @change="checked=>toggleBookSelection(row.id,checked)" /><button class="star" :class="{ active: row.favorite }" title="收藏" @click="toggleFavorite(row)">★</button></div>
              <div class="paper-identity">
                <div class="paper-title">{{ row.title }}</div>
                <div class="paper-meta">{{ [row.authors, row.journal, displayYear(row.published_year)].filter(Boolean).join(' · ') || '等待补充书目信息' }}<span class="publication-state">{{ publicationLabel(row.publication_status) }}</span></div>
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
                <button class="category-link" @click.stop="editCategory(row)">{{ row.category || '添加分类' }}</button>
                <span v-if="row.deep_status === 'done'" class="deep-done">已完成深度分析</span>
                <span v-else-if="row.deep_status === 'running'" class="warning-state">深度分析中</span>
                <button v-else class="analysis-link" @click.stop="runDeep(row)">开始深度分析</button>
              </div>
              <div class="paper-actions" @click.stop>
                <el-button type="primary" plain size="small" @click="readBook(row)">{{ row.reading_status === 'reading' ? '继续阅读' : '开始阅读' }}</el-button>
                <el-dropdown trigger="click" @command="cmd=>paperCommand(cmd,row)">
                  <el-button size="small">更多 ···</el-button>
                  <template #dropdown><el-dropdown-menu><el-dropdown-item command="detail">资料详情</el-dropdown-item><el-dropdown-item command="deck">生成文献汇报</el-dropdown-item><el-dropdown-item command="delete" divided>删除资料</el-dropdown-item></el-dropdown-menu></template>
                </el-dropdown>
              </div>
            </StudyListRow>
            <StudyEmptyState v-if="!filteredBooks.length && !loading" compact :title="activeFilterCount ? '没有符合当前筛选的资料' : '当前范围还没有资料'" :description="activeFilterCount ? '清除部分筛选条件，或切换到其他书架。' : '导入 PDF、Word 或 PowerPoint 后会在这里建立可检索档案。'"><template #actions><el-button v-if="activeFilterCount" type="primary" plain @click="clearLibraryFilters">清除筛选</el-button></template></StudyEmptyState>
            <el-button v-if="books.length < libraryTotal" class="library-load-more" :loading="loading" @click="loadBooks(false)">加载更多（{{ books.length }}/{{ libraryTotal }}）</el-button>
          </div>
        </el-card>

        <el-card v-show="searchPanelOpen" shadow="never" class="fulltext-card">
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
      </main>

      <StudyInspector class="library-inspector" title="资料检查器" :closable="!!currentBook" @close="currentBook=null">
        <template v-if="currentBook">
          <div class="inspector-scroll">
            <div class="inspector-type">{{ (currentBook.file_type || 'file').toUpperCase() }} · {{ publicationLabel(currentBook.archive?.publication_status) }}</div>
            <h2>{{ currentBook.title }}</h2>
            <p class="inspector-meta">{{ [currentBook.archive?.authors, currentBook.archive?.journal, displayYear(currentBook.archive?.published_year)].filter(Boolean).join(' · ') || '书目信息待补充' }}</p>
            <div class="trust-row"><el-tag size="small" effect="plain">{{ visibilityLabel(currentBook.archive?.visibility) }}</el-tag><el-tag size="small" :type="currentBook.archive?.demo_allowed ? 'success' : 'info'" effect="plain">{{ currentBook.archive?.demo_allowed ? '允许演示' : '禁止演示' }}</el-tag><span>元数据 {{ confidenceLabel(currentBook.archive?.metadata_confidence) }}</span></div>
            <div class="inspector-actions"><el-button type="primary" @click="readBook(currentBook)">进入阅读</el-button><el-button @click="openWorkbench(currentBook)">生成汇报</el-button></div>
            <section class="inspector-section">
              <div class="inspector-section-title"><b>阅读与加工</b><el-tag size="small" :type="readingTagType(currentBook.archive?.reading_status)">{{ readingLabel(currentBook.archive?.reading_status) }}</el-tag></div>
              <div class="inspector-facts"><span><b>{{ currentBook.total_pages || '—' }}</b>页数</span><span><b>{{ chapterTree.length }}</b>顶级章节</span><span><b>{{ currentBook.analysis?.keywords?.length || 0 }}</b>关键词</span></div>
              <p class="inspector-status" :class="currentBook.status">{{ detailStatusText }}</p>
            </section>
            <section class="inspector-section inspector-chapters">
              <div class="inspector-section-title"><b>文献结构</b><span>{{ chapterTree.length ? '选择章节可在阅读器定位' : '暂无章节' }}</span></div>
              <el-tree :data="chapterPreview" :props="{ label: 'title', children: 'children' }" :default-expand-all="false" empty-text="暂无章节" />
            </section>
            <section v-if="currentBook.analysis?.keywords?.length" class="inspector-section">
              <div class="inspector-section-title"><b>关键词</b><span>点击进行全文检索</span></div>
              <button v-for="keyword in currentBook.analysis.keywords.slice(0, 10)" :key="keyword" class="keyword-chip" @click="searchKeyword(keyword)">{{ keyword }}</button>
            </section>
            <el-button plain class="inspector-detail-button" @click="openBook(currentBook)">编辑完整档案</el-button>
          </div>
        </template>
        <div v-else class="inspector-empty"><span>资料检查器</span><b>选择一篇文献查看详情</b><p>这里会显示阅读进度、解析状态、章节结构和知识沉淀入口。</p></div>
      </StudyInspector>

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
              <label>年份<el-input-number v-model="currentBook.archive.published_year" :min="1000" :max="currentYear + 1" size="small" /></label>
              <label>DOI<el-input v-model="currentBook.archive.doi" size="small" /></label>
              <label>发表
                <el-select v-model="currentBook.archive.publication_status" size="small"><el-option label="待确认" value="unknown"/><el-option label="已发表" value="published"/><el-option label="预印本" value="preprint"/><el-option label="投稿中" value="submitted"/><el-option label="未发表" value="unpublished"/></el-select>
              </label>
              <label>可见
                <el-select v-model="currentBook.archive.visibility" size="small"><el-option label="仅自己" value="private"/><el-option label="可分享" value="shareable"/><el-option label="公开" value="public"/></el-select>
              </label>
              <label>演示<el-switch v-model="currentBook.archive.demo_allowed" inline-prompt active-text="允许" inactive-text="禁止" /></label>
              <label>可信<el-slider v-model="currentBook.archive.metadata_confidence" :min="0" :max="1" :step="0.1" /></label>
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
import { ref, shallowRef, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBooks, uploadBook, uploadBookBatch, deleteBook, getBook, searchBooks, classifyAllBooks, setBookCategory, deepAnalyze, updateArchiveProfile,
  listShelves, createShelf, updateShelf, deleteShelf as deleteShelfApi, putShelfBooks, reorderBooks } from '../api'
import { sanitizeHtml } from '../utils/markdown'
import { notifyTaskSubmitted } from '../stores/taskCenter'
import StudyCommandBar from '../components/StudyCommandBar.vue'
import StudyEmptyState from '../components/StudyEmptyState.vue'
import StudyListRow from '../components/StudyListRow.vue'
import StudyInspector from '../components/StudyInspector.vue'

const router = useRouter()
const route = useRoute()
const books = ref([])
const libraryTotal = ref(0)
const libraryPage = ref(1)
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
const sortBy = ref(localStorage.getItem('study-library-sort') || 'custom')
const reordering = ref(false)
const draggingBookId = ref(null)
const dropTargetId = ref(null)
const dropPosition = ref('before')
const shelves = ref([])
const selectedShelf = ref('all')
const selectedBookIds = ref([])
const targetShelfId = ref(null)
const currentBook = ref(null)
const detailVisible = ref(false)
const searchPanelOpen = ref(false)
const compactLibrary = ref(false)
const shelfPanelOpen = ref(false)
const currentYear = new Date().getFullYear()
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
const readingCount = computed(() => books.value.filter(book => book.reading_status === 'reading').length)
const favoriteCount = computed(() => books.value.filter(book => book.favorite).length)
const attentionCount = computed(() => books.value.filter(book => ['failed', 'needs_ocr', 'parsing'].includes(book.status)).length)
const unfiledCount = computed(() => books.value.filter(book => !book.shelf_ids?.length).length)
const chapterPreview = computed(() => chapterTree.value.slice(0, 12).map(({ children, ...chapter }) => chapter))
const filteredBooks = computed(() => {
  const q = libraryQ.value.trim().toLowerCase()
  return books.value.filter((b) => {
    const hay = [b.title, b.authors, b.journal, b.doi].filter(Boolean).join(' ').toLowerCase()
    const inShelf = selectedShelf.value === 'all'
      || (selectedShelf.value === 'unfiled' && !b.shelf_ids?.length)
      || (selectedShelf.value === 'reading' && b.reading_status === 'reading')
      || (selectedShelf.value === 'favorite' && b.favorite)
      || (selectedShelf.value === 'attention' && ['failed', 'needs_ocr', 'parsing'].includes(b.status))
      || (typeof selectedShelf.value === 'number' && b.shelf_ids?.includes(selectedShelf.value))
    return inShelf && (!q || hay.includes(q))
      && (!libraryCategory.value || b.category === libraryCategory.value)
      && (!readingFilter.value || b.reading_status === readingFilter.value)
      && (!favoriteOnly.value || b.favorite)
  })
})
const currentShelfName = computed(() => {
  if (selectedShelf.value === 'all') return '全部资料'
  if (selectedShelf.value === 'unfiled') return '未归档'
  if (selectedShelf.value === 'reading') return '阅读中'
  if (selectedShelf.value === 'favorite') return '我的收藏'
  if (selectedShelf.value === 'attention') return '待处理'
  return shelves.value.find(s => s.id === selectedShelf.value)?.name || '当前书架'
})
const activeFilterCount = computed(() => [libraryQ.value.trim(), libraryCategory.value, readingFilter.value, favoriteOnly.value].filter(Boolean).length)
const canDragSort = computed(() => sortBy.value === 'custom'
  && activeFilterCount.value === 0
  && (selectedShelf.value === 'all' || typeof selectedShelf.value === 'number')
  && filteredBooks.value.length > 1
  && !loading.value
  && !reordering.value)
const allFilteredSelected = computed(() => filteredBooks.value.length > 0 && filteredBooks.value.every(book => selectedBookIds.value.includes(book.id)))
const readingLabel = (status) => ({ unread: '未读', reading: '阅读中', read: '已读完' }[status] || '未读')
const readingTagType = (status) => ({ unread: 'info', reading: 'warning', read: 'success' }[status] || 'info')
const publicationLabel = (status) => ({ unknown: '发表状态待确认', published: '已发表', preprint: '预印本', submitted: '投稿中', unpublished: '未发表' }[status] || '发表状态待确认')
const visibilityLabel = (status) => ({ private: '仅自己可见', shareable: '可分享', public: '公开' }[status] || '仅自己可见')
const confidenceLabel = (value) => value >= .8 ? '已核对' : value >= .5 ? '部分核对' : '待核对'
const displayYear = (value) => value && value <= currentYear + 1 ? value : value ? '年份待核对' : null
const chooseShelf = (shelf) => { selectedShelf.value = shelf; shelfPanelOpen.value = false; loadBooks(true) }
const detailStatusText = computed(() => {
  if (!currentBook.value) return ''
  if (currentBook.value.status === 'ready') return '解析完成，原文、目录与检索结构已就绪。'
  if (currentBook.value.status === 'failed') return `解析没有完成：${currentBook.value.error_msg || '请打开完整档案查看原因并重新处理。'}`
  if (currentBook.value.status === 'needs_ocr') return '该资料需要 OCR。你仍可阅读原文件，也可在任务中心查看处理条件。'
  return currentBook.value.task_message || '正在建立可检索结构，可继续使用其他资料。'
})
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

const loadBooks = async (reset = true) => {
  loading.value = true
  try {
    const page = reset ? 1 : libraryPage.value + 1
    const smart = selectedShelf.value
    const resp = await listBooks({
      page, page_size: 40, q: libraryQ.value.trim() || undefined,
      shelf_id: typeof smart === 'number' ? smart : undefined,
      reading_status: smart === 'reading' ? 'reading' : (readingFilter.value || undefined),
      favorite: smart === 'favorite' || favoriteOnly.value ? true : undefined,
      unfiled: smart === 'unfiled' || undefined,
      statuses: smart === 'attention' ? ['failed','needs_ocr','parsing'] : undefined,
      category: libraryCategory.value || undefined,
      sort_by: sortBy.value,
    })
    books.value = reset ? resp.items : [...books.value, ...resp.items]
    libraryPage.value = page
    libraryTotal.value = resp.total
    // 提取唯一分类列表
    const cats = [...new Set(resp.items.map((b) => b.category).filter(Boolean))]
    categories.value = cats
  } catch (e) {
    ElMessage.error(`无法加载资料库：${e.message}。请确认本地服务已经启动。`)
  } finally {
    loading.value = false
  }
}
let librarySearchTimer
watch([libraryQ, libraryCategory, readingFilter, favoriteOnly], () => {
  clearTimeout(librarySearchTimer)
  librarySearchTimer = setTimeout(() => loadBooks(true), 260)
})
watch(sortBy, () => {
  localStorage.setItem('study-library-sort', sortBy.value)
  loadBooks(true)
})

const resetBookDrag = () => {
  draggingBookId.value = null
  dropTargetId.value = null
  dropPosition.value = 'before'
}
const onBookDragStart = (row, event) => {
  if (!canDragSort.value) return event.preventDefault()
  draggingBookId.value = row.id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', String(row.id))
}
const onBookDragOver = (row, event) => {
  if (!canDragSort.value || draggingBookId.value === row.id) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'
  const rect = event.currentTarget.getBoundingClientRect()
  dropTargetId.value = row.id
  dropPosition.value = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after'
}
const onBookDrop = async (target, event) => {
  event.preventDefault()
  if (!canDragSort.value || !draggingBookId.value || draggingBookId.value === target.id) return resetBookDrag()
  const before = [...books.value]
  const movingIndex = books.value.findIndex(book => book.id === draggingBookId.value)
  let targetIndex = books.value.findIndex(book => book.id === target.id)
  if (movingIndex < 0 || targetIndex < 0) return resetBookDrag()
  const [moving] = books.value.splice(movingIndex, 1)
  targetIndex = books.value.findIndex(book => book.id === target.id)
  if (dropPosition.value === 'after') targetIndex += 1
  books.value.splice(targetIndex, 0, moving)
  const orderedIds = books.value.map(book => book.id)
  const shelfId = typeof selectedShelf.value === 'number' ? selectedShelf.value : null
  resetBookDrag()
  reordering.value = true
  try {
    await reorderBooks(orderedIds, shelfId)
    ElMessage.success('顺序已保存')
  } catch (error) {
    books.value = before
    ElMessage.error(`顺序未能保存：${error.message}`)
  } finally {
    reordering.value = false
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
    ElMessage.error(`文件未能导入：${e.message}。请确认格式为 PDF、DOCX 或 PPTX 后重试。`)
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
    ElMessage.success(`已提交导入：${ok} 个正在解析，${dup} 个重复文件已跳过，${fail} 个需要重新检查`)
    batchFiles.value = []
    notifyTaskSubmitted()
    await loadBooks()
  } catch (e) {
    ElMessage.error(`所选文件未能提交：${e.message}。文件仍保留在本机，可调整选择后重试。`)
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
    ElMessage.success('智能归类已完成，可使用“全部分类”筛选查看结果')
    loadBooks()
  } catch (e) {
    ElMessage.error(`智能归类没有完成：${e.message}。请稍后重试，现有分类不会被清除。`)
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
    ElMessage.success('深度分析已加入全局任务中心，你可以继续阅读其他资料')
    notifyTaskSubmitted()
    loadBooks()
  } catch (e) {
    ElMessage.error(`无法启动深度分析：${e.message}。请检查模型设置或稍后重试。`)
  }
}

const readBook = (row) => {
  if ((row.reading_status || row.archive?.reading_status) === 'unread') updateArchiveProfile(row.id, { reading_status: 'reading' }).catch(() => {})
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
      publication_status: a.publication_status || 'unknown', visibility: a.visibility || 'private',
      demo_allowed: !!a.demo_allowed, metadata_confidence: a.metadata_confidence || 0,
      reading_status: a.reading_status,
    })
    ElMessage.success('归档信息已保存')
    loadBooks()
  } catch (e) { ElMessage.error(e.message) }
}

const selectBook = async (row, forceDrawer = false) => {
  try {
    if (currentBook.value?.id !== row.id) {
      const detail = await getBook(row.id)
      currentBook.value = detail
      chapterTree.value = detail.chapters || []
    }
    if (forceDrawer || compactLibrary.value) detailVisible.value = true
  } catch (e) {
    ElMessage.error(`无法打开资料详情：${e.message}。你可以重试或直接进入阅读器。`)
  }
}
const openBook = (row) => selectBook(row, true)

const doSearch = async () => {
  if (!searchQ.value.trim()) return
  searching.value = true
  try {
    const params = { q: searchQ.value, page_size: 20 }
    if (searchCategory.value) params.category = searchCategory.value
    results.value = await searchBooks(params)
  } catch (e) {
    ElMessage.error(`全文检索没有完成：${e.message}。请尝试缩短关键词或确认本地索引已就绪。`)
  } finally {
    searching.value = false
  }
}

const searchKeyword = (kw) => {
  // 关键词芯片点击：赋值后立即搜索（不用事件对象，避免被当作 kw 传入）
  searchQ.value = kw
  searchPanelOpen.value = true
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

const libraryMedia = window.matchMedia('(max-width: 1519px)')
const syncLibraryViewport = (event) => { compactLibrary.value = event.matches }
onMounted(async () => {
  syncLibraryViewport(libraryMedia)
  libraryMedia.addEventListener('change', syncLibraryViewport)
  await Promise.all([loadBooks(), loadShelves()])
  const requestedBookId = Number(route.query.bookId)
  if (Number.isInteger(requestedBookId) && requestedBookId > 0) {
    const row = books.value.find(book => book.id === requestedBookId) || { id: requestedBookId }
    await openBook(row)
  }
})
onBeforeUnmount(() => libraryMedia.removeEventListener('change', syncLibraryViewport))
</script>

<style scoped>
.library-page { max-width: 1680px; }
.library-workspace{display:grid;grid-template-columns:228px minmax(0,1fr);align-items:start;gap:16px;min-width:0}.bookshelf-panel{position:sticky;top:76px;padding:14px;border:1px solid #e5e7eb;border-radius:14px;background:#f7f2e9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.bookshelf-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.bookshelf-head>div{display:flex;flex-direction:column}.bookshelf-head small{margin-top:3px;color:#8b8174;font-size:10px}.create-shelf-head{padding-inline:9px}.shelf-static{display:flex;width:100%;align-items:center;justify-content:space-between;padding:9px 10px;border:0;border-radius:8px;color:#57534b;background:transparent;cursor:pointer;transition:background .16s ease,color .16s ease,transform .16s ease}.shelf-static:hover{color:#6f4721;background:#f0e7d9;transform:translateX(2px)}.shelf-static.active{color:#6f4721;background:#ebe1d1}.shelf-tree{margin-top:5px;background:transparent}.shelf-tree :deep(.el-tree-node__content){height:36px;background:transparent}.shelf-node{display:flex;flex:1;min-width:0;align-items:center;justify-content:space-between;padding:4px 5px;border-radius:7px}.shelf-node.active{color:#6f4721;background:#ebe1d1}.shelf-node>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.shelf-node i{display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%}.shelf-node>div{display:flex;align-items:center}.shelf-node small{color:#978a78}.shelf-empty{display:flex;align-items:center;flex-direction:column;margin-top:12px;padding:16px 10px;border:1px dashed #d8c9b5;border-radius:10px;color:#776b5d;background:rgba(255,255,255,.28);text-align:center}.shelf-empty small{margin:4px 0 11px;color:#9a8e7f;font-size:10px}.shelf-batch,.import-queue{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:9px 12px;margin-bottom:10px;border:1px solid #e5e7eb;border-radius:9px;background:#faf7f0;box-shadow:0 1px 2px rgba(15,23,42,.04)}.shelf-batch :deep(.el-select){width:180px}.import-queue{justify-content:flex-start;border-color:#dfc9a9;background:#fbf4e7}.import-queue>div{display:flex;flex:1;flex-direction:column}.import-queue small{margin-top:2px;color:#8b8174}.drawer-eyebrow{margin-bottom:5px;font-size:9px;letter-spacing:2px;color:#9a7a58}.paper-detail{padding-bottom:28px}.detail-actions{position:sticky;top:0;z-index:2;display:flex;padding:2px 0 14px;background:#fff}.paper-detail :deep(.el-tree){padding:8px 4px 16px;border-radius:10px;background:#f7f3ea}
.library-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 20px 24px; margin-bottom: 14px; color: #f5f0e8; background: linear-gradient(120deg, rgba(28,52,54,.96), rgba(67,85,72,.9)); border: 1px solid rgba(245,240,232,.16); border-radius: 16px; box-shadow: 0 12px 32px rgba(10,24,25,.2); }
.library-hero h1 { margin: 4px 0 6px; font-family: var(--study-font-reading); font-size: 28px; letter-spacing: 2px; }
.library-hero p { color: rgba(245,240,232,.72); }
.eyebrow { color: #d3b58f; font-size: var(--study-font-size-xs); letter-spacing: 2px; }
.hero-stats { display: flex; gap: 28px; }
.hero-stats div { display: flex; flex-direction: column; text-align: right; }
.hero-stats strong { font: 700 24px Georgia, serif; color: #f5f0e8; }
.hero-stats span { font-size: var(--study-font-size-xs); color: rgba(245,240,232,.72); }
.materials-card{border-color:#e5e7eb;box-shadow:0 1px 2px rgba(15,23,42,.06)}
.materials-card :deep(.el-card__header){padding:16px 18px;border-bottom-color:#e8dfd1}.materials-card :deep(.el-card__body){padding:14px 18px 8px}
.card-header, .header-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.header-title { display: flex; align-items:center;gap:10px;font-weight:700}.header-title>div{display:flex;flex-direction:column;gap:3px}.header-title span{font-size:var(--study-font-size-lg)}.header-title small { color: var(--el-text-color-secondary); font-weight: 400;font-size:var(--study-font-size-xs) }
.library-filters { display: grid; grid-template-columns: minmax(240px, 1fr) 126px 116px 142px auto auto; gap: 10px; align-items: center; margin-bottom: 10px; }
.clear-filter{justify-self:end}
.reorder-guide{display:flex;align-items:center;gap:7px;margin:-2px 0 12px;padding:7px 10px;border-radius:7px;background:#f7f2e9;color:#735333;font-size:var(--study-font-size-xs)}.reorder-guide.disabled{color:#958a7d;background:#f7f5f1}.drag-dots{font-size:16px;line-height:1}
.paper-title { font-weight: 650; color: var(--el-text-color-primary); line-height: 1.35; }
.paper-meta { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.publication-state:before{content:' · '}.publication-state{color:var(--study-text-muted)}
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
.paper-list{min-height:160px}.paper-list-head,.paper-row{display:grid;grid-template-columns:72px minmax(220px,1fr) 100px 118px 142px;column-gap:10px;align-items:center}.paper-list-head{padding:8px 12px;color:#918678;font-size:11px;border-top:1px solid #eee6da;border-bottom:1px solid #e5dbcc;background:#f8f4ec}.paper-list-head>span:last-child{text-align:right}.paper-row{position:relative;min-height:92px;padding:12px;border-bottom:1px solid #e8dfd2;transition:background .16s ease,box-shadow .16s ease,transform .16s ease,opacity .16s ease}.paper-row:hover{z-index:1;background:#fcfaf5;box-shadow:0 2px 10px rgba(85,65,42,.07);transform:translateY(-1px)}.paper-row.is-dragging{opacity:.38}.paper-row.drop-before:before,.paper-row.drop-after:after{position:absolute;right:8px;left:8px;height:2px;border-radius:2px;background:var(--el-color-primary);content:''}.paper-row.drop-before:before{top:-1px}.paper-row.drop-after:after{bottom:-1px}.paper-select{display:flex;align-items:center;gap:9px}.drag-handle{width:15px;color:#c4bbb0;font-size:17px;line-height:1;cursor:not-allowed;user-select:none}.drag-handle.enabled{color:#82603c;cursor:grab}.paper-row:active .drag-handle.enabled{cursor:grabbing}.star{padding:0;border:0;background:transparent}.paper-identity{min-width:0;cursor:pointer}.paper-title{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;font-family:'Noto Serif SC','STSong',serif;font-size:14px}.paper-facts{display:flex;gap:10px;margin-top:7px;color:#9b8f80;font-size:10px}.paper-facts span+span:before{content:'·';margin-right:10px}.paper-reading,.paper-knowledge{display:flex;align-items:flex-start;flex-direction:column;gap:7px;font-size:11px}.muted-state{color:#8a8175}.danger-state{color:#b4473d}.warning-state{color:#a86e27}.category-link,.analysis-link{max-width:100%;padding:0;border:0;background:transparent;color:#765331;font-size:11px;cursor:pointer;text-align:left}.analysis-link{color:#8d755a}.deep-done{color:#47806b}.paper-actions{display:flex;align-items:center;justify-content:flex-end;gap:7px}.paper-actions :deep(.el-button+.el-button){margin-left:0}
@media (max-width: 900px) {
  .library-workspace{grid-template-columns:1fr}.bookshelf-panel{position:static}.shelf-tree{max-height:190px;overflow:auto}
  .library-hero { align-items: flex-start; flex-direction: column; }
  .hero-stats { width: 100%; justify-content: space-between; }
  .hero-stats div { text-align: left; }
  .library-filters { grid-template-columns: 1fr 1fr; }
  .header-actions{width:100%;justify-content:flex-start}.paper-list-head{display:none}.paper-row{grid-template-columns:72px minmax(0,1fr) 140px}.paper-reading{grid-column:2;margin-top:10px;flex-direction:row;align-items:center}.paper-knowledge{grid-column:3;grid-row:1 / span 2}.paper-actions{grid-column:2 / -1;margin-top:10px;justify-content:flex-start}
}
@media (max-width: 620px){.library-hero{padding:16px}.library-hero h1{font-size:23px}.library-filters{grid-template-columns:1fr}.hero-stats{gap:12px}.hero-stats strong{font-size:20px}.header-actions :deep(.el-button){margin-left:0}.paper-row{grid-template-columns:66px minmax(0,1fr);padding:14px 4px}.paper-knowledge,.paper-reading,.paper-actions{grid-column:2}.paper-knowledge{grid-row:auto;margin-top:9px;flex-direction:row;align-items:center}.paper-actions{flex-wrap:wrap}.paper-meta{max-width:100%}.import-queue{align-items:flex-start;flex-wrap:wrap}.import-queue>div{flex-basis:100%}.materials-card :deep(.el-card__body){padding:12px}}

/* 资料库迁移：紧凑命令区 + 书架/列表/检查器三层工作区 */
.library-commandbar{margin-bottom:12px}.library-summary{display:flex;gap:18px}.library-summary>div{display:flex;min-width:50px;flex-direction:column;text-align:right}.library-summary strong{color:var(--el-color-primary);font:700 18px Georgia,serif}.library-summary span{margin-top:1px;color:var(--study-text-secondary);font-size:var(--study-font-size-xs)}
.library-primary-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px}.library-primary-actions :deep(.el-button+.el-button){margin-left:0}.library-import-queue{margin:-2px 0 12px}
.mobile-shelf-toggle{display:none;width:100%;align-items:center;justify-content:space-between;margin-bottom:8px;padding:9px 11px;border:1px solid var(--study-card-border);background:var(--study-surface-paper);color:var(--study-text-primary);font-size:var(--study-font-size-sm)}.mobile-shelf-toggle b{color:var(--el-color-primary);font-size:var(--study-font-size-xs)}
.library-workspace{display:grid;grid-template-columns:220px minmax(0,1fr) 300px;align-items:start;gap:12px}.library-main{min-width:0}.bookshelf-panel{top:56px}.bookshelf-section-label{padding:2px 10px 7px;color:#8f806e;font-size:var(--study-font-size-xs);font-weight:700;letter-spacing:1px}.bookshelf-head{margin-top:16px;padding-top:14px;border-top:1px solid #dfd4c4}
.paper-row{cursor:pointer;outline:none}.paper-row.selected{z-index:1;background:#f5eddf;box-shadow:inset 3px 0 0 var(--el-color-primary)}.paper-row:focus-visible{box-shadow:inset 0 0 0 2px var(--el-color-primary)}.paper-title{font-family:var(--study-font-reading);font-size:15px;font-weight:600}.paper-facts{font-size:var(--study-font-size-xs)}.paper-reading,.paper-knowledge,.category-link,.analysis-link{font-size:var(--study-font-size-xs)}
.fulltext-card{margin-top:12px}
.library-inspector{position:sticky;top:56px;height:calc(100vh - 136px);min-height:480px;overflow:hidden;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-surface-paper);box-shadow:var(--study-shadow-sm)}
.inspector-scroll{height:100%;overflow-y:auto;padding:15px}.inspector-type{color:#9a7958;font-size:var(--study-font-size-xs);letter-spacing:.6px}.inspector-scroll h2{margin:7px 0 5px;font-family:var(--study-font-reading);font-size:17px;line-height:1.5;color:var(--el-text-color-primary)}.inspector-meta{color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);line-height:1.6}.trust-row{display:flex;align-items:center;flex-wrap:wrap;gap:5px;margin-top:9px}.trust-row>span{color:var(--study-text-secondary);font-size:var(--study-font-size-xs)}.inspector-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:14px 0}.inspector-actions .el-button{margin:0}.inspector-section{padding:13px 0;border-top:1px solid var(--el-border-color-lighter)}.inspector-section-title{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px}.inspector-section-title span{color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);text-align:right}.inspector-facts{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.inspector-facts span{display:flex;flex-direction:column;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);text-align:center}.inspector-facts b{margin-bottom:2px;color:#6f4721;font:700 17px Georgia,serif}.inspector-status{margin-top:10px;padding:8px 9px;border-radius:7px;background:#f0e9dc;color:#686155;font-size:var(--study-font-size-xs);line-height:1.55}.inspector-status.failed{background:#f9efed;color:#93483d}.inspector-status.needs_ocr,.inspector-status.parsing{background:#fff5e7;color:#8d6228}.inspector-chapters :deep(.el-tree){max-height:210px;overflow:auto;padding:5px;background:transparent}.inspector-chapters :deep(.el-tree-node__content){height:32px}.inspector-detail-button{width:100%;margin-top:2px}.inspector-empty{display:flex;height:100%;align-items:center;justify-content:center;flex-direction:column;padding:28px;color:var(--el-text-color-secondary);text-align:center}.inspector-empty span{color:#9a7958;font-size:var(--study-font-size-xs);letter-spacing:.6px}.inspector-empty b{margin:12px 0 6px;color:var(--el-text-color-primary)}.inspector-empty p{font-size:var(--study-font-size-sm);line-height:1.7}

@media(max-width:1519px){.library-workspace{grid-template-columns:220px minmax(0,1fr)}.library-inspector{display:none}}
@media(max-width:1200px){.library-workspace{grid-template-columns:190px minmax(0,1fr)}.paper-list-head{display:none}.paper-row{grid-template-columns:72px minmax(0,1fr) 130px}.paper-reading{grid-column:2;margin-top:8px;flex-direction:row;align-items:center}.paper-knowledge{grid-column:3;grid-row:1 / span 2}.paper-actions{grid-column:2 / -1;margin-top:8px;justify-content:flex-start}}
@media(max-width:1100px){.library-primary-actions{justify-content:flex-start}}
@media(max-width:900px){.library-workspace{grid-template-columns:1fr}.library-summary>div{text-align:left}.library-primary-actions{flex-wrap:wrap}.mobile-shelf-toggle{display:flex}.bookshelf-panel{display:none;position:static}.bookshelf-panel.mobile-open{display:block}.bookshelf-head{margin-top:10px}.shelf-tree{max-height:190px;overflow:auto}}
@media(max-width:620px){.library-summary{width:100%;justify-content:space-between}.library-primary-actions{display:grid;grid-template-columns:1fr 1fr}.library-primary-actions>*,.library-primary-actions :deep(.el-button){width:100%}.library-primary-actions>:last-child{grid-column:1/-1}.library-import-queue{align-items:flex-start}.paper-row{padding-inline:8px}.fulltext-card :deep(.el-card__body){padding:12px}.result-meta{align-items:flex-start;flex-wrap:wrap}}
</style>
