<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>我的资料</span>
              <el-button type="success" plain :loading="classifying" @click="classifyAll">🤖 自动分类</el-button>
              <el-upload
                :show-file-list="false"
                :before-upload="handleUpload"
                multiple
                accept=".pdf,.docx,.pptx"
                :disabled="uploading"
              >
                <el-button type="primary" :loading="uploading">
                  {{ uploading ? '上传中…' : '上传 PDF / Word / PPT' }}
                </el-button>
              </el-upload>
              <el-upload
                :show-file-list="false"
                :before-upload="handleBatchUpload"
                multiple
                accept=".pdf,.docx,.pptx"
                :disabled="uploading"
              >
                <el-button type="warning" plain :loading="uploading">
                  {{ uploading ? '批量上传中…' : '📁 批量上传' }}
                </el-button>
              </el-upload>
            </div>
          </template>

          <el-table :data="books" v-loading="loading" empty-text="还没有资料，点击右上角上传">
            <el-table-column prop="title" label="书名" min-width="160" show-overflow-tooltip />
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
                <el-tag v-else type="info" size="small">解析中</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="total_pages" label="页数" width="70" />
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
      </el-col>

      <el-col :span="8">
        <el-card v-if="currentBook" shadow="never">
          <template #header>
            <div class="card-header">
              <span>{{ currentBook.title }}</span>
              <el-button link type="primary" size="small" @click="currentBook = null">关闭</el-button>
            </div>
          </template>
          <el-tree
            :data="chapterTree"
            :props="{ label: 'title', children: 'children' }"
            default-expand-all
            empty-text="暂无章节"
          />
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
        </el-card>
      </el-col>
    </el-row>

    <!-- 原文定位面板 -->
    <OriginalViewer ref="originalViewer" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBooks, uploadBook, uploadBookBatch, deleteBook, getBook, searchBooks, getTask, classifyAllBooks, setBookCategory, deepAnalyze } from '../api'
import { sanitizeHtml } from '../utils/markdown'
import OriginalViewer from '../components/OriginalViewer.vue'

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
const currentBook = ref(null)
const chapterTree = ref([])
const originalViewer = ref(null)

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
      await pollTask(resp.task_id)
    }
    loadBooks()
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
  return false // 阻止默认上传
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
    loadBooks()
  } catch (e) {
    ElMessage.error('批量上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
}

const pollTask = async (taskId) => {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 1000))
    const t = await getTask(taskId)
    if (t.status === 'done') {
      ElMessage.success('解析完成！')
      return
    }
    if (t.status === 'failed') {
      ElMessage.error('解析失败：' + (t.message || t.error || '未知错误'))
      return
    }
  }
}

const removeBook = async (row) => {
  try {
    await deleteBook(row.id)
    ElMessage.success('已删除')
    if (currentBook.value?.id === row.id) currentBook.value = null
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
    loadBooks()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const readBook = (row) => {
  router.push('/reader/' + row.id)
}

const openBook = async (row) => {
  try {
    const detail = await getBook(row.id)
    currentBook.value = detail
    chapterTree.value = detail.chapters
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

const viewOriginal = (item) => {
  const book = books.value.find((b) => b.id === item.book_id)
  const ps = item.page_start || item.page || null
  const pe = item.page_end || item.page || null
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
.card-header { display: flex; justify-content: space-between; align-items: center; }
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
</style>