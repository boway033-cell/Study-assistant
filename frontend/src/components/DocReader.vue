<template>
  <div class="doc-reader" :class="{ 'dr-dark': dark }">
    <div class="dr-toolbar">
      <el-button size="small" @click="showToc = !showToc">📑 目录</el-button>
      <el-button-group>
        <el-button size="small" @click="fontSize--">A−</el-button>
        <span class="dr-font">{{ fontSize }}px</span>
        <el-button size="small" @click="fontSize++">A＋</el-button>
      </el-button-group>
      <el-button size="small" :type="dark ? 'primary' : ''" @click="dark = !dark">{{ dark ? '☀️' : '🌙' }}</el-button>
      <el-input v-model="searchQ" size="small" placeholder="页内搜索…" style="width: 180px" clearable @keyup.enter="doSearch" />
      <span class="dr-info">{{ doc?.file_type?.toUpperCase() }} · {{ chapters.length }} 章</span>
    </div>
    <div class="dr-body">
      <aside v-if="showToc" class="dr-toc">
        <div class="dr-toc-item" v-for="c in flatChapters" :key="c.id"
          :style="{ paddingLeft: (c.level - 1) * 16 + 8 + 'px' }"
          :class="{ active: activeChapter === c.id }" @click="jumpTo(c.id)">{{ c.title }}</div>
      </aside>
      <div ref="content" class="dr-content" :style="{ fontSize: fontSize + 'px' }" @scroll="onScroll">
        <div v-if="loading" v-loading="true" style="height: 200px" />
        <div v-else>
          <div class="dr-title">{{ doc?.title }}</div>
          <template v-for="sec in sections" :key="sec.chapter_id">
            <div class="dr-chapter" :data-cid="sec.chapter_id" :ref="(el) => setSecRef(sec.chapter_id, el)">
              <div class="dr-chapter-title">{{ sec.title }}</div>
              <div class="dr-chapter-text">{{ sec.text }}</div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { getBookDocument } from '../api'

const props = defineProps({ bookId: { type: Number, required: true } })

const doc = ref(null)
const chapters = ref([])
const sections = ref([])
const loading = ref(true)
const showToc = ref(true)
const dark = ref(false)
const fontSize = ref(15)
const searchQ = ref('')
const activeChapter = ref(null)
const content = ref(null)
const secRefs = {}

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
  if (el && content.value) {
    content.value.scrollTo({ top: el.offsetTop - 10, behavior: 'smooth' })
  }
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

const doSearch = () => {
  const q = searchQ.value.trim().toLowerCase()
  if (!q) return
  // 简单跳转：找到第一个包含关键词的章节
  for (const sec of sections.value) {
    if (sec.text.toLowerCase().includes(q)) {
      jumpTo(sec.chapter_id)
      return
    }
  }
}

onMounted(async () => {
  try {
    doc.value = await getBookDocument(props.bookId)
    chapters.value = doc.value.chapters || []
    sections.value = doc.value.sections || []
  } catch (e) {
    console.error('doc load error', e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.doc-reader { display: flex; flex-direction: column; height: 100%; min-height: 400px; }
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
.dr-chapter-text { font-size: inherit; line-height: 1.9; color: var(--el-text-color-regular); white-space: pre-wrap; }
.dr-dark .dr-content { background: #1e1e1e; }
.dr-dark .dr-chapter-title, .dr-dark .dr-title { color: #a8c3d1; }
.dr-dark .dr-chapter-text { color: #ccc; }
</style>
