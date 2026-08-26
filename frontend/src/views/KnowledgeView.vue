<template>
  <div class="knowledge-page study-page">
    <KnowledgeScopeSelector v-if="!embedded" />
    <div v-if="!scopeBookIds.length" class="scope-required"><b>先选择需要组织的书目</b><span>知识树不会默认展示或混合整个资料库。</span></div>
    <template v-else>
    <el-row :gutter="16">
      <!-- 左：知识树（大纲 / 导图 双视图） -->
      <el-col :span="9">
        <el-card shadow="never" class="kt-card">
          <template #header>
            <div class="card-header">
              <span>我的知识树</span>
              <div class="header-actions">
                <el-radio-group v-model="viewMode" size="small">
                  <el-radio-button value="outline">大纲</el-radio-button>
                  <el-radio-button value="mindmap">导图</el-radio-button>
                </el-radio-group>
                <el-button size="small" type="primary" plain @click="addRoot">＋ 新建</el-button>
                <el-button size="small" @click="loadTree">刷新</el-button>
                <el-button size="small" :type="multiSelect ? 'warning' : ''" @click="toggleMulti">☑ 多选</el-button>
              </div>
            </div>
          </template>

          <div class="kt-stats">
            <el-tag size="small" type="info">节点 {{ statTotal }}</el-tag>
            <el-tag size="small" type="success">🟢 已掌握 {{ statKnown }}</el-tag>
            <el-tag size="small" type="warning">🟡 模糊 {{ statFuzzy }}</el-tag>
            <el-tag size="small" type="danger">🔴 未掌握 {{ statMiss }}</el-tag>
          </div>
          <el-input v-model="treeFilter" size="small" placeholder="搜索节点…" clearable style="margin-bottom: 8px" prefix-icon="Search" />
          <div class="gen-buttons">
            <el-button size="small" type="success" plain @click="openImport">从章节导入</el-button>
            <el-button size="small" type="warning" plain @click="openAi">AI 生成框架</el-button>
          </div>

          <!-- 大纲视图 -->
          <div v-if="viewMode === 'outline'">
            <div v-if="!tree.length" class="tree-empty">
              <el-empty description="还没有知识树" :image-size="80">
                <el-button type="primary" @click="addRoot">创建第一棵知识树</el-button>
              </el-empty>
              <div class="tree-tip">💡 搭建方式：① 手动新建节点；② 选一本书「从章节导入」；③ 让 AI 分析教材生成课程框架。</div>
            </div>
            <el-tree
              v-else
              ref="treeRef"
              :data="filteredTree"
              :props="{ label: 'title', children: 'children' }"
              node-key="id"
              :show-checkbox="multiSelect"
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
                  <span v-if="scopeBookIds.length > 1 && data.book_id" class="book-badge">{{ bookShortName(data.book_id) }}</span>
                  <span class="tree-actions" @click.stop>
                    <el-button link size="small" type="primary" @click="addChild(data)">＋</el-button>
                    <el-button link size="small" @click="renameNode(data)">改</el-button>
                    <el-button link size="small" type="danger" @click="removeNode(data)">删</el-button>
                  </span>
                </span>
              </template>
            </el-tree>
            <div v-if="multiSelect" class="batch-bar">
              <el-button size="small" type="danger" @click="batchDelete">🗑 删除所选</el-button>
            </div>
            <div class="tree-drag-tip" v-if="tree.length">拖拽节点调整层级；点击节点查看详情与原文</div>
          </div>

          <!-- 导图视图 -->
          <div v-else class="mindmap-wrap">
            <el-empty v-if="!tree.length" description="先创建或导入知识树" :image-size="80" />
            <MindMap v-else :data="tree" :selected-id="current?.id ?? null" @select="selectNode" />
          </div>
        </el-card>
      </el-col>

      <!-- 右：节点详情 + 原文（pdf.js 阅读器） -->
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
            <el-form-item label="节点类型">
              <el-radio-group v-model="edit.node_type" size="small">
                <el-radio-button value="concept">📘 概念</el-radio-button>
                <el-radio-button value="theorem">📐 定理</el-radio-button>
                <el-radio-button value="point">🎯 考点</el-radio-button>
                <el-radio-button value="example">📝 例题</el-radio-button>
                <el-radio-button value="question">❓ 疑问</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="掌握度">
              <el-radio-group v-model="edit.mastery" size="small">
                <el-radio-button value="unknown">⬜ 未标记</el-radio-button>
                <el-radio-button value="known">🟢 已掌握</el-radio-button>
                <el-radio-button value="fuzzy">🟡 模糊</el-radio-button>
                <el-radio-button value="miss">🔴 未掌握</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="跨树引用">
              <el-select v-model="edit.ref_node_id" placeholder="引用另一个节点（跨树关联）" clearable filterable style="width: 320px">
                <el-option v-for="n in allNodeOptions" :key="n.id" :label="n.label" :value="n.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="我的内容">
              <el-input v-model="edit.note" type="textarea" :rows="5"
                placeholder="记录知识点的理解、总结、易错点、例题…（支持 Markdown）" />
              <div v-if="edit.note" class="note-preview markdown-body" v-html="renderMarkdown(edit.note)"></div>
            </el-form-item>
            <el-form-item label="关联章节">
              <el-select v-model="edit.book_id" placeholder="选择书籍（可跨书）" clearable style="width: 190px; margin-right: 8px" @change="onBookChange">
                <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
              </el-select>
              <el-select v-model="edit.chapter_id" placeholder="选择章节" clearable style="width: 230px">
                <el-option v-for="c in chapterOptions" :key="c.id" :label="c.title" :value="c.id" />
              </el-select>
              <div class="form-tip">关联后，下方用内置 PDF 阅读器直接展示该章教材原文（无需下载）</div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveNode">保存</el-button>
              <el-button @click="loadSource">加载原文</el-button>
              <el-button type="warning" plain :loading="expanding" @click="expandNode">🤖 AI 展开子节点</el-button>
              <el-button type="info" plain :loading="reviewing" @click="reviewNote">📝 AI 批改笔记</el-button>
            </el-form-item>
          </el-form>

          <el-divider content-position="left">📖 资料原文</el-divider>
          <div v-if="sourceLoading" v-loading="true" style="height: 120px" />
          <template v-else-if="source.text">
            <div class="source-meta">
              <el-tag size="small" type="info">《{{ source.book_title }}》</el-tag>
              <el-tag size="small" type="warning">{{ source.chapter_title }}</el-tag>
              <el-tag size="small" type="success">第 {{ source.page_start }} - {{ source.page_end }} 页</el-tag>
              <el-button link size="small" type="primary" @click="goReadSource" style="margin-left: auto">⛶ 全屏阅读</el-button>
            </div>
            <div class="source-text">{{ source.text }}</div>
          </template>
          <el-empty v-else description="该节点尚未关联书籍章节，或该章节暂无内容" :image-size="80" />

          <el-divider content-position="left">📌 关联批注（PDF 阅读器）</el-divider>
          <div v-if="!nodeAnns.length" class="form-tip">还没有关联批注：在阅读器中选中文字 → 高亮 → 挂到此节点</div>
          <div v-for="a in nodeAnns" :key="a.id" class="node-ann">
            <span class="ann-dot" :style="{ background: a.color }"></span>
            <el-tag size="small" type="info">《{{ a.book_title }}》第 {{ a.page }} 页</el-tag>
            <span class="node-ann-text">{{ a.text || a.note || '' }}</span>
            <el-button link type="primary" size="small" @click="goRead(a)">去阅读 →</el-button>
          </div>
        </el-card>
        <el-empty v-else description="点击左侧节点查看详情" style="margin-top: 80px" />
      </el-col>
    </el-row>
    </template>

    <!-- AI 批改笔记弹窗 -->
    <el-dialog v-model="reviewDialog" title="AI 批改笔记" width="560px">
      <div class="markdown-body" v-html="renderMarkdown(reviewText)"></div>
      <template #footer>
        <el-button type="primary" @click="reviewDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 从章节导入 对话框 -->
    <el-dialog v-model="showImport" title="从书籍章节导入知识树骨架" width="480px">
      <el-form label-width="90px">
        <el-form-item label="选择书籍">
          <el-select v-model="importBook" multiple collapse-tags filterable placeholder="选择一本或多本书籍" style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
        </el-form-item>
        <div class="form-tip">每本书会生成一棵独立知识树，避免不同书目的目录与概念混在一起。</div>
      </el-form>
      <template #footer>
        <el-button @click="showImport = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <!-- AI 生成框架 对话框 -->
    <el-dialog v-model="showAi" title="AI 生成课程知识框架" width="480px">
      <el-form label-width="90px">
        <el-form-item label="选择书籍">
          <el-select v-model="aiBook" multiple collapse-tags filterable placeholder="选择一本或多本书籍" style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
        </el-form-item>
        <div class="form-tip">AI 按书逐本分析、逐本建树；多选不会把来源压成一棵混合树。</div>
        <div class="form-tip" v-if="aiRunning">🤖 DeepSeek 正在分析教材章节与关键词… {{ aiStage }}</div>
      </el-form>
      <template #footer>
        <el-button @click="showAi = false" :disabled="aiRunning">取消</el-button>
        <el-button type="warning" :loading="aiRunning" @click="doAiGenerate">生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { renderMarkdown } from '../utils/markdown'
import { ElMessage, ElMessageBox } from 'element-plus'
import MindMap from '../components/MindMap.vue'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
import { knowledgeBooks, knowledgeBookIds, loadKnowledgeBooks } from '../stores/knowledgeScope'
defineProps({ embedded: { type: Boolean, default: false } })
import {
  getKnowledgeTree, createKnowledgeNode, updateKnowledgeNode,
  deleteKnowledgeNode, moveKnowledgeNode, getKnowledgeSource,
  importKnowledgeChapters, aiGenerateKnowledge, expandKnowledgeNode, applyKnowledgeSuggestions,
  batchDeleteKnowledge, reviewKnowledgeNote,
  getBook, getTask, getNodeAnnotations,
} from '../api'

const router = useRouter()
const treeFilter = ref('')
const multiSelect = ref(false)
const treeRef = ref(null)
const reviewing = ref(false)
const allNodeOptions = ref([])
const reviewDialog = ref(false)
const reviewText = ref('')
const tree = ref([])
const scopeBookIds = knowledgeBookIds
const books = knowledgeBooks
const nodeAnns = ref([])
const viewMode = ref('outline')
const current = ref(null)
const edit = ref({ title: '', book_id: null, chapter_id: null, note: '', node_type: 'concept', mastery: 'unknown', ref_node_id: null })
const expanding = ref(false)
const chapterOptions = ref([])
const source = ref({})
const sourceLoading = ref(false)
const showImport = ref(false)
const importBook = ref([])
const importing = ref(false)
const showAi = ref(false)
const aiBook = ref([])
const aiRunning = ref(false)
const aiStage = ref('')

const statTotal = ref(0)
const statKnown = ref(0)
const statFuzzy = ref(0)
const statMiss = ref(0)

const filteredTree = computed(() => {
  const q = treeFilter.value.trim().toLowerCase()
  if (!q) return tree.value
  const filterNodes = (nodes) => nodes.map(n => ({ ...n, children: n.children?.length ? filterNodes(n.children) : [] }))
    .filter(n => (n.title + (n.note || '')).toLowerCase().includes(q) || n.children?.length)
  return filterNodes(tree.value)
})

const loadTree = async () => {
  try {
    const resp = await getKnowledgeTree(scopeBookIds.value)
    tree.value = resp.items
    updateStats()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const updateStats = () => {
  let total = 0, known = 0, fuzzy = 0, miss = 0
  const opts = []
  const walk = (nodes, depth) => { for (const n of nodes) { total++; if (n.mastery === 'known') known++; else if (n.mastery === 'fuzzy') fuzzy++; else if (n.mastery === 'miss') miss++; opts.push({ id: n.id, label: '　'.repeat(depth) + n.title }); if (n.children?.length) walk(n.children, depth + 1) } }
  walk(tree.value, 0)
  statTotal.value = total; statKnown.value = known; statFuzzy.value = fuzzy; statMiss.value = miss
  allNodeOptions.value = opts
}

const selectNode = async (data) => {
  current.value = data
  edit.value = { title: data.title, book_id: data.book_id, chapter_id: data.chapter_id, note: data.note || '', node_type: data.node_type || 'concept', mastery: data.mastery || 'unknown', ref_node_id: data.ref_node_id || null }
  source.value = {}
  if (data.book_id) {
    await loadChapters(data.book_id)
  }
  if (data.chapter_id) loadSource()
  loadNodeAnns(data.id)
}

const loadNodeAnns = async (nodeId) => {
  try {
    nodeAnns.value = await getNodeAnnotations(nodeId)
  } catch { nodeAnns.value = [] }
}

const goRead = (a) => {
  router.push('/reader/' + a.book_id + '?page=' + a.page)
}

const goReadSource = () => {
  if (!source.value.book_id) return
  router.push('/reader/' + source.value.book_id + '?page=' + (source.value.page_start || 1))
}

const loadChapters = async (bookId) => {
  if (!bookId) {
    chapterOptions.value = []
    return
  }
  try {
    const detail = await getBook(bookId)
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
  if (scopeBookIds.value.length !== 1) { ElMessage.info('新建知识树时请将书目范围限定为一本；多本文献可分别建树后再建立跨树引用'); return }
  try {
    const { value } = await ElMessageBox.prompt('输入知识树名称', '新建知识树', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPlaceholder: '如：公共管理学 · 核心框架',
    })
    await createKnowledgeNode({ parent_id: null, title: value.trim(), book_id: scopeBookIds.value[0] })
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
      node_type: edit.value.node_type,
      mastery: edit.value.mastery,
      ref_node_id: edit.value.ref_node_id || null,
    })
    ElMessage.success('已保存')
    loadTree()
    loadSource()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const toggleMulti = () => {
  multiSelect.value = !multiSelect.value
  if (!multiSelect.value && treeRef.value) treeRef.value.setCheckedKeys([])
}

const batchDelete = async () => {
  const keys = treeRef.value?.getCheckedKeys?.() || []
  if (!keys.length) { ElMessage.warning('请先勾选节点'); return }
  try {
    await ElMessageBox.confirm('确定删除所选 ' + keys.length + ' 个节点（含子节点）？', '批量删除', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
    await batchDeleteKnowledge(keys)
    ElMessage.success('已删除')
    multiSelect.value = false
    loadTree()
  } catch { /* 取消 */ }
}

const reviewNote = async () => {
  if (!current.value?.id) return
  reviewing.value = true
  try {
    const resp = await reviewKnowledgeNote(current.value.id)
    reviewText.value = resp.review
    reviewDialog.value = true
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    reviewing.value = false
  }
}

const expandNode = async () => {
  if (!current.value?.id) return
  expanding.value = true
  try {
    const resp = await expandKnowledgeNode(current.value.id)
    ElMessage.success('AI 正在展开…')
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      const t = await getTask(resp.task_id)
      if (t.status === 'done') {
        const suggestions=t.result?.suggestions||[]
        if(!suggestions.length){ElMessage.warning('没有生成可用建议节点');break}
        const preview=suggestions.map((item,index)=>`${index+1}. ${item.title}`).join('\n')
        await ElMessageBox.confirm(`AI 仅生成建议，尚未写入知识树：\n\n${preview}\n\n确认后才会添加到「${current.value.title}」下。`,'确认建议节点',{confirmButtonText:'确认写入',cancelButtonText:'暂不写入',type:'warning',customClass:'suggestion-confirm'})
        await applyKnowledgeSuggestions(current.value.id,suggestions)
        ElMessage.success(`已确认写入 ${suggestions.length} 个建议节点`)
        await loadTree()
        break
      }
      if (t.status === 'failed') { ElMessage.error('展开失败：' + (t.error || '')); break }
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    expanding.value = false
  }
}

const loadSource = async () => {
  if (!current.value?.id) return
  sourceLoading.value = true
  try {
    source.value = await getKnowledgeSource(current.value.id)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    sourceLoading.value = false
  }
}

const allowDrop = () => true

const subtreeSize = data => 1 + (data.children || []).reduce((sum, child) => sum + subtreeSize(child), 0)
const bookShortName = id => {
  const title = books.value.find(book => book.id === id)?.title || `书目 ${id}`
  return title.length > 8 ? title.slice(0, 8) + '…' : title
}

const handleDrop = async (draggingNode, dropNode, dropType) => {
  const newParentId = dropType === 'inner' ? dropNode.data.id : (dropNode.data.parent_id ?? null)
  if (newParentId === draggingNode.data.id) return
  // Element Tree 已在前端临时移动；先恢复正式结构，确认后再提交，避免误拖即写库。
  await loadTree()
  try {
    const affected = subtreeSize(draggingNode.data)
    const target = newParentId ? `「${dropNode.data.title}」所在层级` : '知识树根层级'
    await ElMessageBox.confirm(`将移动「${draggingNode.data.title}」及其 ${affected - 1} 个下级节点到${target}。来源关联不会改变，是否保存？`,'确认层级变更',{confirmButtonText:'保存变更',cancelButtonText:'取消',type:'warning'})
    await moveKnowledgeNode(draggingNode.data.id, newParentId)
    ElMessage.success(`已移动，影响 ${affected} 个节点`)
    await loadTree()
  } catch (e) {
    if(e!=='cancel'&&e!=='close')ElMessage.error(e.message)
    await loadTree()
  }
}

const doImport = async () => {
  if (!importBook.value.length) {
    ElMessage.warning('请选择书籍')
    return
  }
  importing.value = true
  try {
    await importKnowledgeChapters({
      book_ids: importBook.value,
      parent_node_id: null,
    })
    ElMessage.success('章节骨架已导入')
    showImport.value = false
    loadTree()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    importing.value = false
  }
}

const doAiGenerate = async () => {
  if (!aiBook.value.length) {
    ElMessage.warning('请选择书籍')
    return
  }
  aiRunning.value = true
  try {
    const resp = await aiGenerateKnowledge({
      book_ids: aiBook.value,
      parent_node_id: null,
    })
    ElMessage.success('AI 正在分析教材结构…')
    // 轮询任务
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const t = await getTask(resp.task_id)
      aiStage.value = t.stage === 'ai' ? (t.message || '分析中…') : t.stage
      if (t.status === 'done') {
        ElMessage.success(`AI 知识框架已生成（${t.result?.created || 0} 个节点）`)
        aiRunning.value = false
        showAi.value = false
        loadTree()
        return
      }
      if (t.status === 'failed') {
        ElMessage.error('生成失败：' + (t.message || t.error || ''))
        aiRunning.value = false
        return
      }
    }
    ElMessage.warning('生成超时，请稍后刷新查看')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    aiRunning.value = false
  }
}

const openImport = () => { importBook.value = [...scopeBookIds.value]; showImport.value = true }
const openAi = () => { aiBook.value = [...scopeBookIds.value]; showAi.value = true }
const onScopeChange = async () => { current.value=null; source.value={}; await loadTree() }
watch(scopeBookIds, onScopeChange, { deep: true })
onMounted(async () => { await loadKnowledgeBooks(); await loadTree() })
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.header-actions { display: flex; align-items: center; gap: 6px; }
.gen-buttons { display: flex; gap: 8px; margin-bottom: 10px; }
.kt-card :deep(.el-card__body) { display: flex; flex-direction: column; }
.tree-empty { padding: 8px 0; }
.tree-tip { margin-top: 12px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.8; }
.tree-node { display: flex; align-items: center; justify-content: space-between; flex: 1; padding-right: 8px; }
.tree-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-badge { flex:none; margin-left:6px; padding:1px 6px; border:1px solid #decdb7; border-radius:8px; background:#faf3e8; color:#8a623c; font-size:10px; }
.tree-actions { display: none; gap: 2px; flex-shrink: 0; }
:deep(.el-tree-node__content:hover) .tree-actions { display: inline-flex; }
.tree-drag-tip { margin-top: 10px; font-size: 12px; color: var(--el-text-color-placeholder); }
.mindmap-wrap { min-height: 480px; }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.source-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.source-text {
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 14px;
  font-size: 14px; line-height: 1.9; color: var(--el-text-color-primary);
  max-height: 480px; overflow-y: auto; white-space: pre-wrap;
  border: 1px solid var(--el-border-color-extra-light);
}
.pdf-box { border-radius: 8px; overflow: hidden; height: 560px; }
.knowledge-page{max-width:1500px}.scope-required{display:flex;flex-direction:column;gap:6px;padding:40px;border:1px dashed #d8c7b0;border-radius:var(--study-radius-md);background:#f7f2e9;text-align:center}.scope-required span{color:var(--el-text-color-secondary)}
@media(max-width:1100px){.knowledge-page :deep(.el-row){display:flex;flex-direction:column}.knowledge-page :deep(.el-col){max-width:100%;flex:0 0 100%}.kt-card{margin-bottom:14px}}
</style>
