<template>
  <div class="doc-reader" :class="{ 'dr-dark': dark }">
    <div class="dr-toolbar">
      <el-button size="small" @click="showToc = !showToc">📑 目录</el-button>
      <el-button size="small" :type="editingToc ? 'warning' : ''" @click="toggleTocEdit">
        {{ editingToc ? '完成目录编辑' : '✏️ 编辑目录' }}
      </el-button>
      <el-button-group>
        <el-button size="small" @click="fontSize--">A−</el-button>
        <span class="dr-font">{{ fontSize }}px</span>
        <el-button size="small" @click="fontSize++">A＋</el-button>
      </el-button-group>
      <el-button size="small" :type="dark ? 'primary' : ''" @click="dark = !dark">{{ dark ? '☀️' : '🌙' }}</el-button>
      <el-button size="small" @click="showAnnPanel = true">🖍 批注({{ annotations.length }})</el-button>
      <span class="dr-info">{{ doc?.file_type?.toUpperCase() }} · {{ chapters.length }} {{ isPpt ? '幻灯片' : '章' }}</span>
      <el-input-number v-if="isPpt" v-model="jumpSlide" :min="1" :max="sections.length" size="small" style="width: 120px" @change="jumpToSlide" />
      <el-button v-if="isPpt" size="small" @click="jumpToSlide">跳转</el-button>
    </div>
    <div class="dr-body">
      <aside v-if="showToc" class="dr-toc">
        <div class="dr-toc-item" v-for="c in flatChapters" :key="c.id"
          :style="{ paddingLeft: (c.level - 1) * 14 + 8 + 'px' }"
          :class="{ active: activeChapter === c.id }" @click="jumpTo(c.id)">
          <template v-if="editingToc">
            <el-input v-model="c._title" size="small" @blur="saveChapterTitle(c)" @click.stop />
          </template>
          <template v-else>{{ c.title }}</template>
        </div>
      </aside>
      <div ref="content" class="dr-content" :style="{ fontSize: fontSize + 'px' }" @scroll="onScroll" @mouseup="onMouseUp">
        <div v-if="loading" v-loading="true" style="height: 200px" />
        <div v-else>
          <div class="dr-title">{{ doc?.title }}</div>
          <template v-for="(sec, idx) in sections" :key="sec.chapter_id">
            <div class="dr-chapter" :class="{ 'dr-slide': isPpt }" :data-cid="sec.chapter_id" :ref="(el) => setSecRef(sec.chapter_id, el)">
              <div class="dr-chapter-title">
                <span v-if="isPpt" class="slide-badge">幻灯片 {{ idx + 1 }}</span>
                {{ sec.title }}
              </div>
              <div v-if="isPpt" class="dr-slide-text" v-html="renderSlideText(sec.text)"></div>
              <div v-else class="dr-chapter-text" v-html="sanitizeHtml(renderDocText(sec.text))"></div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 选中文字浮动工具条 -->
    <div v-if="selToolbar" class="dr-sel-bar" :style="{ top: selY + 'px', left: selX + 'px' }">
      <el-button size="small" type="primary" @click="aiAsk('explain')">💡 询问 AI</el-button>
      <el-button size="small" type="success" @click="aiAsk('translate')">🌐 翻译</el-button>
      <el-button size="small" type="warning" @click="addAnnotation">🖍 标注</el-button>
    </div>

    <!-- AI 结果抽屉 -->
    <el-drawer v-model="aiPanel" :title="aiTitle" size="42%">
      <div v-if="aiLoading" v-loading="true" style="height: 160px" />
      <div v-else class="ai-result markdown-body" v-html="renderMarkdown(aiResult)"></div>
    </el-drawer>

    <!-- 批注管理抽屉 -->
    <el-drawer v-model="showAnnPanel" title="我的批注" size="38%">
      <div v-if="!annotations.length" class="form-tip">选中正文文字 → 点「🖍 标注」即可添加</div>
      <div v-for="a in annotations" :key="a.id" class="ann-item">
        <div class="ann-head">
          <el-tag size="small" type="info">{{ isPpt ? '幻灯片 ' + (a.page || '?') : (a.page ? '第 ' + a.page + ' 页' : '全文') }}</el-tag>
          <el-button link size="small" type="danger" @click="removeAnn(a)">删除</el-button>
        </div>
        <div class="ann-text">{{ a.text }}</div>
        <div v-if="a.note" class="ann-note">📝 {{ a.note }}</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBookDocument, listAnnotations, createAnnotation, deleteAnnotation, renameChapter, aiExplain } from '../api'
import { renderMarkdown, sanitizeHtml } from '../utils/markdown'

const props = defineProps({ bookId: { type: Number, required: true } })

const doc = ref(null)
const isPpt = computed(() => doc.value?.file_type === 'pptx')
const jumpSlide = ref(1)

const jumpToSlide = () => {
  const idx = Math.min(Math.max(1, jumpSlide.value), sections.value.length)
  if (idx > 0 && idx <= sections.value.length) {
    const sec = sections.value[idx - 1]
    if (sec) jumpTo(sec.chapter_id)
  }
}

const renderSlideText = (text) => {
  if (!text) return ''
  // PPT 文本按行分割，每行作为一个要点
  return text.split('\n').filter(l => l.trim()).map(l => '<p>' + escapeHtml(l.trim()) + '</p>').join('')
}

// Word 正文渲染：按空行分段为 <p>；识别标题模式行（第X章/一、/1.1/（一）等）加粗
const renderDocText = (text) => {
  if (!text) return ''
  const paras = text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean)
  return paras.map(para => {
    const firstLine = para.split('\n')[0].trim()
    if (isHeadingLine(firstLine)) {
      return '<p class="doc-sub-heading">' + escapeHtml(para) + '</p>'
    }
    return '<p>' + escapeHtml(para).replace(/\n/g, '<br/>') + '</p>'
  }).join('')
}

const isHeadingLine = (line) => {
  if (!line || line.length > 45) return false
  return /^第\s*[一二三四五六七八九十百千万0-9]+\s*[章节篇编部]/.test(line)
    || /^[一二三四五六七八九十]+、/.test(line)
    || /^\d+(\.\d+)*\s/.test(line)
    || /^（[一二三四五六七八九十]+）/.test(line)
}

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const chapters = ref([])
const sections = ref([])
const loading = ref(true)
const showToc = ref(true)
const editingToc = ref(false)
const dark = ref(false)
const fontSize = ref(15)
const activeChapter = ref(null)
const content = ref(null)
const secRefs = {}
const annotations = ref([])
const showAnnPanel = ref(false)
const selToolbar = ref(false)
const selX = ref(0)
const selY = ref(0)
let selText = ''
let selChapterId = null
const aiPanel = ref(false)
const aiTitle = ref('AI 解读')
const aiLoading = ref(false)
const aiResult = ref('')

function setSecRef(id, el) { if (el) secRefs[id] = el }

const flatChapters = computed(() => {
  const out = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      out.push({ ...n, level: n.level || depth })
      if (n.children?.length) walk(n.children, depth + 1)
    }
  }
  walk(buildTree(chapters.value), 1)
  return out
})

const buildTree = (list) => {
  const map = {}
  const roots = []
  for (const c of list) { map[c.id] = { ...c, children: [] } }
  for (const c of list) {
    if (c.parent_id && map[c.parent_id]) map[c.parent_id].children.push(map[c.id])
    else roots.push(map[c.id])
  }
  return roots
}

const jumpTo = (id) => {
  const el = secRefs[id]
  if (el && content.value) content.value.scrollTo({ top: el.offsetTop - 10, behavior: 'smooth' })
}

const onScroll = () => {
  const st = content.value?.scrollTop || 0
  let cur = null
  for (const sec of sections.value) {
    const el = secRefs[sec.chapter_id]
    if (el && el.offsetTop <= st + 20) cur = sec.chapter_id
  }
  activeChapter.value = cur
}

const toggleTocEdit = () => {
  editingToc.value = !editingToc.value
  if (editingToc.value) {
    for (const c of flatChapters.value) c._title = c.title
  }
}

const saveChapterTitle = async (c) => {
  const t = (c._title || '').trim()
  if (!t || t === c.title) return
  try {
    await renameChapter(c.id, t)
    c.title = t
    ElMessage.success('标题已保存')
    // 同步 sections 显示
    const sec = sections.value.find(s => s.chapter_id === c.id)
    if (sec) sec.title = t
    // 重新加载目录
    await loadDoc()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const onMouseUp = () => {
  setTimeout(() => {
    const sel = window.getSelection()
    const t = sel?.toString().trim()
    if (!sel || sel.isCollapsed || !t) { selToolbar.value = false; return }
    // 选区在正文区内
    const node = sel.anchorNode
    const inContent = node?.parentElement?.closest?.('.dr-content')
    if (!inContent) return
    selText = t.slice(0, 2000)
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    // 找所属章节
    selChapterId = null
    let el = node.parentElement
    while (el) {
      if (el.classList?.contains('dr-chapter')) { selChapterId = Number(el.dataset.cid); break }
      el = el.parentElement
    }
    selX.value = rect.left
    selY.value = rect.bottom + 8
    selToolbar.value = true
  }, 0)
}

const aiAsk = async (action) => {
  if (!selText) return
  selToolbar.value = false
  aiTitle.value = action === 'translate' ? '翻译' : 'AI 解读'
  aiPanel.value = true
  aiLoading.value = true
  aiResult.value = ''
  try {
    const resp = await aiExplain({ text: selText, action, book_title: doc.value?.title || '', chapter_title: '' })
    if (!resp.ok) throw new Error(resp.error || 'AI 调用失败')
    aiResult.value = resp.result
  } catch (e) {
    aiResult.value = '⚠️ ' + e.message
  } finally {
    aiLoading.value = false
  }
}

const addAnnotation = async () => {
  if (!selText) return
  selToolbar.value = false
  try {
    const { value } = await ElMessageBox.prompt('可选：写批注', '添加批注', {
      confirmButtonText: '保存', cancelButtonText: '取消', inputPlaceholder: '你的理解…',
    })
    await createAnnotation(props.bookId, {
      page: 0, rect_json: '[]', text: selText, color: '#9be5a0', note: value || '',
      knowledge_node_id: null,
    })
    ElMessage.success('已添加批注')
    window.getSelection()?.removeAllRanges()
    loadAnnotations()
  } catch { /* 取消 */ }
}

const removeAnn = async (a) => {
  await deleteAnnotation(a.id)
  loadAnnotations()
}

const loadAnnotations = async () => {
  try { annotations.value = await listAnnotations(props.bookId) } catch {}
}

const loadDoc = async () => {
  try {
    doc.value = await getBookDocument(props.bookId)
    chapters.value = doc.value.chapters || []
    sections.value = doc.value.sections || []
  } catch (e) {
    console.error('doc load error', e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadDoc()
  loadAnnotations()
})
</script>

<style scoped>
.doc-reader { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 400px; }
.dr-toolbar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; flex-wrap: wrap; background: var(--el-fill-color-lighter); border-radius: 8px 8px 0 0; border: 1px solid var(--el-border-color-extra-light); }
.dr-font { font-size: 12px; color: var(--el-text-color-secondary); min-width: 40px; text-align: center; }
.dr-info { font-size: 12px; color: var(--el-text-color-secondary); margin-left: auto; }
.dr-body { display: flex; flex: 1; min-height: 0; border: 1px solid var(--el-border-color-extra-light); border-radius: 0 0 8px 8px; overflow: hidden; }
.dr-toc { width: 200px; flex-shrink: 0; overflow-y: auto; background: #f6f9fb; border-right: 1px solid var(--el-border-color-extra-light); padding: 6px 0; }
.dr-toc-item { font-size: 13px; padding: 5px 10px; cursor: pointer; color: var(--el-text-color-regular); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dr-toc-item:hover { background: var(--el-color-primary-light-9); }
.dr-toc-item.active { background: var(--el-color-primary-light-8); color: var(--el-color-primary); font-weight: 600; }
.dr-content { flex: 1; overflow-y: auto; padding: 20px 28px; background: #fff; }
.dr-title { font-size: 24px; font-weight: 700; text-align: center; margin-bottom: 24px; color: var(--el-text-color-primary); }
.dr-chapter { margin-bottom: 28px; }
.dr-chapter-title { font-size: 18px; font-weight: 700; color: var(--bailu-accent); border-left: 4px solid var(--bailu-accent); padding-left: 10px; margin-bottom: 12px; }
.dr-chapter-text { font-size: inherit; line-height: 1.9; color: var(--el-text-color-regular); user-select: text; cursor: text; }
.dr-chapter-text p { margin: 0.6em 0; line-height: 1.9; }
.dr-chapter-text .doc-sub-heading { font-weight: 700; color: var(--el-text-color-primary); font-size: 1.05em; margin-top: 1.1em; }
.dr-dark .dr-chapter-text .doc-sub-heading { color: #c8d6dc; }
.dr-slide { background: #fff; border: 1px solid var(--el-border-color-light); border-radius: 12px; padding: 24px 32px; margin-bottom: 20px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.dr-slide .dr-chapter-title { display: flex; align-items: center; gap: 8px; font-size: 20px; border-bottom: 2px solid var(--bailu-accent); padding-bottom: 10px; margin-bottom: 16px; }
.slide-badge { background: var(--bailu-accent); color: #fff; font-size: 12px; padding: 2px 10px; border-radius: 12px; white-space: nowrap; }
.dr-slide-text { line-height: 2; }
.dr-slide-text p { margin: 0.5em 0; padding-left: 16px; border-left: 3px solid var(--el-border-color-lighter); }
.dr-dark .dr-slide { background: #1e1e1e; }
.dr-dark .dr-slide-text p { border-left-color: #444; }
.dr-dark .dr-content { background: #1e1e1e; }
.dr-dark .dr-chapter-title, .dr-dark .dr-title { color: #a8c3d1; }
.dr-dark .dr-chapter-text { color: #ccc; }
.dr-sel-bar { position: fixed; z-index: 50; display: flex; gap: 4px; padding: 4px; background: #fff; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.25); border: 1px solid var(--el-border-color-light); }
.ai-result { font-size: 14px; line-height: 1.9; color: #333333; }
.ann-item { padding: 10px; border: 1px solid var(--el-border-color-extra-light); border-radius: 8px; margin-bottom: 8px; }
.ann-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ann-text { font-size: 13px; color: var(--el-text-color-regular); margin-bottom: 4px; }
.ann-note { font-size: 12px; color: var(--el-text-color-secondary); }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; }
</style>