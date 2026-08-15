<template>
  <el-row :gutter="16">
    <!-- 左：知识树编辑 -->
    <el-col :span="9">
      <el-card shadow="never">
        <template #header>
          <div class="card-header">
            <span>我的知识树</span>
            <div>
              <el-button size="small" type="primary" plain @click="addRoot">＋ 新建知识树</el-button>
              <el-button size="small" @click="loadTree">刷新</el-button>
            </div>
          </div>
        </template>
        <div v-if="!tree.length" class="tree-empty">
          <el-empty description="还没有知识树" :image-size="80">
            <el-button type="primary" @click="addRoot">创建第一棵知识树</el-button>
          </el-empty>
          <div class="tree-tip">💡 把自己对课程的理解搭成树：章节 → 概念 → 例题，再关联教材章节，右侧即可查看原文。</div>
        </div>
        <el-tree
          v-else
          :data="tree"
          :props="{ label: 'title', children: 'children' }"
          node-key="id"
          draggable
          :allow-drop="allowDrop"
          highlight-current
          :expand-on-click-node="false"
          @node-click="selectNode"
          @node-drop="handleDrop"
        >
          <template #default="{ node, data }">
            <span class="tree-node">
              <span class="tree-label">{{ data.title }}</span>
              <span class="tree-actions" @click.stop>
                <el-button link size="small" type="primary" @click="addChild(data)">＋</el-button>
                <el-button link size="small" @click="renameNode(data)">改</el-button>
                <el-button link size="small" type="danger" @click="removeNode(data)">删</el-button>
              </span>
            </span>
          </template>
        </el-tree>
        <div class="tree-drag-tip" v-if="tree.length">拖拽节点可调整层级 / 移动位置；点击节点查看详情与原文。</div>
      </el-card>
    </el-col>

    <!-- 右：节点详情 + 原文展示 -->
    <el-col :span="15">
      <el-card v-if="current" shadow="never">
        <template #header>
          <div class="card-header">
            <span>节点详情：{{ current.title }}</span>
            <el-tag v-if="source.book_title" size="small" type="info">《{{ source.book_title }}》{{ source.chapter_title || '' }}</el-tag>
          </div>
        </template>

        <el-form label-width="90px">
          <el-form-item label="节点标题">
            <el-input v-model="edit.title" style="max-width: 420px" />
          </el-form-item>
          <el-form-item label="关联章节">
            <el-select v-model="edit.book_id" placeholder="选择书籍" clearable style="width: 200px; margin-right: 8px" @change="onBookChange">
              <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <el-select v-model="edit.chapter_id" placeholder="选择章节" clearable style="width: 240px">
              <el-option v-for="c in chapterOptions" :key="c.id" :label="c.title" :value="c.id" />
            </el-select>
            <div class="form-tip">关联后，右侧即可展示该章节的教材原文（本地解析，不联网）</div>
          </el-form-item>
          <el-form-item label="我的笔记">
            <el-input v-model="edit.note" type="textarea" :rows="4" placeholder="记录自己对知识点的理解 / 总结" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveNode">保存</el-button>
            <el-button @click="loadSource">加载原文</el-button>
          </el-form-item>
        </el-form>

        <!-- 右侧原文展示 -->
        <el-divider content-position="left">📖 资料原文</el-divider>
        <div v-if="sourceLoading" v-loading="true" style="height: 120px" />
        <template v-else-if="source.text">
          <div class="source-meta">
            <el-tag size="small" type="info">《{{ source.book_title }}》</el-tag>
            <el-tag size="small" type="warning">{{ source.chapter_title }}</el-tag>
            <el-tag size="small" type="success">第 {{ source.page_start }} - {{ source.page_end }} 页</el-tag>
            <el-radio-group v-model="sourceView" size="small" style="margin-left: auto">
              <el-radio-button value="text">文本</el-radio-button>
              <el-radio-button value="pdf" v-if="source.book_id && pdfBookType === 'pdf'">PDF 原文</el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="sourceView === 'text'" class="source-text">{{ source.text }}</div>
          <iframe v-else-if="sourceView === 'pdf'" :src="pdfUrl" class="pdf-frame" frameborder="0" />
        </template>
        <el-empty v-else description="该节点尚未关联书籍章节，或该章节暂无内容" :image-size="80" />
      </el-card>
      <el-empty v-else description="点击左侧节点查看详情" style="margin-top: 80px" />
    </el-col>
  </el-row>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getKnowledgeTree, createKnowledgeNode, updateKnowledgeNode,
  deleteKnowledgeNode, moveKnowledgeNode, getKnowledgeSource,
  listBooks, getBook, bookFileUrl,
} from '../api'

const tree = ref([])
const books = ref([])
const current = ref(null)
const edit = ref({ title: '', book_id: null, chapter_id: null, note: '' })
const chapterOptions = ref([])
const source = ref({})
const sourceLoading = ref(false)
const sourceView = ref('text')
const pdfBookType = ref('')

const pdfUrl = computed(() => {
  if (!source.value.book_id || !source.value.page_start) return ''
  return `${bookFileUrl(source.value.book_id)}#page=${source.value.page_start}`
})

const loadTree = async () => {
  try {
    const resp = await getKnowledgeTree()
    tree.value = resp.items
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const loadBooks = async () => {
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items.filter((b) => b.status === 'ready')
  } catch { /* ignore */ }
}

const selectNode = async (data) => {
  current.value = data
  edit.value = { title: data.title, book_id: data.book_id, chapter_id: data.chapter_id, note: data.note || '' }
  source.value = {}
  sourceView.value = 'text'
  if (data.book_id) {
    const book = books.value.find((b) => b.id === data.book_id)
    pdfBookType.value = book?.file_type || ''
    await loadChapters(data.book_id)
  }
  if (data.chapter_id) loadSource()
}

const loadChapters = async (bookId) => {
  if (!bookId) {
    chapterOptions.value = []
    return
  }
  try {
    const detail = await getBook(bookId)
    // 展平章节树为下拉选项
    const flat = []
    const walk = (nodes, depth) => {
      for (const n of nodes) {
        flat.push({ id: n.id, title: '　'.repeat(depth) + n.title })
        if (n.children?.length) walk(n.children, depth + 1)
      }
    }
    walk(detail.chapters || [], 0)
    chapterOptions.value = flat
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const onBookChange = () => {
  edit.value.chapter_id = null
  loadChapters(edit.value.book_id)
}

const addRoot = async () => {
  try {
    const { value } = await ElMessageBox.prompt('输入知识树名称', '新建知识树', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPlaceholder: '如：公共管理学 · 核心框架',
    })
    await createKnowledgeNode({ parent_id: null, title: value.trim() })
    ElMessage.success('已创建')
    loadTree()
  } catch { /* 取消 */ }
}

const addChild = async (data) => {
  try {
    const { value } = await ElMessageBox.prompt(`在「${data.title}」下新建节点`, '新建子节点', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPlaceholder: '节点名称，如：第一章 导论',
    })
    await createKnowledgeNode({ parent_id: data.id, title: value.trim() })
    ElMessage.success('已创建')
    loadTree()
  } catch { /* 取消 */ }
}

const renameNode = async (data) => {
  try {
    const { value } = await ElMessageBox.prompt('修改节点名称', '重命名', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: data.title,
    })
    await updateKnowledgeNode(data.id, { title: value.trim() })
    ElMessage.success('已重命名')
    loadTree()
  } catch { /* 取消 */ }
}

const removeNode = async (data) => {
  try {
    await ElMessageBox.confirm(`确定删除「${data.title}」及其全部子节点？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteKnowledgeNode(data.id)
    ElMessage.success('已删除')
    if (current.value?.id === data.id) current.value = null
    loadTree()
  } catch { /* 取消 */ }
}

const saveNode = async () => {
  if (!current.value) return
  if (!edit.value.title.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }
  try {
    await updateKnowledgeNode(current.value.id, {
      title: edit.value.title.trim(),
      note: edit.value.note,
      book_id: edit.value.book_id || null,
      chapter_id: edit.value.chapter_id || null,
    })
    ElMessage.success('已保存')
    loadTree()
    loadSource()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const loadSource = async () => {
  if (!current.value?.id) return
  sourceLoading.value = true
  try {
    source.value = await getKnowledgeSource(current.value.id)
    if (source.value.book_id) {
      const book = books.value.find((b) => b.id === source.value.book_id)
      pdfBookType.value = book?.file_type || ''
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    sourceLoading.value = false
  }
}

const allowDrop = () => true

const handleDrop = async (draggingNode, dropNode, dropType) => {
  // 计算新的父节点：inner=成为 dropNode 的子节点；before/after=与 dropNode 同级（父=dropNode.parent_id）
  const newParentId = dropType === 'inner' ? dropNode.data.id : (dropNode.data.parent_id ?? null)
  if (newParentId === draggingNode.data.id) return
  try {
    await moveKnowledgeNode(draggingNode.data.id, newParentId)
    ElMessage.success('已移动')
    loadTree()
  } catch (e) {
    ElMessage.error(e.message)
    loadTree()
  }
}

onMounted(async () => {
  loadTree()
  await loadBooks()
})
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.tree-empty { padding: 8px 0; }
.tree-tip { margin-top: 12px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.8; }
.tree-node { display: flex; align-items: center; justify-content: space-between; flex: 1; padding-right: 8px; }
.tree-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-actions { display: none; gap: 2px; flex-shrink: 0; }
.el-tree-node__content:hover .tree-actions { display: inline-flex; }
.tree-drag-tip { margin-top: 10px; font-size: 12px; color: var(--el-text-color-placeholder); }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.source-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.source-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 1.9; color: var(--el-text-color-primary);
  max-height: 480px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light);
}
.pdf-frame { width: 100%; height: 560px; border-radius: 8px; border: 1px solid var(--el-border-color); }
</style>