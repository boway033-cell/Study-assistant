<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>我的资料</span>
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
            </div>
          </template>

          <el-table :data="books" v-loading="loading" empty-text="还没有资料，点击右上角上传">
            <el-table-column prop="title" label="书名" min-width="160" show-overflow-tooltip />
            <el-table-column prop="file_type" label="类型" width="70">
              <template #default="{ row }">
                <el-tag size="small">{{ row.file_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.status === 'ready'" type="success" size="small">已就绪</el-tag>
                <el-tag v-else-if="row.status === 'failed'" type="danger" size="small">解析失败</el-tag>
                <el-tag v-else type="warning" size="small">解析中</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="total_pages" label="页数" width="70" />
            <el-table-column prop="card_count" label="卡片" width="70" />
            <el-table-column prop="quiz_count" label="题目" width="70" />
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openBook(row)">详情</el-button>
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
          <template #header>全文搜索</template>
          <el-input
            v-model="searchQ"
            placeholder="输入关键词，如：拉格朗日 / 特征值 / 定积分"
            clearable
            @keyup.enter="doSearch"
            @clear="results = null"
          >
            <template #append>
              <el-button @click="doSearch">搜索</el-button>
            </template>
          </el-input>

          <div v-if="searching" v-loading="true" style="height: 60px" />
          <div v-else-if="results" style="margin-top: 12px">
            <div class="result-count">共 {{ results.total }} 条结果</div>
            <el-card v-for="r in results.items" :key="r.chunk_id" shadow="never" class="result-item">
              <div class="result-meta">
                <el-tag size="small" type="info">《{{ r.book_title }}》</el-tag>
                <span v-if="r.chapter_title" class="result-chapter">{{ r.chapter_title }}</span>
                <span v-if="r.page" class="result-page">第 {{ r.page }} 页</span>
              </div>
              <div class="result-snippet" v-html="r.snippet" />
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
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listBooks, uploadBook, deleteBook, getBook, searchBooks, getTask } from '../api'

const books = ref([])
const loading = ref(false)
const uploading = ref(false)
const searchQ = ref('')
const results = ref(null)
const searching = ref(false)
const currentBook = ref(null)
const chapterTree = ref([])

const loadBooks = async () => {
  loading.value = true
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items
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
    ElMessage.success(`已上传，开始解析：${resp.title}`)
    // 轮询任务状态
    await pollTask(resp.task_id)
    loadBooks()
  } catch (e) {
    ElMessage.error('上传失败：' + e.message)
  } finally {
    uploading.value = false
  }
  return false // 阻止默认上传
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
    results.value = await searchBooks({ q: searchQ.value, page_size: 20 })
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    searching.value = false
  }
}

onMounted(loadBooks)
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.result-item { margin-top: 8px; }
.result-meta { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.result-chapter { color: #909399; font-size: 12px; }
.result-page { color: #909399; font-size: 12px; }
.result-snippet { font-size: 14px; line-height: 1.6; color: #303133; }
.result-count { color: #909399; font-size: 12px; margin-bottom: 8px; }
</style>
