<template>
  <div class="report-workspace">
    <KnowledgeScopeSelector v-if="!embedded" />
    <div v-if="!knowledgeBookIds.length" class="scope-required">
      <b>先选择研究书目</b><span>未选择不会自动扩大到整个资料库。</span>
    </div>
    <div v-else class="workspace-grid">
      <aside class="source-pane panel">
        <header><b>研究材料</b><small>{{ knowledgeBookIds.length }} 本书 · {{ selectedChapterIds.length }} 章 · {{ selectedNoteIds.length }} 条笔记</small></header>
        <el-input v-model="sourceQuery" clearable placeholder="筛选章节或笔记" />
        <section>
          <h3>书目与章节</h3>
          <div v-for="book in materialBooks" :key="book.id" class="source-book">
            <b>《{{ book.title }}》</b>
            <el-checkbox-group v-model="selectedChapterIds">
              <el-checkbox v-for="chapter in filteredChapters(book.chapters)" :key="chapter.id" :value="chapter.id">{{ chapter.label }}</el-checkbox>
            </el-checkbox-group>
          </div>
        </section>
        <section>
          <h3>已有笔记</h3>
          <el-checkbox-group v-model="selectedNoteIds">
            <el-checkbox v-for="note in filteredNotes" :key="note.id" :value="note.id"><span>{{ note.title }}</span><small>《{{ note.book_title }}》</small></el-checkbox>
          </el-checkbox-group>
          <el-empty v-if="!filteredNotes.length" description="当前范围暂无知识笔记" :image-size="52" />
        </section>
      </aside>

      <main class="report-pane panel">
        <header>
          <div><b>研究报告工作区</b><small>由研究路由中的模型规划研究路径，来源约束负责防止脱离原文</small></div>
          <div class="report-head-actions"><el-button v-if="content" @click="copyReport">复制 Markdown</el-button><el-button v-if="content" type="primary" plain @click="immersive=true">沉浸阅读</el-button></div>
        </header>
        <div class="research-brief">
          <div class="method-row">
            <label>研读方式
              <el-select v-model="researchMode">
                <el-option v-for="item in researchModes" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </label>
            <label>推演深度
              <el-radio-group v-model="reasoningDepth">
                <el-radio-button value="standard">快速</el-radio-button>
                <el-radio-button value="deep">深度</el-radio-button>
              </el-radio-group>
            </label>
          </div>
          <p class="method-hint">{{ activeMode.description }} <span v-if="reasoningDepth==='deep'">深度模式先规划子问题，再按路径检索和综合。</span><span v-else>快速模式一次综合，适合先形成草稿。</span></p>
          <div class="method-row writing-options">
            <label>成文方式
              <el-select v-model="writingStyle"><el-option label="连贯分析文章" value="analytical_essay"/><el-option label="结构化研究报告" value="structured_report"/></el-select>
            </label>
            <label>推演自由度
              <el-select v-model="extensionLevel"><el-option label="探索性延伸（推荐）" value="exploratory"/><el-option label="紧贴直接证据" value="grounded"/></el-select>
            </label>
            <label>目标字数
              <el-input-number v-model="targetLength" :min="800" :max="12000" :step="500" controls-position="right" />
            </label>
          </div>
          <label>研究问题
            <el-input v-model="focus" type="textarea" :rows="3" maxlength="500" placeholder="例如：不同文献如何解释基层协同治理，其证据边界有何差异？" />
          </label>
          <label>用户补充维度（可选）
            <el-input v-model="framework" type="textarea" :rows="2" maxlength="400" placeholder="留空时由 AI 根据材料自主选择；也可补充必须比较的概念、案例或方法" />
          </label>
          <el-button type="primary" size="large" :disabled="loading" @click="generate">{{ loading ? '研读任务已在后台运行' : '生成研究报告' }}</el-button>
          <section v-if="loading" class="task-monitor" aria-live="polite">
            <header><div><b>研读进度</b><small>{{ activeTaskId }}</small></div><el-button link type="danger" @click="cancelActiveTask">停止任务</el-button></header>
            <el-progress :percentage="progress" :indeterminate="progress===0" :stroke-width="8" />
            <div class="task-stages">
              <span v-for="(item,index) in taskStages" :key="item.key" :class="{active:index===currentStageIndex,done:index<currentStageIndex}">{{ index < currentStageIndex ? '✓' : index + 1 }} {{ item.label }}</span>
            </div>
            <p>{{ stage }}</p><small>页面关闭或连接短暂中断不会丢失任务；返回本页会自动恢复进度与结果。</small>
          </section>
        </div>

        <section v-if="hasPlan" class="research-plan">
          <header><div><b>AI 研究路径</b><small>这是可审查的分析计划，不展示或保存模型隐性推理</small></div><el-tag size="small" effect="plain">{{ activePlan.material_type }}</el-tag></header>
          <div class="plan-columns">
            <div v-if="activePlan.subquestions?.length"><h3>子问题</h3><ol><li v-for="item in activePlan.subquestions" :key="item">{{ item }}</li></ol></div>
            <div v-if="activePlan.analysis_axes?.length"><h3>分析维度</h3><ul><li v-for="item in activePlan.analysis_axes" :key="item">{{ item }}</li></ul></div>
            <div v-if="activePlan.evidence_needs?.length"><h3>证据需求</h3><ul><li v-for="item in activePlan.evidence_needs" :key="item">{{ item }}</li></ul></div>
          </div>
        </section>

        <article v-if="content" class="markdown-body report-content" v-html="renderMarkdown(content)" />
        <el-empty v-else description="选择材料并提出研究问题；不勾选章节时，AI 会在所选书目范围内检索证据" />
        <footer v-if="content" class="output-actions">
          <el-button type="success" :loading="depositing" @click="depositReview">保存批判性审查到知识库</el-button>
          <el-button type="primary" plain @click="sendToPptx">发送到 PPTX 工作台</el-button>
        </footer>
      </main>

      <aside class="audit-pane panel">
        <header><div><b>来源与主张审计</b><small>{{ activeReport?.claims?.length || 0 }} 条主张</small></div><div class="claim-review-actions"><el-button v-if="activeReport?.claims?.length&&!editingClaims" size="small" text @click="beginClaimReview">人工复核</el-button><template v-if="editingClaims"><el-button size="small" text @click="cancelClaimReview">取消</el-button><el-button size="small" type="primary" :loading="savingClaims" @click="saveClaimReview">保存</el-button></template></div></header>
        <section class="source-summary"><h3>本次来源</h3><button v-for="book in scopedBooks" :key="book.id" @click="router.push(`/reader/${book.id}`)">《{{ book.title }}》 ↗</button></section>
        <section v-if="activeReport?.evidence_summary" class="evidence-summary"><h3>跨文献关系</h3><div><span>共识 <b>{{ relationCount('consensus') }}</b></span><span>互补 <b>{{ relationCount('complementary') }}</b></span><span>冲突 <b>{{ relationCount('conflict') }}</b></span><span>待定 <b>{{ relationCount('unresolved') }}</b></span></div></section>
        <section>
          <h3>可核验主张</h3>
          <article v-for="(claim,index) in displayedClaims" :key="index" class="claim-card" :class="{editing:editingClaims}">
            <div class="claim-meta"><span :class="claim.status">{{ statusLabel(claim.status) }}</span><span>{{ relationLabel(claim.synthesis_relation) }}</span><span>{{ qualityLabel(claim.evidence_quality) }}</span><small>{{ confidenceLabel(claim.confidence) }}</small></div>
            <template v-if="editingClaims"><el-input v-model="claim.claim" type="textarea" :rows="2" maxlength="1000" /><div class="claim-edit-grid"><el-select v-model="claim.status"><el-option label="支持" value="supported"/><el-option label="部分支持" value="partial"/><el-option label="待核验" value="needs_review"/><el-option label="不支持" value="unsupported"/></el-select><el-select v-model="claim.synthesis_relation"><el-option label="共识" value="consensus"/><el-option label="互补" value="complementary"/><el-option label="冲突" value="conflict"/><el-option label="单一来源" value="single_source"/><el-option label="待定" value="unresolved"/></el-select><el-select v-model="claim.evidence_quality"><el-option label="高质量" value="high"/><el-option label="中等质量" value="moderate"/><el-option label="低质量" value="low"/><el-option label="极低质量" value="very_low"/><el-option label="未评估" value="not_assessed"/></el-select></div><el-input v-model="claim.reason" type="textarea" :rows="2" placeholder="判断理由"/><el-input v-model="claim.counterpoint" type="textarea" :rows="2" placeholder="反例、限制或适用边界"/><el-checkbox v-model="claim.human_review_required">仍需进一步人工核验</el-checkbox></template><p v-else>{{ claim.claim }}</p>
            <div class="source-links"><button v-for="ref in claim.source_refs || []" :key="ref" @click="openSourceRef(ref)">{{ ref }} ↗</button><small v-if="!(claim.source_refs || []).length">无有效来源锚点</small></div>
            <em>{{ claim.reason }}</em><em v-if="claim.counterpoint" class="counterpoint">限制：{{ claim.counterpoint }}</em>
            <em v-if="claim.bias_flags?.length" class="counterpoint">偏倚风险：{{ claim.bias_flags.join('；') }}</em>
            <em v-if="claim.alternative_explanations?.length" class="counterpoint">竞争解释：{{ claim.alternative_explanations.join('；') }}</em>
          </article>
          <el-empty v-if="!(activeReport?.claims || []).length" description="生成后显示逐条主张与来源" :image-size="54" />
        </section>
        <section v-if="activeReport?.open_questions?.length" class="open-questions"><h3>待继续核查</h3><ul><li v-for="item in activeReport.open_questions" :key="item">{{ item }}</li></ul></section>
        <section v-if="activeReport?.hypotheses?.length" class="open-questions"><h3>候选假设（需人工检验）</h3><ul><li v-for="item in activeReport.hypotheses" :key="item.statement"><b>{{ item.statement }}</b><small v-if="item.falsifier">挑战条件：{{ item.falsifier }}</small></li></ul></section>
        <section class="history"><h3>历史报告</h3><button v-for="report in scopedReports" :key="report.id" :class="{active:activeReport?.id===report.id}" @click="openReport(report)">{{ report.created_at?.slice(0,16).replace('T',' ') }}<small>{{ report.focus || '未命名研究问题' }}</small></button></section>
      </aside>
    </div>
    <el-drawer v-model="immersive" :title="activeReport?.focus || '研究报告'" size="100%" destroy-on-close class="immersive-report-drawer">
      <div class="immersive-report">
        <main><div class="immersive-kicker">CRITICAL REVIEW · {{ activeReport?.research_mode || 'adaptive' }}</div><article class="markdown-body" v-html="renderMarkdown(content)" /></main>
        <aside><h2>证据审查</h2><div class="immersive-counts"><span>共识 <b>{{ relationCount('consensus') }}</b></span><span>互补 <b>{{ relationCount('complementary') }}</b></span><span>冲突 <b>{{ relationCount('conflict') }}</b></span><span>待定 <b>{{ relationCount('unresolved') }}</b></span></div><article v-for="(claim,index) in activeReport?.claims || []" :key="index"><b>{{ claim.claim }}</b><small>{{ relationLabel(claim.synthesis_relation) }} · {{ qualityLabel(claim.evidence_quality) }} · {{ statusLabel(claim.status) }}</small></article></aside>
      </div>
      <template #footer><div class="note-reader-actions"><el-button type="success" :loading="depositing" @click="depositReview">保存批判性审查到知识库</el-button><el-button @click="immersive=false">完成阅读</el-button></div></template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
import { knowledgeBooks, knowledgeBookIds, loadKnowledgeBooks } from '../stores/knowledgeScope'
import { cancelTask, getBook, subscribeTask, studyOverview, studyReports, getStudyReport, listKnowledgeRecords, depositStudyReport, updateStudyReportClaims } from '../api'
import { renderMarkdown } from '../utils/markdown'
import { notifyTaskSubmitted } from '../stores/taskCenter'

defineProps({ embedded: { type: Boolean, default: false } })
const researchModes = [
  { value: 'adaptive', label: 'AI 自主研读（推荐）', description: '先判断材料与问题类型，再自主选择分析维度和报告结构，不套用固定期刊模板。' },
  { value: 'comparative', label: '观点与理论比较', description: '比较概念、机制、证据和适用边界，只保留对当前问题有效的维度。' },
  { value: 'critical', label: '证据与方法审查', description: '重点检查证据强度、替代解释、反例、方法限制与因果外推。' },
  { value: 'gap', label: '研究缺口探索', description: '梳理共识与分歧，找出材料尚未回答的问题和下一步研究方向。' },
]
const router = useRouter()
const route = useRoute()
const materialBooks = ref([]), notes = ref([]), selectedChapterIds = ref([]), selectedNoteIds = ref([])
const sourceQuery = ref(''), focus = ref(''), framework = ref(''), content = ref('')
const researchMode = ref('adaptive'), reasoningDepth = ref('deep')
const writingStyle = ref('analytical_essay'), extensionLevel = ref('exploratory'), targetLength = ref(3000)
const loading = ref(false), progress = ref(0), stage = ref(''), reports = ref([]), activeReport = ref(null)
const activeTaskId = ref('')
const livePlan = ref({})
const taskStages = [
  { key: 'overview', label: '汇总材料' }, { key: 'research-plan', label: '规划问题' },
  { key: 'evidence', label: '检索证据' }, { key: 'synthesis', label: '综合写作' },
  { key: 'done', label: '保存报告' },
]
let taskAbortController = null
const ACTIVE_TASK_KEY = 'sa-study-active-task'
const depositing = ref(false)
const editingClaims=ref(false),savingClaims=ref(false),claimDraft=ref([])
const immersive = ref(false)
const activeMode = computed(() => researchModes.find(item => item.value === researchMode.value) || researchModes[0])
const activePlan = computed(() => activeReport.value?.research_plan || livePlan.value || {})
const currentStageIndex = computed(() => {
  if (progress.value >= 100) return taskStages.length - 1
  const index = taskStages.findIndex(item => item.key === stageKey.value)
  return index < 0 ? 0 : index
})
const stageKey = ref('overview')
const hasPlan = computed(() => ['subquestions', 'analysis_axes', 'evidence_needs'].some(key => activePlan.value[key]?.length))
const scopedBooks = computed(() => knowledgeBooks.value.filter(book => knowledgeBookIds.value.includes(book.id)))
const sameScope = (ids = []) => { const a = [...ids].sort((x,y) => x-y), b = [...knowledgeBookIds.value].sort((x,y) => x-y); return a.length === b.length && a.every((id,i) => id === b[i]) }
const scopedReports = computed(() => reports.value.filter(report => sameScope(report.book_ids)))
const filteredNotes = computed(() => notes.value.filter(note => !sourceQuery.value || `${note.title}${note.book_title}`.toLowerCase().includes(sourceQuery.value.toLowerCase())))
const filteredChapters = items => items.filter(item => !sourceQuery.value || item.label.toLowerCase().includes(sourceQuery.value.toLowerCase()))
const displayedClaims=computed(()=>editingClaims.value?claimDraft.value:(activeReport.value?.claims||[]))
const flatten = (nodes, depth = 0, out = []) => { for (const node of nodes) { out.push({ id: node.id, label: '　'.repeat(depth) + node.title }); flatten(node.children || [], depth + 1, out) } return out }
const loadMaterials = async () => { const books = []; for (const book of scopedBooks.value) { try { const detail = await getBook(book.id); books.push({ ...book, chapters: flatten(detail.chapters || []) }) } catch { books.push({ ...book, chapters: [] }) } } materialBooks.value = books; try { const result = await listKnowledgeRecords({ book_ids: knowledgeBookIds.value, record_types: ['note'] }); notes.value = result.items || [] } catch { notes.value = [] } }
const loadReports = async () => { try { const result=await studyReports(1,50); reports.value = result.items || [] } catch (error) { ElMessage.error(error.message) } }
const openReport = async report => { try { const full=report.content!==undefined?report:await getStudyReport(report.id); editingClaims.value=false;claimDraft.value=[]; activeReport.value = full; content.value = full.content; focus.value = full.focus || focus.value; framework.value = full.framework || framework.value; researchMode.value = full.research_mode || full.selection?.research_mode || 'adaptive'; reasoningDepth.value = full.reasoning_depth || full.selection?.reasoning_depth || 'standard'; writingStyle.value=full.writing_style||full.selection?.writing_style||'analytical_essay';extensionLevel.value=full.extension_level||full.selection?.extension_level||'exploratory';targetLength.value=Number(full.target_length||full.selection?.target_length)||3000; selectedChapterIds.value = full.selection?.chapter_ids || []; selectedNoteIds.value = full.selection?.note_ids || [] } catch(error){ ElMessage.error(`无法打开研究报告：${error.message}`) } }
const followStudyTask = async taskId => {
  taskAbortController?.abort()
  const controller = new AbortController()
  taskAbortController = controller
  activeTaskId.value = taskId
  localStorage.setItem(ACTIVE_TASK_KEY, taskId)
  loading.value = true
  try {
    const task = await subscribeTask(taskId, next => {
      progress.value = Math.round((next.progress || 0) * 100)
      stage.value = next.message || next.stage || '处理中'
      stageKey.value = next.stage || stageKey.value
      if (next.result?.research_plan) livePlan.value = next.result.research_plan
    }, { signal: controller.signal })
    if (task.status === 'failed' || task.status === 'cancelled') throw new Error(task.error || task.message || '任务未完成')
    progress.value = 100
    stageKey.value = 'done'
    await loadReports()
    const latest = reports.value.find(report => report.id === task.result?.report_id) || scopedReports.value[0]
    if (latest) await openReport(latest)
    ElMessage.success('研究报告、研究路径与主张审计已完成')
  } catch (error) {
    if (error?.name === 'AbortError') return
    ElMessage.error(`生成失败：${error.message}`)
  } finally {
    if (taskAbortController === controller && !controller.signal.aborted) {
      localStorage.removeItem(ACTIVE_TASK_KEY)
      loading.value = false
      activeTaskId.value = ''
    }
  }
}
const generate = async () => {
  if (!focus.value.trim()) return ElMessage.warning('请先写明研究问题')
  loading.value = true
  progress.value = 0
  stageKey.value = 'overview'
  stage.value = '正在提交到全局任务中心'
  livePlan.value = {}
  try {
    const response = await studyOverview({ book_ids: knowledgeBookIds.value, chapter_ids: selectedChapterIds.value, note_ids: selectedNoteIds.value, focus: focus.value.trim(), framework: framework.value.trim(), research_mode: researchMode.value, reasoning_depth: reasoningDepth.value, writing_style:writingStyle.value, extension_level:extensionLevel.value, target_length:targetLength.value })
    notifyTaskSubmitted()
    followStudyTask(response.task_id)
  } catch (error) {
    loading.value = false
    ElMessage.error(`无法提交研读任务：${error.message}`)
  }
}
const cancelActiveTask = async () => {
  if (!activeTaskId.value) return
  try {
    await cancelTask(activeTaskId.value)
    stage.value = '正在安全停止任务…'
  } catch (error) { ElMessage.error(`无法停止任务：${error.message}`) }
}
const copyReport = async () => { try { await navigator.clipboard.writeText(content.value); ElMessage.success('Markdown 已复制') } catch { ElMessage.warning('请手动复制') } }
const depositReview = async () => { if(!activeReport.value?.id)return ElMessage.warning('请先生成或打开报告');depositing.value=true;try{const result=await depositStudyReport(activeReport.value.id,{include_report_note:true,include_claim_cards:true});await loadMaterials();ElMessage.success(`已沉淀审查笔记和 ${result.claim_count} 张证据卡；重复保存不会产生副本`)}catch(error){ElMessage.error(error.message)}finally{depositing.value=false} }
const beginClaimReview=()=>{claimDraft.value=JSON.parse(JSON.stringify(activeReport.value?.claims||[]));editingClaims.value=true}
const cancelClaimReview=()=>{claimDraft.value=[];editingClaims.value=false}
const saveClaimReview=async()=>{if(claimDraft.value.some(claim=>!claim.claim?.trim()))return ElMessage.warning('主张文本不能为空');savingClaims.value=true;try{activeReport.value=await updateStudyReportClaims(activeReport.value.id,claimDraft.value);editingClaims.value=false;claimDraft.value=[];await loadReports();ElMessage.success('人工复核结果已保存，后续沉淀将使用修订后的证据台账')}catch(error){ElMessage.error(error.message)}finally{savingClaims.value=false}}
const sendToPptx = () => { sessionStorage.setItem('deckSelectedText', content.value); sessionStorage.setItem('deckSourceBookIds', JSON.stringify(knowledgeBookIds.value)); sessionStorage.setItem('deckSourceReportId', String(activeReport.value?.id || '')); sessionStorage.setItem('deckSourceChapterIds', JSON.stringify(selectedChapterIds.value)); router.push({ path: '/literature-workbench', query: { from: 'research-report', bookId: knowledgeBookIds.value[0] } }) }
const openSourceRef = refText => { const match = String(refText).match(/^B(\d+)(?::CH\d+)?(?::P(\d+)(?:-\d+)?)?/); if (!match) return ElMessage.warning('来源锚点无法定位'); router.push({ path: `/reader/${match[1]}`, query: match[2] ? { page: match[2] } : {} }) }
const statusLabel = status => ({ supported: '支持', partial: '部分支持', needs_review: '待核验', unsupported: '不支持' }[status] || '待核验')
const typeLabel = type => ({ descriptive: '事实描述', associational: '关联判断', causal: '因果判断', interpretive: '解释判断' }[type] || '解释判断')
const confidenceLabel = confidence => ({ high: '高置信', medium: '中置信', low: '低置信' }[confidence] || '低置信')
const relationLabel = relation => ({ consensus:'共识', complementary:'互补', conflict:'冲突', single_source:'单一来源', unresolved:'待定' }[relation] || '待定')
const qualityLabel = quality => ({ high:'高质量', moderate:'中等质量', low:'低质量', very_low:'极低质量', not_assessed:'未评估' }[quality] || '未评估')
const relationCount = relation => activeReport.value?.evidence_summary?.relations?.[relation] || 0
const resetScope = async () => { activeReport.value = null; content.value = ''; selectedChapterIds.value = []; selectedNoteIds.value = []; await loadMaterials() }
watch(knowledgeBookIds, resetScope, { deep: true })
onMounted(async () => {
  await loadKnowledgeBooks()
  await Promise.all([loadMaterials(), loadReports()])
  const reportId = Number(route.query.reportId)
  if (Number.isInteger(reportId) && reportId > 0) {
    const report = reports.value.find(item => item.id === reportId) || { id: reportId }
    await openReport(report)
  }
  const taskId = String(route.query.taskId || localStorage.getItem(ACTIVE_TASK_KEY) || '')
  if (taskId) followStudyTask(taskId)
})
onBeforeUnmount(() => taskAbortController?.abort())
</script>

<style scoped>
.report-workspace{max-width:1560px}.scope-required{display:flex;flex-direction:column;gap:6px;padding:40px;border:1px dashed #d8c7b0;border-radius:12px;background:#f7f2e9;text-align:center}.workspace-grid{display:grid;grid-template-columns:280px minmax(520px,1fr) 320px;gap:10px;align-items:start}.panel{overflow:hidden;border:1px solid #e5e7eb;border-radius:11px;background:#fffdf9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.panel>header{display:flex;align-items:center;justify-content:space-between;padding:13px 14px;border-bottom:1px solid #ece4d8}.panel>header>div,.source-pane header,.audit-pane header{display:flex;flex-direction:column}.panel header small{color:var(--el-text-color-secondary);font-size:11px}.source-pane>.el-input{margin:10px;width:calc(100% - 20px)}.source-pane section,.audit-pane>section{padding:0 12px 12px;border-top:1px solid #eee7dc}.panel h3{margin:12px 0 8px;color:#786b5b;font-size:12px}.source-book>b{display:block;margin:9px 0 4px;font-size:13px}.source-pane :deep(.el-checkbox-group){display:grid;gap:2px}.source-pane :deep(.el-checkbox){height:auto;margin:0;padding:4px 0;white-space:normal}.source-pane :deep(.el-checkbox__label){display:flex;min-width:0;flex-direction:column;font-size:12px}.source-pane :deep(.el-checkbox__label small){color:#988b7b}.report-pane{min-height:700px}.research-brief{display:grid;gap:10px;padding:14px;background:#f8f3ea}.method-row{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:12px}.research-brief label{display:grid;gap:5px;color:#675d50;font-size:12px;font-weight:700}.method-hint{margin:0;padding:8px 10px;border-left:2px solid #b98a58;background:rgba(255,255,255,.55);color:#75695c;font-size:11px;line-height:1.6}.progress span{font-size:11px;color:#7b7165}.research-plan{margin:14px;border:1px solid #e7ddcf;border-radius:9px;background:#fffcf7}.research-plan>header{display:flex;align-items:center;justify-content:space-between;padding:11px 12px;border-bottom:1px solid #eee5d9}.research-plan>header div{display:flex;flex-direction:column}.research-plan>header small{color:#8b7f71;font-size:10px}.plan-columns{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px;padding:0 12px 10px}.plan-columns ol,.plan-columns ul,.open-questions ul{margin:0;padding-left:18px;color:#5e554b;font-size:11px;line-height:1.65}.report-content{max-height:calc(100vh - 430px);min-height:300px;overflow-y:auto;padding:18px 24px;line-height:1.9}.output-actions{display:flex;justify-content:flex-end;gap:7px;padding:12px;border-top:1px solid #ece4d8}.source-summary{display:grid;gap:5px}.source-summary button,.history button{padding:7px;border:0;border-radius:7px;background:#f7f1e7;color:#82582e;cursor:pointer;text-align:left;transition:transform .15s ease,background-color .15s ease}.source-summary button:hover,.history button:hover,.source-links button:hover{transform:translateY(-1px);background:#f0e5d5}.claim-card{margin-bottom:8px;padding:9px;border:1px solid #ece3d7;border-radius:8px;box-shadow:0 1px 2px rgba(15,23,42,.04)}.claim-meta{display:flex;align-items:center;gap:7px}.claim-meta span,.claim-meta small{font-size:10px}.claim-meta span:first-child{font-weight:700}.claim-meta span.supported{color:#327052}.claim-meta span.partial{color:#8b6a20}.claim-meta span.needs_review{color:#b5661f}.claim-meta span.unsupported{color:#ad4242}.claim-meta small{margin-left:auto;color:#948779}.claim-card p{margin:7px 0;font-size:12px;line-height:1.55}.claim-card em{display:block;color:#817569;font-size:11px;font-style:normal;line-height:1.5}.claim-card .counterpoint{margin-top:4px;color:#8b5f46}.source-links{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}.source-links button{padding:2px 5px;border:0;border-radius:4px;background:#f5eee3;color:#8a5d31;cursor:pointer;font-size:9px;transition:transform .15s ease,background-color .15s ease}.source-links small{color:#a39688;font-size:10px}.history{display:grid;gap:5px}.history button{display:flex;flex-direction:column}.history button.active{outline:1px solid #b98a58}.history small{overflow:hidden;color:#82776a;text-overflow:ellipsis;white-space:nowrap}.open-questions li+li{margin-top:4px}@media(max-width:1240px){.workspace-grid{grid-template-columns:250px minmax(480px,1fr)}.audit-pane{grid-column:1/-1}}@media(max-width:780px){.workspace-grid{grid-template-columns:1fr}.audit-pane{grid-column:auto}.method-row,.plan-columns{grid-template-columns:1fr}.output-actions{flex-wrap:wrap;justify-content:flex-start}}
.evidence-summary>div{display:grid;grid-template-columns:1fr 1fr;gap:5px}.evidence-summary span{display:flex;justify-content:space-between;padding:6px 7px;border-radius:6px;background:#f7f1e7;color:#786b5b;font-size:11px}.claim-review-actions{display:flex!important;align-items:center;flex-direction:row!important}.claim-card.editing{display:grid;gap:7px}.claim-edit-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px}.open-questions li{display:flex;flex-direction:column}.open-questions li small{margin-top:3px;color:#8b7460}
.report-head-actions{display:flex;gap:7px}.immersive-report{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:48px;width:min(100%,1280px);margin:0 auto;padding:10px 8px 72px}.immersive-report>main{min-width:0}.immersive-kicker{margin-bottom:20px;color:#8d7358;font-size:10px;letter-spacing:1.5px}.immersive-report>main>.markdown-body{width:min(100%,76ch);font-family:var(--study-font-reading);font-size:17px;line-height:1.95}.immersive-report>aside{position:sticky;top:0;align-self:start;padding-left:22px;border-left:1px solid var(--study-card-border)}.immersive-report>aside h2{margin-bottom:12px;font-size:16px}.immersive-counts{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:14px}.immersive-counts span{display:flex;justify-content:space-between;padding:7px;background:var(--study-surface-muted);font-size:11px}.immersive-report>aside article{display:flex;flex-direction:column;padding:10px 0;border-top:1px solid var(--study-card-border)}.immersive-report>aside article b{font-family:var(--study-font-reading);font-size:12px;line-height:1.55}.immersive-report>aside article small{margin-top:5px;color:var(--study-text-secondary)}.note-reader-actions{display:flex;justify-content:flex-end;gap:8px}@media(max-width:840px){.immersive-report{grid-template-columns:1fr}.immersive-report>aside{position:static;padding-left:0;border-top:1px solid var(--study-card-border);border-left:0}.immersive-report>main>.markdown-body{font-size:16px}}
.task-monitor{display:grid;gap:9px;padding:12px;border:1px solid #dfcfb9;border-radius:9px;background:#fffaf1}.task-monitor header{display:flex;align-items:center;justify-content:space-between}.task-monitor header div{display:flex;min-width:0;flex-direction:column}.task-monitor header small{overflow:hidden;max-width:240px;color:#8c7e6d;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.task-monitor p,.task-monitor>small{margin:0;color:#75695c;font-size:11px}.task-stages{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}.task-stages span{padding:5px 3px;border-radius:5px;background:#eee7dc;color:#9a8f81;font-size:9px;text-align:center}.task-stages span.active{background:#ead8bd;color:#815627;font-weight:700}.task-stages span.done{background:#e5efe7;color:#39704e}@media(max-width:680px){.task-stages{grid-template-columns:1fr 1fr}.task-stages span:last-child{grid-column:1/-1}}
</style>
