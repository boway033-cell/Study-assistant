<template>
  <section class="source-picker">
    <el-tabs v-model="active" class="picker-tabs" stretch>
      <el-tab-pane v-for="tab in tabs" :key="tab.type" :name="tab.type">
        <template #label>
          <span class="tab-label">{{ tab.label }}<b v-if="tabCount(tab.type)">{{ tabCount(tab.type) }}</b></span>
        </template>

        <template v-if="tab.type !== 'web'">
          <div class="picker-toolbar">
            <el-input v-model="lists[tab.type].q" size="small" clearable :placeholder="`搜索${tab.label}`"
                      @keyup.enter="reload(tab.type)" @clear="reload(tab.type)" @change="reload(tab.type)" />
            <span class="picker-stat">已选 {{ (modelValue[tab.type] || []).length }} / 共 {{ lists[tab.type].total }}</span>
          </div>
          <div v-loading="lists[tab.type].loading" :class="['picker-list', `list-${tab.type}`]">
            <p v-if="!lists[tab.type].loading && !lists[tab.type].items.length" class="picker-empty">
              {{ lists[tab.type].q ? '没有匹配的来源' : `尚无${tab.label}` }}
            </p>
            <button v-for="item in lists[tab.type].items" :key="sourceKey(tab.type, item)"
                    type="button" :class="['picker-row', { picked: isSelected(modelValue, tab.type, item) }]"
                    @click="toggle(tab.type, item)">
              <span class="row-mark">{{ isSelected(modelValue, tab.type, item) ? '✓' : '+' }}</span>
              <span class="row-body">
                <b>{{ item.title }}</b>
                <small>{{ sourceSubtitle(tab.type, item) }}</small>
                <small v-if="tab.type === 'evidence'" class="row-preview">{{ item.claim_preview || item.preview }}</small>
                <small v-else-if="tab.type === 'report' && item.hypothesis_count" class="row-preview">
                  含 {{ item.hypothesis_count }} 条假设与竞争性解释
                </small>
              </span>
              <el-tag size="small" effect="plain">{{ tab.label }}</el-tag>
            </button>
            <el-button v-if="lists[tab.type].items.length < lists[tab.type].total" text size="small"
                       :loading="lists[tab.type].loading" @click="loadMore(tab.type)">
              继续加载（{{ lists[tab.type].items.length }}/{{ lists[tab.type].total }}）
            </el-button>
          </div>
        </template>

        <template v-else>
          <div class="picker-toolbar">
            <el-input v-model="web.q" size="small" clearable placeholder="按主题检索学术元数据（如：地方治理 参与机制）"
                      @keyup.enter="runWebSearch" />
            <el-button size="small" :loading="web.loading" :disabled="web.q.trim().length < 3"
                       @click="runWebSearch">检索</el-button>
            <span class="picker-stat">已选 {{ (modelValue.web || []).length }}</span>
          </div>
          <el-alert v-if="web.error" type="warning" :closable="false" show-icon :title="web.error" />
          <p v-if="web.searched && !web.results.length && !web.error" class="picker-empty">没有检索到结果，可调整关键词后重试。</p>
          <div class="picker-list list-web">
            <div v-for="item in web.results" :key="sourceKey('web', item)" class="web-result">
              <button type="button" :class="['picker-row', { picked: isSelected(modelValue, 'web', item), disabled: !webSourceSelectable(item) }]"
                      @click="webSourceSelectable(item) ? toggle('web', item) : null">
                <span class="row-mark">{{ webSourceSelectable(item) ? (isSelected(modelValue, 'web', item) ? '✓' : '+') : '—' }}</span>
                <span class="row-body">
                  <b>{{ item.title }}</b>
                  <small>{{ sourceSubtitle('web', item) }}</small>
                  <small v-if="item.abstract" class="row-preview">{{ item.abstract.slice(0, 160) }}</small>
                </span>
                <el-tag size="small" :type="webSourceSelectable(item) ? 'success' : 'info'" effect="plain">
                  {{ webSourceSelectable(item) ? '摘要级依据' : '元数据线索' }}
                </el-tag>
              </button>
              <div class="web-actions">
                <el-button text size="small" :loading="web.pending === sourceKey('web', item)"
                           @click="resolveWeb(item)">查找开放全文</el-button>
                <template v-for="candidate in web.resolved[sourceKey('web', item)] || []" :key="candidate.url">
                  <el-button v-if="candidate.direct_download" text size="small" @click="importWeb(item, candidate)">
                    导入资料库
                  </el-button>
                  <el-button text size="small" @click="openWeb(item, candidate)">在浏览器打开</el-button>
                </template>
              </div>
            </div>
          </div>
        </template>
      </el-tab-pane>
    </el-tabs>

    <div class="picked-panel">
      <header>
        <b>已选来源</b>
        <span>{{ usableSourceCount(modelValue) }} 项可承载事实{{ metadataOnlyCount ? `，${metadataOnlyCount} 条线索不可用` : '' }}</span>
      </header>
      <p v-if="!selectedGroups(modelValue).length" class="picker-empty">尚未选择任何来源。</p>
      <div v-for="group in selectedGroups(modelValue)" :key="group.type" class="picked-group">
        <span class="group-label">{{ group.label }}</span>
        <div class="picked-items">
          <span v-for="(item, index) in group.items" :key="sourceKey(group.type, item)" class="picked-item">
            <b>{{ item.title }}</b>
            <el-tag v-if="group.type === 'web'" size="small" effect="plain" type="success">摘要级依据</el-tag>
            <el-tag v-else-if="group.type === 'evidence'" size="small" effect="plain">
              {{ verificationLabel(item.verification_status) }}
            </el-tag>
            <el-button text size="small" @click="remove(group.type, index)">移除</el-button>
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { importOpenAccess, listWritingSources, openBrowserHandoff, resolveLiterature, searchLiterature } from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'
import {
  SOURCE_LABELS, dropMissing, isSelected, removeAt, selectedGroups, sourceKey,
  sourceSubtitle, usableSourceCount, verificationLabel, webSourceSelectable,
} from '../utils/writingSourceModel'

const props = defineProps({ modelValue: { type: Object, required: true } })
const emit = defineEmits(['update:modelValue'])

const tabs = [
  { type: 'note', label: SOURCE_LABELS.note },
  { type: 'evidence', label: SOURCE_LABELS.evidence },
  { type: 'report', label: '审查报告' },
  { type: 'local_literature', label: SOURCE_LABELS.local_literature },
  { type: 'web', label: SOURCE_LABELS.web },
]
const active = ref('note')
const newList = () => ({ items: [], total: 0, page: 1, q: '', loading: false, loaded: false })
const lists = reactive({
  note: newList(), evidence: newList(), report: newList(), local_literature: newList(),
})
const counts = ref({ note: 0, evidence: 0, report: 0, local_literature: 0 })
const web = reactive({ q: '', loading: false, searched: false, error: '', results: [], resolved: {}, pending: '' })

const tabCount = type => type === 'web' ? web.results.length : counts.value[type] || 0
const metadataOnlyCount = computed(() => (props.modelValue.web || []).filter(item => !webSourceSelectable(item)).length)

const update = selection => emit('update:modelValue', selection)
const toggle = (type, item) => update(addOrToggle(type, item))
const addOrToggle = (type, item) => {
  const current = props.modelValue[type] || []
  const key = sourceKey(type, item)
  const picked = current.some(entry => sourceKey(type, entry) === key)
  const next = picked
    ? { ...props.modelValue, [type]: current.filter(entry => sourceKey(type, entry) !== key) }
    : { ...props.modelValue, [type]: [...current, item] }
  return next
}
const remove = (type, index) => update(removeAt(props.modelValue, type, index))

const fetchPage = async (type, page) => {
  const list = lists[type]
  list.loading = true
  try {
    const result = await listWritingSources({ category: type, q: list.q.trim() || undefined, page, page_size: 20 })
    list.total = result.total || 0
    list.page = result.page || page
    list.items = page === 1 ? (result.items || []) : [...list.items, ...(result.items || [])]
    counts.value = { ...counts.value, ...(result.counts || {}) }
    list.loaded = true
  } catch (e) {
    ElMessage.error(`加载${SOURCE_LABELS[type]}失败：${e.message}`)
  } finally {
    list.loading = false
  }
}
const reload = type => { lists[type].items = []; fetchPage(type, 1) }
const loadMore = type => fetchPage(type, lists[type].page + 1)

// 每类分区按需加载：切换到某个分区时才拉取它的第一页，避免一次性请求五类来源。
watch(active, type => {
  if (type === 'web') return
  const list = lists[type]
  if (!list || list.loaded || list.loading) return
  fetchPage(type, 1)
})

const runWebSearch = async () => {
  const query = web.q.trim()
  if (query.length < 3) return
  web.loading = true
  web.error = ''
  try {
    const result = await searchLiterature({ query, provider: 'crossref', rows: 10 })
    web.results = result.results || []
    web.searched = true
    if (!web.results.length) ElMessage.info('没有检索到结果，关键词与已选来源已保留')
  } catch (e) {
    // 失败时保留查询词、已选本地来源与 DNA 选择，只提示可恢复的错误。
    web.error = `文献检索未完成：${e.message}。查询词与已选来源已保留，可直接重试。`
  } finally {
    web.loading = false
  }
}

const resolveWeb = async item => {
  const key = sourceKey('web', item)
  web.pending = key
  try {
    const result = await resolveLiterature({ query: item.doi || item.url, include_si: false })
    const candidates = result.candidates || []
    web.resolved = { ...web.resolved, [key]: candidates }
    if (!candidates.length) ElMessage.info('未发现开放全文，可改用图书馆或 CARSI 入口')
  } catch (e) {
    ElMessage.error(`全文检索失败：${e.message}。该线索与其他已选来源已保留。`)
  } finally {
    web.pending = ''
  }
}

const importWeb = async (item, candidate) => {
  try {
    const result = await importOpenAccess({ query: item.doi || item.url, url: candidate.url,
                                            provider: candidate.provider, title: item.title, include_si: false })
    if (result.task_id) notifyTaskSubmitted()
    ElMessage.success(result.duplicate ? '该文献已在资料库，可在本地文献分区中取材' : '已进入解析队列，完成后可在本地文献分区取材')
  } catch (e) {
    ElMessage.error(`无法导入：${e.message}。可改用浏览器下载后从资料库导入。`)
  }
}

const openWeb = async (item, candidate) => {
  try {
    const result = await openBrowserHandoff({ query: item.doi || item.url, url: candidate.url, include_si: false })
    window.open(result.url, '_blank', 'noopener')
    ElMessage.info(`${result.instruction}；若没有新页面，请允许浏览器弹窗后重试。`)
  } catch (e) {
    ElMessage.error(`无法打开浏览器接续路径：${e.message}`)
  }
}

// 已删除/失效的来源必须被明确移除并提示，而不是等到生成时才失败。
const reconcile = async () => {
  let removedTotal = 0
  for (const type of ['note', 'evidence', 'report', 'local_literature']) {
    const picked = props.modelValue[type] || []
    if (!picked.length) continue
    try {
      const result = await listWritingSources({ category: type, ids: picked.map(item => Number(item.id ?? item.book_id)) })
      const dropped = dropMissing(props.modelValue, type, result.missing_ids)
      if (dropped.removed) {
        update(dropped.selection)
        removedTotal += dropped.removed
      }
    } catch { /* 校验失败不清空选择 */ }
  }
  if (removedTotal) ElMessage.info(`已移除 ${removedTotal} 个已不存在的来源`)
}

onMounted(async () => {
  await reconcile()
  await fetchPage('note', 1)
})
</script>

<style scoped>
.source-picker{box-sizing:border-box;display:grid;gap:10px;width:100%;max-width:100%;min-width:0;padding-top:2px;overflow:hidden}
.picker-tabs{width:100%;min-width:0}
.picker-tabs :deep(.el-tabs__header){margin-bottom:8px}
.picker-tabs :deep(.el-tabs__nav-wrap),.picker-tabs :deep(.el-tabs__nav-scroll),.picker-tabs :deep(.el-tabs__content){min-width:0;max-width:100%}
.picker-tabs :deep(.el-tabs__item){min-width:0;padding:0 4px}
.tab-label{display:inline-flex;min-width:0;max-width:100%;align-items:center;gap:4px;font-size:var(--study-font-size-xs);white-space:nowrap}
.tab-label b{min-width:18px;padding:0 5px;border-radius:9px;background:var(--study-surface-muted);color:var(--study-text-secondary);font-size:var(--study-font-size-micro);font-weight:500;text-align:center}
.picker-toolbar{display:flex;min-width:0;align-items:center;gap:8px}
.picker-toolbar .el-input{min-width:0;max-width:320px}
.picker-stat{color:var(--study-text-secondary);font-size:var(--study-font-size-micro);white-space:nowrap}
.picker-list{display:grid;gap:4px;max-height:280px;overflow:auto;padding:2px}
.picker-empty{margin:6px 0;color:var(--study-text-muted);font-size:var(--study-font-size-xs)}
.picker-row{box-sizing:border-box;display:grid;grid-template-columns:20px minmax(0,1fr) auto;align-items:center;gap:8px;width:100%;min-width:0;padding:7px 8px;border:1px solid transparent;border-radius:var(--study-radius-sm);background:var(--study-surface-muted);text-align:left;cursor:pointer}
.picker-row:hover{border-color:var(--study-card-border)}
.picker-row.picked{border-color:var(--study-accent);background:var(--study-surface-paper)}
.picker-row.disabled{cursor:default;opacity:.75}
.row-mark{color:var(--study-accent);font-size:var(--study-font-size-sm);text-align:center}
.row-body{display:flex;min-width:0;flex-direction:column;gap:2px}
.row-body b{font-size:var(--study-font-size-sm);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row-body small{color:var(--study-text-secondary);font-size:var(--study-font-size-micro);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row-body .row-preview{color:var(--study-text-muted)}
.web-result{display:grid;gap:2px}
.web-actions{display:flex;flex-wrap:wrap;gap:4px;padding-left:28px}
.picked-panel{display:grid;gap:6px;padding-top:8px;border-top:1px solid var(--study-card-border)}
.picked-panel header{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.picked-panel header span{color:var(--study-text-secondary);font-size:var(--study-font-size-micro)}
.picked-group{display:grid;gap:4px}
.group-label{color:var(--study-text-secondary);font-size:var(--study-font-size-micro)}
.picked-items{display:flex;flex-wrap:wrap;gap:6px}
.picked-item{display:inline-flex;align-items:center;gap:6px;padding:3px 6px;border:1px solid var(--study-card-border);border-radius:var(--study-radius-sm);background:var(--study-surface-muted);font-size:var(--study-font-size-xs);max-width:100%}
.picked-item b{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
</style>
