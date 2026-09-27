<template>
  <div class="research-archive" v-loading="loading">
    <p class="hint">这里保存你确认的研究问题、解释和判断。每条记录绑定保存时的书目与正文版本；新资料不会改写旧依据。</p>
    <div class="archive-toolbar">
      <el-select v-model="snapshotId" placeholder="选择资料范围快照" style="min-width: 250px" @change="loadDiff">
        <el-option v-for="s in snapshots" :key="s.id" :value="s.id" :label="`#${s.id} · ${s.books.length} 本 · ${formatTime(s.created_at)}`" />
      </el-select>
      <el-button @click="freezeScope" :loading="saving">冻结当前范围</el-button>
    </div>
    <el-alert v-if="diff && !diff.is_current" type="warning" :closable="false" show-icon
      :title="`范围已变化：新增 ${diff.added.length} 本、移除 ${diff.removed.length} 本、来源更新 ${diff.changed.length} 本${diff.shelves_changed ? '；项目书架组成已变化' : ''}`" />
    <p v-else-if="diff?.is_current" class="hint">当前书目和正文版本与所选快照一致。</p>
    <p v-if="snapshot" class="hint">本次范围：{{ snapshot.books.map(b => b.title).join('、') || '空范围' }}</p>
    <p v-if="candidateEvidence?.statement" class="hint">已带入论证节点的 AI 初判。请先核对原文，再决定是否用自己的判断保存。</p>
    <el-divider />
    <div class="archive-toolbar"><b>研究问题与判断</b><el-button type="primary" size="small" :disabled="!snapshotId" @click="newItem">＋ 新建条目</el-button></div>
    <div v-if="!visibleItems.length" class="hint">还没有研究条目。先写一个可以回到原文核对的问题。</div>
    <div v-for="item in visibleItems" :key="item.id" class="archive-item" @click="selectItem(item)">
      <el-tag size="small" :type="item.kind === 'question' ? 'primary' : item.kind === 'judgment' ? 'warning' : 'success'">{{ kindLabel(item.kind) }}</el-tag>
      <span class="item-statement">{{ item.statement }}</span>
      <small>快照 #{{ item.snapshot_id }} · {{ item.origin === 'ai' && item.review_status !== 'confirmed' ? 'AI 提议待复核' : item.review_status === 'confirmed' ? '用户确认' : '待复核' }} · {{ item.evidence.length }} 条证据</small>
    </div>
    <template v-if="selected">
      <el-divider />
      <div class="archive-toolbar"><b>{{ kindLabel(selected.kind) }} #{{ selected.id }}</b><el-tag size="small" type="info">快照 #{{ selected.snapshot_id }}</el-tag></div>
      <el-input v-model="editStatement" type="textarea" :rows="3" maxlength="4000" show-word-limit placeholder="当前判断或问题" />
      <h4>当前解释</h4>
      <el-input v-model="editExplanation" type="textarea" :rows="3" maxlength="4000" placeholder="为什么这样判断？也可以写下竞争解释" />
      <h4>未解决问题</h4>
      <el-input v-model="editDoubt" type="textarea" :rows="2" maxlength="2000" placeholder="尚未解决的问题 / 下一步要读什么" class="archive-gap" />
      <div class="archive-toolbar archive-gap">
        <el-select v-model="editReview" style="width: 145px"><el-option label="我已确认" value="confirmed" /><el-option label="尚未看懂" value="unclear" /><el-option label="待复核" value="unreviewed" /><el-option label="不再采纳" value="rejected" /></el-select>
        <el-select v-model="triggerId" clearable placeholder="触发修订的证据" style="min-width: 180px"><el-option v-for="e in selected.evidence" :key="e.id" :label="`#${e.id} ${e.book_title} 第${e.page_start || '?'}页`" :value="e.id" /></el-select>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存修订</el-button>
      </div>
      <h4>支持、挑战与边界证据</h4>
      <p v-if="!selected.evidence.length" class="hint">还没有证据。可以从问答引用打开此面板，或填写文献与文本块编号。</p>
      <div v-for="e in selected.evidence" :key="e.id" class="archive-evidence">
        <el-tag size="small" :type="e.relation === 'challenges' ? 'danger' : e.relation === 'supports' ? 'success' : 'info'">{{ relationLabel(e.relation) }}</el-tag>
        <span>《{{ e.book_title }}》{{ e.page_start ? `第 ${e.page_start}${e.page_end && e.page_end !== e.page_start ? `–${e.page_end}` : ''} 页` : '页码未明' }}</span>
        <el-tag v-if="e.source_status !== 'current'" size="small" type="warning">{{ statusLabel(e.source_status) }}</el-tag>
        <p>“{{ e.quote }}”</p>
        <el-button v-if="e.source_status === 'current' && e.page_start" link type="primary" @click="$emit('open-source', e)">回到原页</el-button>
      </div>
      <div class="evidence-form">
        <b>加入原文依据</b>
        <div class="archive-toolbar archive-gap"><el-input-number v-model="evidenceBookId" :min="1" placeholder="文献 ID" /><el-input-number v-model="evidenceChunkId" :min="1" placeholder="文本块 ID" /><el-select v-model="evidenceRelation" style="width: 130px"><el-option v-for="r in relations" :key="r" :value="r" :label="relationLabel(r)" /></el-select></div>
        <el-input v-model="evidenceQuote" type="textarea" :rows="3" maxlength="1000" placeholder="填写原文中的短引；保存时会核对文本块和版本" />
        <el-button type="primary" plain class="archive-gap" :loading="saving" :disabled="!evidenceBookId || !evidenceChunkId || evidenceQuote.trim().length < 2" @click="saveEvidence">保存证据</el-button>
      </div>
      <el-button link @click="loadRevisions">{{ showRevisions ? '收起修订记录' : '查看修订记录' }}</el-button>
      <div v-if="showRevisions" class="archive-revisions"><div v-for="r in revisions" :key="r.id"><small>{{ formatTime(r.created_at) }}{{ r.trigger_evidence_id ? ` · 证据 #${r.trigger_evidence_id}` : '' }}</small><p>{{ r.before.statement || '新增证据' }} → {{ r.after.statement || '证据已加入' }}</p><p v-if="r.after.detail?.reading_outcomes?.length > (r.before.detail?.reading_outcomes?.length || 0)">阅读复核：{{ outcomeLabel(r.after.detail.reading_outcomes.at(-1).outcome) }} · {{ r.after.detail.reading_outcomes.at(-1).note || '未填写备注' }}</p></div></div>
    </template>
    <ResearchDiscoveryPanel v-if="snapshotId" :scope-type="scopeType" :scope-id="scopeId" :snapshot-id="snapshotId" :snapshot-current="!!diff?.is_current" :selected-item="selected" @open-source="e => $emit('open-source', e)" @archive-updated="syncAfterDiscovery" />
    <ResearchDesignPanel v-if="snapshotId" :scope-type="scopeType" :scope-id="scopeId" :snapshot-id="snapshotId" :snapshot-current="!!diff?.is_current" :selected-item="selected" :books="snapshot?.books || []" @open-source="e => $emit('open-source', e)" @archive-updated="syncAfterDiscovery" />
    <el-dialog v-model="createDialog" title="新建研究条目" width="540px" append-to-body>
      <p v-if="newOrigin === 'ai'" class="hint">以下内容来自论证节点的 AI 初判，保存后仍需你对照原文确认。</p>
      <el-select v-model="newKind"><el-option label="研究问题" value="question" /><el-option label="当前判断" value="judgment" /><el-option label="解释" value="explanation" /></el-select>
      <el-input v-model="newStatement" type="textarea" :rows="4" maxlength="4000" class="archive-gap" placeholder="用自己的话写下问题、判断或解释" />
      <template #footer><el-button @click="createDialog=false">取消</el-button><el-button type="primary" :disabled="!snapshotId || newStatement.trim().length < 2" :loading="saving" @click="saveNewItem">{{ newOrigin === 'ai' ? '保存为待复核提议' : '保存' }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createResearchSnapshot, listResearchSnapshots, getResearchSnapshotDiff, listResearchItems,
  createResearchItem, updateResearchItem, addResearchEvidence, listResearchRevisions } from '../api'
import ResearchDiscoveryPanel from './ResearchDiscoveryPanel.vue'
import ResearchDesignPanel from './ResearchDesignPanel.vue'

const props = defineProps({ scopeType: { type: String, required: true }, scopeId: { type: Number, required: true }, candidateEvidence: { type: Object, default: null } })
defineEmits(['open-source'])
const snapshots = ref([])
const items = ref([])
const visibleItems = computed(() => items.value.filter(item => ['question', 'judgment', 'explanation'].includes(item.kind)))
const snapshotId = ref(null)
const snapshot = computed(() => snapshots.value.find(s => s.id === snapshotId.value))
const diff = ref(null)
const selected = ref(null)
const loading = ref(false)
const saving = ref(false)
const createDialog = ref(false)
const newKind = ref('question')
const newOrigin = ref('user')
const newStatement = ref('')
const editStatement = ref('')
const editExplanation = ref('')
const editDoubt = ref('')
const editReview = ref('confirmed')
const triggerId = ref(null)
const revisions = ref([])
const showRevisions = ref(false)
const evidenceBookId = ref(null)
const evidenceChunkId = ref(null)
const evidenceQuote = ref('')
const evidenceRelation = ref('supports')
const relations = ['supports', 'challenges', 'defines', 'limits', 'unclear']
const scope = () => ({ scope_type: props.scopeType, scope_id: props.scopeId })
const kindLabel = k => ({ question: '研究问题', judgment: '当前判断', explanation: '解释' }[k] || k)
const relationLabel = r => ({ supports: '支持', challenges: '挑战', defines: '定义', limits: '限定', unclear: '尚不明确' }[r] || r)
const statusLabel = s => ({ missing: '原文已删除', source_changed: '原文块已变化', document_changed: '文献版本已变化', missing_page: '缺少页码' }[s] || s)
const outcomeLabel = s => ({ supports_a: '支持 A', supports_b: '支持 B', neither: '两者都不支持', unclear: '仍不清楚' }[s] || s)
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN') : ''

const refresh = async () => {
  if (!props.scopeType || !props.scopeId) return
  loading.value = true
  try {
    const [nextSnapshots, nextItems] = await Promise.all([listResearchSnapshots(props.scopeType, props.scopeId), listResearchItems(props.scopeType, props.scopeId)])
    snapshots.value = nextSnapshots
    items.value = nextItems
    if (!nextSnapshots.some(s => s.id === snapshotId.value)) snapshotId.value = nextSnapshots[0]?.id || null
    selected.value = selected.value ? nextItems.find(i => i.id === selected.value.id) || null : null
    if (!selected.value) {
      const readingTasks = nextItems.filter(i => i.kind === 'reading_task')
      const priority = readingTasks.find(i => i.evidence?.some(e => e.source_status !== 'current')) || readingTasks.find(i => !i.detail?.outcome || i.detail.outcome === 'unclear')
      const judgment = priority && nextItems.find(i => i.id === priority.detail?.parent_item_id && i.kind === 'judgment')
      if (judgment) { snapshotId.value = judgment.snapshot_id; selectItem(judgment) }
    }
    await loadDiff()
  } catch (e) { ElMessage.error(e.message) } finally { loading.value = false }
}
const syncAfterDiscovery = async () => {
  await refresh()
  if (selected.value) selectItem(selected.value)
}
const loadDiff = async () => {
  diff.value = snapshotId.value ? await getResearchSnapshotDiff(snapshotId.value, props.scopeType, props.scopeId) : null
}
const freezeScope = async () => {
  saving.value = true
  try { const row = await createResearchSnapshot(scope()); await refresh(); snapshotId.value = row.id; await loadDiff(); ElMessage.success('当前资料范围已冻结') }
  catch (e) { ElMessage.error(e.message) } finally { saving.value = false }
}
const selectItem = item => {
  selected.value = item
  editStatement.value = item.statement
  editExplanation.value = item.detail?.explanation || ''
  editDoubt.value = item.detail?.unresolved || ''
  editReview.value = item.review_status
  triggerId.value = null
  showRevisions.value = false
}
const newItem = () => { newStatement.value = props.candidateEvidence?.statement || ''; newOrigin.value = props.candidateEvidence?.statement ? 'ai' : 'user'; createDialog.value = true }
const saveNewItem = async () => {
  saving.value = true
  try { const row = await createResearchItem({ ...scope(), snapshot_id: snapshotId.value, kind: newKind.value, statement: newStatement.value.trim(), detail: {}, origin: newOrigin.value }); createDialog.value = false; await refresh(); selectItem(items.value.find(i => i.id === row.id)); ElMessage.success('研究条目已保存') }
  catch (e) { ElMessage.error(e.message) } finally { saving.value = false }
}
const saveEdit = async () => {
  if (!selected.value) return
  saving.value = true
  try { await updateResearchItem(selected.value.id, { ...scope(), statement: editStatement.value.trim(), detail: { ...selected.value.detail, explanation: editExplanation.value.trim(), unresolved: editDoubt.value.trim() }, review_status: editReview.value, trigger_evidence_id: triggerId.value }); await refresh(); ElMessage.success('修订已记录') }
  catch (e) { ElMessage.error(e.message) } finally { saving.value = false }
}
const saveEvidence = async () => {
  if (!selected.value) return
  saving.value = true
  try { await addResearchEvidence(selected.value.id, { ...scope(), book_id: evidenceBookId.value, chunk_id: evidenceChunkId.value, quote: evidenceQuote.value.trim(), relation: evidenceRelation.value }); await refresh(); evidenceQuote.value = ''; ElMessage.success('证据已保存，可回原页复核') }
  catch (e) { ElMessage.error(e.message) } finally { saving.value = false }
}
const loadRevisions = async () => {
  if (showRevisions.value) { showRevisions.value = false; return }
  try { revisions.value = await listResearchRevisions(selected.value.id, props.scopeType, props.scopeId); showRevisions.value = true }
  catch (e) { ElMessage.error(e.message) }
}
watch(() => [props.scopeType, props.scopeId], refresh, { immediate: true })
watch(() => props.candidateEvidence, value => {
  if (!value) return
  evidenceBookId.value = value.book_id
  evidenceChunkId.value = value.chunk_id
  evidenceQuote.value = value.quote || ''
  evidenceRelation.value = 'supports'
  if (value.statement && !selected.value) {
    newKind.value = 'judgment'
    newStatement.value = value.statement
  }
}, { immediate: true })
</script>

<style scoped>
.research-archive { padding: 0 4px 24px; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.55; }
.archive-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.archive-gap { margin-top: 12px; }
.archive-item { display: flex; align-items: center; gap: 8px; padding: 9px 6px; border-bottom: 1px solid var(--el-border-color-light); cursor: pointer; }
.archive-item:hover { background: var(--el-fill-color-light); }
.item-statement { flex: 1; min-width: 0; }
.archive-item small { color: var(--el-text-color-secondary); }
.archive-evidence, .archive-revisions > div { padding: 9px; border-left: 2px solid var(--el-border-color); margin: 8px 0; background: var(--el-fill-color-light); }
.archive-evidence p { margin: 6px 0; white-space: pre-wrap; }
.evidence-form { margin: 16px 0; padding: 12px; border: 1px solid var(--el-border-color); border-radius: 6px; }
</style>
