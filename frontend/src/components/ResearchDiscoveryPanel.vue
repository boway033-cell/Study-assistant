<template>
  <section class="discovery-panel">
    <el-divider content-position="left">跨书架概念追踪</el-divider>
    <el-alert v-if="!snapshotCurrent" type="warning" :closable="false" title="当前资料与这次快照不同。请先在上方冻结当前范围，再启动新分析。" />
    <p class="hint">按术语定位文献，每本优先选取不同页的最多四处候选原文，再核对定义、测量和适用范围。归并术语只改变检索分组，不代表两个义项等价。</p>
    <div class="row"><el-input v-model="term" placeholder="输入要追踪的概念，例如：自主性" maxlength="160" style="max-width: 260px" />
      <el-input v-model="aliases" placeholder="可选：作者使用的别名，逗号分隔" style="max-width: 300px" />
      <el-button :disabled="!snapshotCurrent || term.trim().length < 2" :loading="busy" @click="previewConcept">查看分析范围</el-button></div>
    <div v-if="conceptPreview" class="preview">
      <p>将在 {{ conceptPreview.matched_books }}/{{ conceptPreview.total_books }} 本文献的 {{ conceptPreview.passages }} 处原文中查找义项；预计 {{ conceptPreview.estimated_calls }} 次调用、约 {{ conceptPreview.estimated_tokens }} Token<span v-if="conceptPreview.estimated_cost_cny != null">，约 ¥{{ conceptPreview.estimated_cost_cny }}</span>。</p>
      <small>{{ conceptPreview.notice }}</small>
      <p v-if="conceptPreview.no_match?.length">未定位术语：{{ conceptPreview.no_match.map(b => b.title).join('、') }}。可以补充作者原词后再分析。</p>
      <el-button type="primary" size="small" :disabled="!snapshotCurrent || !conceptPreview.matched_books || !conceptPreview.within_budget" :loading="busy" @click="analyzeConcept">开始分析</el-button>
      <el-tag v-if="!conceptPreview.within_budget" type="warning">超过当前任务预算</el-tag>
    </div>
    <p v-if="taskMessage" class="hint">{{ taskMessage }}</p>
    <div v-if="selectedSenses.length" class="row"><span>已选 {{ selectedSenses.length }}/2 个义项</span><el-button type="primary" plain size="small" :disabled="!snapshotCurrent || selectedSenses.length !== 2" :loading="busy" @click="alignSenses">比较可比性</el-button><small>两条短引会发送给当前研究模型。</small></div>
    <p v-if="!cards.length" class="hint">当前快照还没有概念义项。分析会产生待复核候选，不会自动合并作者的概念。</p>
    <article v-for="card in cards" :key="card.concept_key" class="concept-card">
      <h4>{{ card.concept_key }} <small>{{ card.senses.length }} 个义项</small></h4>
      <div class="sense-grid"><div v-for="sense in card.senses" :key="sense.id" class="sense">
        <div class="row"><el-checkbox :model-value="selectedSenses.includes(sense.id)" @change="toggleSense(sense.id)">参与比较</el-checkbox><el-tag size="small" :type="sense.review_status === 'confirmed' ? 'success' : 'warning'">{{ sense.review_status === 'confirmed' ? '用户确认' : 'AI 待核对' }}</el-tag><el-tag v-if="sense.evidence[0]?.source_status !== 'current'" size="small" type="danger">来源已变化</el-tag></div>
        <b>{{ sense.detail.original_term }}</b><small v-if="sense.shelves?.length"> · 当前书架：{{ sense.shelves.join('、') }}</small>
        <p><b>原文义项：</b>{{ sense.detail.meaning }}</p>
        <dl><dt>测量或操作化</dt><dd>{{ sense.detail.measurement }}</dd><dt>分析单位</dt><dd>{{ sense.detail.unit }}</dd><dt>时期地点 / 对象</dt><dd>{{ sense.detail.period_place }} / {{ sense.detail.population }}</dd><dt>研究方法</dt><dd>{{ sense.detail.method }}</dd><dt>结论方向 / 范围</dt><dd>{{ sense.detail.result_direction || '未说明' }} / {{ sense.detail.conclusion_scope }}</dd></dl>
        <div v-for="e in sense.evidence" :key="e.id" class="quote">《{{ e.book_title }}》{{ pageText(e) }} · 文本块 #{{ e.chunk_id }}：“{{ e.quote }}” <el-button v-if="e.source_status === 'current'" link size="small" @click="$emit('open-source', e)">原页 ↗</el-button></div>
        <div class="row"><el-input v-model="senseEdits[sense.id]" size="small" placeholder="核对后可修订义项" /><el-button size="small" :disabled="sense.evidence[0]?.source_status !== 'current'" @click="confirmSense(sense)">修订并确认</el-button></div>
        <div class="row"><el-input v-model="mergeTerms[sense.id]" size="small" placeholder="归并到另一概念词" /><el-button size="small" plain @click="mergeSense(sense)">归并</el-button></div>
        <el-button link size="small" @click="toggleRevisions(sense.id)">义项修订记录</el-button>
        <div v-if="revisionsByItem[sense.id]" class="revisions"><p v-for="revision in revisionsByItem[sense.id]" :key="revision.id">{{ revision.before.concept_key !== revision.after.concept_key ? `${revision.before.concept_key} → ${revision.after.concept_key}` : revision.before.statement !== revision.after.statement ? `${revision.before.statement} → ${revision.after.statement}` : `${revision.before.review_status} → ${revision.after.review_status}` }}</p></div>
      </div></div>
      <div v-for="alignment in card.alignments" :key="alignment.id" class="alignment">
        <div class="row"><el-tag :type="alignment.review_status === 'confirmed' ? 'success' : 'warning'" size="small">{{ alignment.review_status === 'confirmed' ? '用户核定' : 'AI 可比性提议' }}</el-tag><b>{{ alignmentLabel(alignment.detail.status) }}</b><small>义项 #{{ alignment.detail.sense_ids.join(' 与 #') }}</small></div>
        <p>{{ alignment.detail.reason }}</p><small>尚需核对：{{ alignment.detail.unresolved || '未说明' }}</small>
        <div class="evidence-columns"><div><b>支持材料</b><p v-if="!alignment.evidence.some(e => e.relation === 'supports')" class="hint">尚未补充</p><div v-for="e in alignment.evidence.filter(e => e.relation === 'supports')" :key="e.id" class="quote">{{ e.book_title }} · {{ pageText(e) }}：“{{ e.quote }}” <el-button v-if="e.source_status === 'current'" link size="small" @click="$emit('open-source', e)">原页 ↗</el-button></div></div><div><b>挑战材料</b><p v-if="!alignment.evidence.some(e => e.relation === 'challenges')" class="hint">尚未补充</p><div v-for="e in alignment.evidence.filter(e => e.relation === 'challenges')" :key="e.id" class="quote">{{ e.book_title }} · {{ pageText(e) }}：“{{ e.quote }}” <el-button v-if="e.source_status === 'current'" link size="small" @click="$emit('open-source', e)">原页 ↗</el-button></div></div></div>
        <details><summary>两条义项的定义依据与补充证据</summary><div v-for="e in alignment.evidence.filter(e => !['supports', 'challenges'].includes(e.relation))" :key="e.id" class="quote">{{ e.book_title }} · {{ pageText(e) }} · 文本块 #{{ e.chunk_id }}：“{{ e.quote }}” <el-button v-if="e.source_status === 'current'" link size="small" @click="$emit('open-source', e)">原页 ↗</el-button></div></details>
        <div class="row"><el-select v-model="alignmentEvidence[alignment.id].relation" size="small" style="width: 110px"><el-option label="支持" value="supports" /><el-option label="挑战" value="challenges" /><el-option label="限定" value="limits" /></el-select><el-input-number v-model="alignmentEvidence[alignment.id].book_id" size="small" :min="1" placeholder="文献 ID" /><el-input-number v-model="alignmentEvidence[alignment.id].chunk_id" size="small" :min="1" placeholder="文本块 ID" /></div>
        <div class="row"><el-input v-model="alignmentEvidence[alignment.id].quote" size="small" placeholder="填写原文连续短引，保存时核对范围、版本与原页" /><el-button size="small" :disabled="!alignmentEvidence[alignment.id].book_id || !alignmentEvidence[alignment.id].chunk_id || alignmentEvidence[alignment.id].quote.trim().length < 8" @click="saveAlignmentEvidence(alignment)">补充证据</el-button></div>
        <div class="row review-row"><el-select v-model="alignmentForms[alignment.id].status" size="small" style="width: 130px"><el-option v-for="s in alignmentStates" :key="s" :value="s" :label="alignmentLabel(s)" /></el-select><el-input v-model="alignmentForms[alignment.id].reason" size="small" placeholder="你的可比性理由" /><el-button size="small" @click="confirmAlignment(alignment)">核定</el-button></div>
        <el-button link size="small" @click="toggleRevisions(alignment.id)">可比性修订记录</el-button>
        <div v-if="revisionsByItem[alignment.id]" class="revisions"><p v-for="revision in revisionsByItem[alignment.id]" :key="revision.id">{{ alignmentLabel(revision.before.detail?.status) }} → {{ alignmentLabel(revision.after.detail?.status) }}；{{ revision.after.statement }}</p></div>
      </div>
    </article>

    <p v-if="selectedItem?.kind === 'judgment' && selectedItem.snapshot_id !== snapshotId" class="hint">这条判断属于快照 #{{ selectedItem.snapshot_id }}。请切换到对应快照后寻找反证。</p>
    <template v-if="selectedItem?.kind === 'judgment' && selectedItem.snapshot_id === snapshotId">
      <el-divider content-position="left">主动寻找反证</el-divider>
      <p class="hint">检索只使用这条判断保存时的文献快照。候选可能是相反结果、边界、负例、替代机制、方法限制或图表与结论的不一致；需要你回原页判断是否相关。</p>
      <div class="row"><el-button :loading="busy" :disabled="!snapshotCurrent || selectedItem.review_status !== 'confirmed'" @click="previewCounter">查看检索范围与费用</el-button><el-tag v-if="selectedItem.review_status !== 'confirmed'" type="warning">请先确认这条判断</el-tag></div>
      <div v-if="counterPreview" class="preview"><p>将检索快照内 {{ counterPreview.total_books }} 本文献；预计 {{ counterPreview.estimated_calls }} 次模型调用、约 {{ counterPreview.estimated_tokens }} Token<span v-if="counterPreview.estimated_cost_cny != null">，约 ¥{{ counterPreview.estimated_cost_cny }}</span>。</p><small>{{ counterPreview.notice }}</small><div class="row"><el-button type="primary" size="small" :disabled="!counterPreview.within_budget" :loading="busy" @click="searchCounter">开始寻找反证</el-button></div></div>
      <p v-if="counterMessage" class="hint">{{ counterMessage }}</p>
      <p v-if="counterSearched && !counterCandidates.length" class="hint">本次所选范围未找到值得呈现的候选；这不表示不存在反证。</p>
      <article v-for="candidate in counterCandidates" :key="candidate.id" class="counter-card">
        <div class="row"><el-tag size="small" type="danger">{{ counterTypeLabel(candidate.detail.type) }}</el-tag><el-tag size="small" :type="candidate.review_status === 'confirmed' ? 'success' : 'warning'">{{ candidate.review_status === 'confirmed' ? '已采纳' : candidate.review_status === 'rejected' ? '不相关' : '候选待核对' }}</el-tag><el-tag v-if="candidate.evidence[0]?.source_status !== 'current'" size="small" type="warning">来源已变化</el-tag></div>
        <p>{{ candidate.statement }}</p><small>可能改变判断之处：{{ candidate.detail.why_it_might_change || '需要核对' }}</small>
        <div v-for="e in candidate.evidence" :key="e.id" class="quote">《{{ e.book_title }}》{{ pageText(e) }} · 文本块 #{{ e.chunk_id }}：“{{ e.quote }}” <el-button v-if="e.source_status === 'current'" link size="small" @click="$emit('open-source', e)">原页 ↗</el-button></div>
        <div v-if="candidate.review_status === 'unreviewed' && candidate.evidence[0]?.source_status === 'current'" class="row"><el-input v-model="revisedStatements[candidate.id]" size="small" placeholder="采纳后，你如何修订原判断？" /><el-button size="small" type="primary" :disabled="!revisedStatements[candidate.id]?.trim()" @click="acceptCandidate(candidate)">采纳并修订</el-button><el-button size="small" @click="rejectCandidate(candidate)">不相关</el-button></div>
      </article>
    </template>
  </section>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { subscribeTask, previewResearchConcepts, analyzeResearchConcepts, listResearchConceptCards,
  mergeResearchConcept, alignResearchConcepts, reviewResearchAlignment, updateResearchItem,
  listResearchRevisions, addResearchEvidence,
  previewResearchCounterevidence, searchResearchCounterevidence, listResearchCounterevidence,
  acceptResearchCounterevidence } from '../api'

const props = defineProps({ scopeType: { type: String, required: true }, scopeId: { type: Number, required: true }, snapshotId: { type: Number, required: true }, snapshotCurrent: { type: Boolean, default: false }, selectedItem: { type: Object, default: null } })
const emit = defineEmits(['open-source', 'archive-updated'])
const cards = ref([])
const term = ref('')
const aliases = ref('')
const conceptPreview = ref(null)
const counterPreview = ref(null)
const counterCandidates = ref([])
const counterSearched = ref(false)
const selectedSenses = ref([])
const mergeTerms = reactive({})
const senseEdits = reactive({})
const alignmentForms = reactive({})
const alignmentEvidence = reactive({})
const revisedStatements = reactive({})
const revisionsByItem = reactive({})
const alignmentStates = ['same', 'partial', 'different', 'insufficient']
const busy = ref(false)
const taskMessage = ref('')
const counterMessage = ref('')
const scope = () => ({ scope_type: props.scopeType, scope_id: props.scopeId })
const aliasesList = () => aliases.value.split(/[,，、]/).map(s => s.trim()).filter(Boolean).slice(0, 7)
const alignmentLabel = s => ({ same: '可比较', partial: '部分可比较', different: '不可比较', insufficient: '材料不足' }[s] || s)
const counterTypeLabel = s => ({ opposite_result: '相反结果', boundary: '适用边界', negative_case: '负例', alternative_mechanism: '替代机制', method_limit: '方法限制', figure_mismatch: '图表/案例与结论' }[s] || s)
const pageText = e => e.page_start ? `第 ${e.page_start}${e.page_end && e.page_end !== e.page_start ? `–${e.page_end}` : ''} 页` : '页码未明'
const loadCards = async () => {
  if (!props.snapshotId) return
  try {
    cards.value = await listResearchConceptCards(props.scopeType, props.scopeId, props.snapshotId)
    for (const card of cards.value) {
      for (const sense of card.senses) { senseEdits[sense.id] = sense.detail.meaning; mergeTerms[sense.id] = card.concept_key }
      for (const alignment of card.alignments) {
        alignmentForms[alignment.id] = { status: alignment.detail.status, reason: alignment.detail.reason, unresolved: alignment.detail.unresolved || '' }
        alignmentEvidence[alignment.id] ||= { relation: 'supports', book_id: null, chunk_id: null, quote: '' }
      }
    }
  } catch (e) { ElMessage.error(e.message) }
}
const loadCandidates = async () => {
  if (props.selectedItem?.kind !== 'judgment') { counterCandidates.value = []; counterSearched.value = false; return }
  try { counterCandidates.value = await listResearchCounterevidence(props.selectedItem.id, props.scopeType, props.scopeId) }
  catch (e) { ElMessage.error(e.message) }
}
const follow = async (taskId, destination) => {
  const task = await subscribeTask(taskId, state => { destination.value = `${state.message || state.stage || '正在分析'} · ${Math.round((state.progress || 0) * 100)}%` })
  if (task.status !== 'done') throw new Error(task.error || '研究任务未完成')
  return task.result || {}
}
const previewConcept = async () => {
  busy.value = true
  try { conceptPreview.value = await previewResearchConcepts({ ...scope(), snapshot_id: props.snapshotId, term: term.value.trim(), aliases: aliasesList() }) }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const analyzeConcept = async () => {
  busy.value = true
  try {
    const queued = await analyzeResearchConcepts({ ...scope(), snapshot_id: props.snapshotId, term: term.value.trim(), aliases: aliasesList(), acknowledged: true })
    if (!queued.task_id) { taskMessage.value = '当前范围未定位到这个术语；可补充作者原词后重试。'; return }
    const result = await follow(queued.task_id, taskMessage)
    taskMessage.value = result.candidate_ids?.length ? `已保存 ${result.candidate_ids.length} 条待复核义项。` : '本次没有新增可核对的义项。'
    await loadCards(); emit('archive-updated')
  } catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const toggleSense = id => { selectedSenses.value = selectedSenses.value.includes(id) ? selectedSenses.value.filter(x => x !== id) : [...selectedSenses.value.slice(-1), id] }
const alignSenses = async () => {
  busy.value = true
  try { const queued = await alignResearchConcepts({ ...scope(), snapshot_id: props.snapshotId, sense_ids: selectedSenses.value, acknowledged: true }); await follow(queued.task_id, taskMessage); selectedSenses.value = []; await loadCards(); emit('archive-updated'); ElMessage.success('可比性判断已进入待复核区') }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const mergeSense = async sense => {
  try { await mergeResearchConcept(sense.id, { ...scope(), target_term: mergeTerms[sense.id] }); await loadCards(); emit('archive-updated'); ElMessage.success('义项已归入指定概念分组；请继续核对是否可比') }
  catch (e) { ElMessage.error(e.message) }
}
const confirmSense = async sense => {
  const meaning = (senseEdits[sense.id] || '').trim()
  if (meaning.length < 2) { ElMessage.warning('请写下核对后的义项'); return }
  try { await updateResearchItem(sense.id, { ...scope(), statement: `${sense.detail.original_term}：${meaning}`, detail: { ...sense.detail, meaning }, review_status: 'confirmed' }); await loadCards(); emit('archive-updated'); ElMessage.success('义项已由你确认') }
  catch (e) { ElMessage.error(e.message) }
}
const confirmAlignment = async alignment => {
  const form = alignmentForms[alignment.id]
  try { await reviewResearchAlignment(alignment.id, { ...scope(), ...form, review_status: 'confirmed' }); await loadCards(); emit('archive-updated'); ElMessage.success('可比性判断已核定') }
  catch (e) { ElMessage.error(e.message) }
}
const saveAlignmentEvidence = async alignment => {
  try { await addResearchEvidence(alignment.id, { ...scope(), ...alignmentEvidence[alignment.id] }); alignmentEvidence[alignment.id] = { relation: 'supports', book_id: null, chunk_id: null, quote: '' }; await loadCards(); emit('archive-updated'); ElMessage.success('原文依据已关联到概念卡') }
  catch (e) { ElMessage.error(e.message) }
}
const toggleRevisions = async id => {
  if (revisionsByItem[id]) { delete revisionsByItem[id]; return }
  try { revisionsByItem[id] = await listResearchRevisions(id, props.scopeType, props.scopeId) }
  catch (e) { ElMessage.error(e.message) }
}
const previewCounter = async () => {
  busy.value = true
  try { counterPreview.value = await previewResearchCounterevidence({ ...scope(), item_id: props.selectedItem.id }) }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const searchCounter = async () => {
  busy.value = true
  try { const queued = await searchResearchCounterevidence({ ...scope(), item_id: props.selectedItem.id, acknowledged: true }); const result = await follow(queued.task_id, counterMessage); counterSearched.value = true; counterMessage.value = result.candidate_ids?.length ? `送模型核对的候选原文覆盖 ${result.retrieved_books}/${result.total_books} 本文献，得到 ${result.candidate_ids.length} 条待核对反证。` : `本次送模型核对的候选原文覆盖 ${result.retrieved_books || 0}/${result.total_books || 0} 本文献，未筛出反证；不表示不存在反证。`; await loadCandidates(); emit('archive-updated') }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const acceptCandidate = async candidate => {
  try { await acceptResearchCounterevidence(candidate.id, { ...scope(), revised_statement: revisedStatements[candidate.id].trim() }); await loadCandidates(); emit('archive-updated'); ElMessage.success('反证及修订已记录') }
  catch (e) { ElMessage.error(e.message) }
}
const rejectCandidate = async candidate => {
  try { await updateResearchItem(candidate.id, { ...scope(), review_status: 'rejected' }); await loadCandidates(); ElMessage.success('候选已标记为不相关') }
  catch (e) { ElMessage.error(e.message) }
}
watch(() => [props.scopeType, props.scopeId, props.snapshotId], () => { conceptPreview.value = null; counterPreview.value = null; selectedSenses.value = []; loadCards(); loadCandidates() }, { immediate: true })
watch(() => props.selectedItem?.id, () => { counterPreview.value = null; counterMessage.value = ''; counterSearched.value = false; loadCandidates() })
watch([term, aliases], () => { conceptPreview.value = null })
</script>

<style scoped>
.discovery-panel { margin-top: 24px; }
.hint, .preview small, .sense small, .counter-card small { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
.preview, .concept-card, .counter-card { border: 1px solid var(--el-border-color); border-radius: 8px; padding: 12px; margin: 10px 0; }
.preview { background: var(--el-fill-color-light); }
.concept-card h4 { margin: 2px 0 12px; }
.sense-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
.sense { padding: 10px; border: 1px solid var(--el-border-color-light); border-radius: 6px; }
.sense p, .counter-card p { line-height: 1.6; }
.sense dl { display: grid; grid-template-columns: 110px 1fr; gap: 3px 6px; font-size: 12px; }
.sense dt { color: var(--el-text-color-secondary); }
.sense dd { margin: 0; }
.quote { font-size: 12px; line-height: 1.6; padding: 7px; border-left: 2px solid var(--el-color-primary-light-3); background: var(--el-fill-color-light); }
.alignment { padding: 9px; margin-top: 10px; border-left: 3px solid var(--el-color-warning); background: var(--el-fill-color-light); }
.evidence-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 9px 0; }
.revisions { padding: 4px 8px; font-size: 12px; background: var(--el-fill-color-light); }
.review-row { flex-wrap: nowrap; }
.review-row .el-input { flex: 1; }
</style>
