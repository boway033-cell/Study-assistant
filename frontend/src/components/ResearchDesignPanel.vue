<template>
  <section v-if="selectedItem?.kind === 'judgment' && selectedItem.snapshot_id === snapshotId" class="design-panel">
    <el-divider />
    <h3>区分性阅读与研究设计陪练</h3>
    <p class="hint">先写出两种竞争解释各自依赖的前提，以及若它成立应观察到什么。AI 只提出待复核的阅读路径。</p>
    <div v-for="(rival, index) in rivals" :key="index" class="rival">
      <b>解释 {{ index === 0 ? 'A' : 'B' }}</b>
      <el-input v-model="rival.statement" placeholder="竞争解释" maxlength="1000" />
      <el-input v-model="rival.premise" placeholder="这项解释依赖的前提" maxlength="1000" />
      <el-input v-model="rival.prediction" placeholder="如果成立，应观察到什么" maxlength="1000" />
    </div>
    <el-input v-model="interest" placeholder="你最关心的群体、时期或机制（选填）" maxlength="500" />
    <div class="row"><el-button :loading="busy" @click="saveRivals">保存解释与预测</el-button><el-button :disabled="!snapshotCurrent || selectedItem.review_status !== 'confirmed'" :loading="busy" @click="previewPlan">预览阅读任务</el-button></div>
    <div v-if="planPreview" class="preview">
      <p>本次范围：{{ planPreview.books?.map(b => b.title).join('、') }}</p>
      <p>研究模型：{{ planPreview.provider || '当前配置' }} / {{ planPreview.model }} · 约 {{ planPreview.estimated_calls }} 次、{{ planPreview.estimated_tokens }} tokens<span v-if="planPreview.estimated_cost_cny != null">、¥{{ planPreview.estimated_cost_cny }}</span></p>
      <small>{{ planPreview.notice }}</small>
      <div class="row"><el-button type="primary" :disabled="!planPreview.within_budget || busy" @click="generatePlan">确认并生成 1–3 项</el-button></div>
    </div>
    <p v-if="message" class="hint">{{ message }}</p>
    <div class="row"><b>阅读任务</b><el-button link @click="load">刷新</el-button><el-button v-if="tasks.length > 3" link @click="showAllTasks = !showAllTasks">{{ showAllTasks ? '只看优先 3 项' : `查看全部 ${tasks.length} 项` }}</el-button></div>
    <p v-if="!tasks.length" class="hint">暂无阅读任务。任务会优先显示未解决或来源变化的项目。</p>
    <article v-for="task in visibleTasks" :key="task.id" class="task">
      <div class="row"><el-tag :type="task.detail.target_type === 'source_page' ? 'primary' : 'warning'">{{ task.detail.target_type === 'source_page' ? '待核对原页' : '需找外部资料' }}</el-tag><el-tag v-if="task.detail.outcome">{{ outcomeLabel(task.detail.outcome) }}</el-tag><el-tag v-if="task.evidence.some(e => e.source_status !== 'current')" type="danger">来源已变化</el-tag></div>
      <b>{{ task.statement }}</b>
      <p class="hint">生成时的判断：{{ task.detail.judgment_at_generation }}</p>
      <p class="hint">生成时的解释：A {{ task.detail.rivals?.[0]?.statement }}；B {{ task.detail.rivals?.[1]?.statement }}</p>
      <p>若 A 成立：{{ task.detail.expected_a }}<br>若 B 成立：{{ task.detail.expected_b }}</p>
      <p class="hint">为何可能改变判断：{{ task.detail.why_change }}<br>目前缺口：{{ task.detail.evidence_gap }}</p>
      <p v-if="task.detail.target_type === 'external_data'">下一步找：{{ task.detail.external_data_type }}。当前快照中没有可直接核对的原页。</p>
      <div v-for="e in task.evidence" :key="e.id"><p>《{{ e.book_title }}》第 {{ e.page_start }} 页：“{{ e.quote }}”</p><el-button v-if="e.source_status === 'current'" link type="primary" @click="$emit('open-source', e)">打开原页</el-button></div>
      <div v-if="!task.detail.outcome" class="row"><el-select v-model="outcomes[task.id]" placeholder="复核结果" style="width: 190px"><el-option v-for="key in outcomeKeys" :key="key" :value="key" :label="outcomeLabel(key)" /></el-select><el-input v-model="notes[task.id]" placeholder="你看到了什么、仍有什么疑问" style="min-width: 200px; flex: 1" maxlength="1200" /><el-button :disabled="!outcomes[task.id] || task.evidence.some(e => e.source_status !== 'current') || (task.detail.target_type === 'external_data' && outcomes[task.id] !== 'unclear' && (externalReferences[task.id] || '').trim().length < 8)" @click="recordOutcome(task)">记录复核</el-button></div>
      <el-input v-if="!task.detail.outcome" v-model="revisedStatements[task.id]" placeholder="复核后如需修改当前判断，可在这里填写（选填）" maxlength="4000" />
      <el-input v-if="!task.detail.outcome && task.detail.target_type === 'external_data'" v-model="externalReferences[task.id]" placeholder="外部资料的作者、标题、网址或数据标识；判断 A/B 时必填（用户提供，尚未经系统核验）" maxlength="1000" />
      <p v-else class="hint">你的复核：{{ task.detail.outcome_note || outcomeLabel(task.detail.outcome) }}。<span v-if="task.detail.external_reference">外部来源线索（用户提供，未自动核验）：{{ task.detail.external_reference }}。</span>已写入判断的修订历史。</p>
    </article>
    <el-divider />
    <div class="row"><b>研究设计陪练</b><el-button link @click="load">刷新</el-button></div>
    <p class="hint">按全文研读识别的材料类型，每轮只问一个具体薄弱点。先完成这本文献的“理解这篇”。</p>
    <div class="row"><el-select v-model="coachBookId" placeholder="选一本文献" style="min-width: 230px"><el-option v-for="book in books" :key="book.book_id" :label="book.title" :value="book.book_id" /></el-select><el-button :disabled="!snapshotCurrent || !coachBookId" @click="previewQuestion">预览陪练</el-button></div>
    <div v-if="coachPreview" class="preview"><p>材料类型：{{ materialLabel(coachPreview.material_type) }} · 《{{ coachPreview.book_title }}》第 {{ coachPreview.page }} 页</p><p class="hint">追问方向：{{ coachPreview.guidance }}</p><p>约 {{ coachPreview.estimated_calls }} 次、{{ coachPreview.estimated_tokens }} tokens<span v-if="coachPreview.estimated_cost_cny != null">、¥{{ coachPreview.estimated_cost_cny }}</span>；{{ coachPreview.notice }}</p><el-button type="primary" :disabled="!coachPreview.within_budget || busy" @click="startQuestion">确认并提问</el-button></div>
    <article v-for="turn in turns" :key="turn.id" class="task">
      <el-tag size="small">{{ materialLabel(turn.detail.material_type) }}</el-tag>
      <p><b>{{ turn.statement }}</b></p><p class="hint">薄弱点：{{ turn.detail.weak_point }}</p>
      <div v-for="e in turn.evidence" :key="e.id"><p>原文短引：“{{ e.quote }}” · 第 {{ e.page_start }} 页</p><el-button v-if="e.source_status === 'current'" link type="primary" @click="$emit('open-source', e)">打开原页</el-button><el-tag v-else type="warning">来源已变化</el-tag></div>
      <template v-if="!turn.detail.answer"><el-input v-model="answers[turn.id]" type="textarea" :rows="3" maxlength="3000" placeholder="先用自己的话回答" /><div class="row"><el-button :disabled="(answers[turn.id] || '').trim().length < 8 || turn.evidence.some(e => e.source_status !== 'current')" @click="previewAnswer(turn)">预览反馈</el-button><el-button v-if="feedbackPreviews[turn.id]" type="primary" :disabled="!feedbackPreviews[turn.id].within_budget || busy" @click="submitAnswer(turn)">确认并提交回答</el-button></div><p v-if="feedbackPreviews[turn.id]" class="hint">研究模型约 {{ feedbackPreviews[turn.id].estimated_calls }} 次、{{ feedbackPreviews[turn.id].estimated_tokens }} tokens。{{ feedbackPreviews[turn.id].notice }}</p></template>
      <template v-else><p>你的回答：{{ turn.detail.answer }}</p><template v-if="turn.detail.feedback"><p><b>原文如此说：</b>“{{ turn.detail.feedback.source_quote }}”</p><p><b>AI 对原文的解读（待核对）：</b>{{ turn.detail.feedback.ai_source_interpretation }}</p><p><b>你的推断：</b>{{ turn.detail.feedback.user_inference }}</p><p><b>AI 下一步建议：</b>{{ turn.detail.feedback.ai_suggestion }}</p><p v-if="turn.detail.feedback.remaining_doubt" class="hint">仍存疑：{{ turn.detail.feedback.remaining_doubt }}</p></template><template v-else><p class="hint">反馈尚未生成；回答已保存在档案中。若后台任务失败，可用原回答重试。</p><el-button @click="previewAnswer(turn)">预览重试用量</el-button><el-button v-if="feedbackPreviews[turn.id]" type="primary" :disabled="!feedbackPreviews[turn.id].within_budget || busy" @click="submitAnswer(turn)">确认并重试反馈</el-button><p v-if="feedbackPreviews[turn.id]" class="hint">约 {{ feedbackPreviews[turn.id].estimated_calls }} 次、{{ feedbackPreviews[turn.id].estimated_tokens }} tokens。{{ feedbackPreviews[turn.id].notice }}</p></template></template>
    </article>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { subscribeTask, saveResearchRivals, previewResearchReadingTasks, generateResearchReadingTasks,
  listResearchReadingTasks, saveResearchReadingOutcome, previewResearchCoach, startResearchCoach,
  listResearchCoachTurns, previewResearchCoachFeedback, answerResearchCoach } from '../api'

const props = defineProps({ scopeType: String, scopeId: Number, snapshotId: Number, snapshotCurrent: Boolean, selectedItem: Object, books: { type: Array, default: () => [] } })
const emit = defineEmits(['open-source', 'archive-updated'])
const emptyRivals = () => [{ statement: '', premise: '', prediction: '' }, { statement: '', premise: '', prediction: '' }]
const rivals = ref(emptyRivals())
const interest = ref('')
const planPreview = ref(null)
const coachPreview = ref(null)
const tasks = ref([])
const showAllTasks = ref(false)
const visibleTasks = computed(() => showAllTasks.value ? tasks.value : tasks.value.slice(0, 3))
const turns = ref([])
const coachBookId = ref(null)
const busy = ref(false)
const message = ref('')
const outcomes = reactive({})
const notes = reactive({})
const externalReferences = reactive({})
const revisedStatements = reactive({})
const answers = reactive({})
const feedbackPreviews = reactive({})
const outcomeKeys = ['supports_a', 'supports_b', 'neither', 'unclear']
const scope = () => ({ scope_type: props.scopeType, scope_id: props.scopeId })
const parent = computed(() => props.selectedItem?.id)
const outcomeLabel = key => ({ supports_a: '支持 A', supports_b: '支持 B', neither: '两者都不支持', unclear: '仍不清楚' }[key] || key)
const materialLabel = key => ({ quantitative: '定量研究', qualitative: '质性研究', theoretical: '理论文本', review: '综述或政策报告', other: '其他材料' }[key] || key)
const load = async () => {
  if (!parent.value || props.selectedItem?.snapshot_id !== props.snapshotId) { tasks.value = []; turns.value = []; return }
  try { [tasks.value, turns.value] = await Promise.all([listResearchReadingTasks(parent.value, props.scopeType, props.scopeId), listResearchCoachTurns(parent.value, props.scopeType, props.scopeId)]) }
  catch (e) { ElMessage.error(e.message) }
}
const follow = async id => {
  const task = await subscribeTask(id, state => { message.value = `${state.message || state.stage || '正在分析'} · ${Math.round((state.progress || 0) * 100)}%` })
  if (task.status !== 'done') throw new Error(task.error || '任务未完成')
  return task.result || {}
}
const saveRivals = async () => {
  busy.value = true
  try { await saveResearchRivals(parent.value, { ...scope(), rivals: rivals.value, interest: interest.value }); planPreview.value = null; emit('archive-updated'); ElMessage.success('两种解释及预测已记录在修订历史') }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const previewPlan = async () => {
  busy.value = true
  try { planPreview.value = await previewResearchReadingTasks({ ...scope(), item_id: parent.value }) }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const generatePlan = async () => {
  busy.value = true
  try { const queued = await generateResearchReadingTasks({ ...scope(), item_id: parent.value, acknowledged: true }); const result = await follow(queued.task_id); message.value = result.task_ids?.length ? `已生成 ${result.task_ids.length} 项待复核任务。` : '没有新增可核对任务。'; await load() }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const recordOutcome = async task => {
  busy.value = true
  try { await saveResearchReadingOutcome(task.id, { ...scope(), outcome: outcomes[task.id], note: notes[task.id] || '', external_reference: externalReferences[task.id] || '', revised_statement: revisedStatements[task.id]?.trim() || null }); await load(); emit('archive-updated'); ElMessage.success('复核结果已加入判断修订历史') }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const previewQuestion = async () => {
  busy.value = true
  try { coachPreview.value = await previewResearchCoach({ ...scope(), item_id: parent.value, book_id: coachBookId.value }) }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const startQuestion = async () => {
  busy.value = true
  try { const queued = await startResearchCoach({ ...scope(), item_id: parent.value, book_id: coachBookId.value, acknowledged: true }); await follow(queued.task_id); message.value = '问题已生成。请先读原页，再写下你的回答。'; await load() }
  catch (e) { ElMessage.error(e.message) } finally { busy.value = false }
}
const previewAnswer = async turn => {
  try { feedbackPreviews[turn.id] = await previewResearchCoachFeedback(turn.id, props.scopeType, props.scopeId, (answers[turn.id] || turn.detail.answer || '').length) }
  catch (e) { ElMessage.error(e.message) }
}
const submitAnswer = async turn => {
  busy.value = true
  try { const queued = await answerResearchCoach(turn.id, { ...scope(), answer: (answers[turn.id] || turn.detail.answer).trim(), acknowledged: true }); await load(); await follow(queued.task_id); await load(); message.value = '反馈已生成；原文、你的推断与 AI 建议已分别标明。' }
  catch (e) { await load(); ElMessage.error(e.message) } finally { busy.value = false }
}
watch([parent, () => props.snapshotId], () => { const detail = props.selectedItem?.detail || {}; rivals.value = Array.isArray(detail.rivals) && detail.rivals.length === 2 ? detail.rivals.map(x => ({ ...x })) : emptyRivals(); interest.value = detail.reading_interest || ''; planPreview.value = null; coachPreview.value = null; load() }, { immediate: true })
</script>

<style scoped>
.design-panel { margin-top: 20px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 9px 0; }
.rival, .task, .preview { border: 1px solid var(--el-border-color); border-radius: 8px; padding: 12px; margin: 10px 0; }
.rival .el-input { margin-top: 8px; }
.preview { background: var(--el-fill-color-light); }
.task { line-height: 1.6; }
.task p { margin: 8px 0; }
</style>
