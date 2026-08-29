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
          <div><b>研究报告工作区</b><small>由 DeepSeek 自主规划研究路径，来源约束负责防止脱离原文</small></div>
          <el-button v-if="content" @click="copyReport">复制 Markdown</el-button>
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
          <label>研究问题
            <el-input v-model="focus" type="textarea" :rows="3" maxlength="500" placeholder="例如：不同文献如何解释基层协同治理，其证据边界有何差异？" />
          </label>
          <label>用户补充维度（可选）
            <el-input v-model="framework" type="textarea" :rows="2" maxlength="400" placeholder="留空时由 AI 根据材料自主选择；也可补充必须比较的概念、案例或方法" />
          </label>
          <el-button type="primary" size="large" :loading="loading" @click="generate">{{ loading ? 'DeepSeek 正在研读…' : '生成研究报告' }}</el-button>
          <div v-if="loading" class="progress"><el-progress :percentage="progress" :indeterminate="progress===0" /><span>{{ stage }}</span></div>
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
          <el-button @click="saveAsNote">保存为知识笔记</el-button>
          <el-button @click="saveClaimsAsEvidence">保存主张为证据卡片</el-button>
          <el-button type="primary" plain @click="sendToPptx">发送到 PPTX 工作台</el-button>
        </footer>
      </main>

      <aside class="audit-pane panel">
        <header><b>来源与主张审计</b><small>{{ activeReport?.claims?.length || 0 }} 条主张</small></header>
        <section class="source-summary"><h3>本次来源</h3><button v-for="book in scopedBooks" :key="book.id" @click="router.push(`/reader/${book.id}`)">《{{ book.title }}》 ↗</button></section>
        <section>
          <h3>可核验主张</h3>
          <article v-for="(claim,index) in activeReport?.claims || []" :key="index" class="claim-card">
            <div class="claim-meta"><span :class="claim.status">{{ statusLabel(claim.status) }}</span><span>{{ typeLabel(claim.claim_type) }}</span><small>{{ confidenceLabel(claim.confidence) }}</small></div>
            <p>{{ claim.claim }}</p>
            <div class="source-links"><button v-for="ref in claim.source_refs || []" :key="ref" @click="openSourceRef(ref)">{{ ref }} ↗</button><small v-if="!(claim.source_refs || []).length">无有效来源锚点</small></div>
            <em>{{ claim.reason }}</em><em v-if="claim.counterpoint" class="counterpoint">限制：{{ claim.counterpoint }}</em>
          </article>
          <el-empty v-if="!(activeReport?.claims || []).length" description="生成后显示逐条主张与来源" :image-size="54" />
        </section>
        <section v-if="activeReport?.open_questions?.length" class="open-questions"><h3>待继续核查</h3><ul><li v-for="item in activeReport.open_questions" :key="item">{{ item }}</li></ul></section>
        <section class="history"><h3>历史报告</h3><button v-for="report in scopedReports" :key="report.id" :class="{active:activeReport?.id===report.id}" @click="openReport(report)">{{ report.created_at?.slice(0,16).replace('T',' ') }}<small>{{ report.focus || '未命名研究问题' }}</small></button></section>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
import { knowledgeBooks, knowledgeBookIds, loadKnowledgeBooks } from '../stores/knowledgeScope'
import { getBook, subscribeTask, studyOverview, studyReports, getStudyReport, listKnowledgeRecords, createKnowledgeNote, createEvidenceCard } from '../api'
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
const materialBooks = ref([]), notes = ref([]), selectedChapterIds = ref([]), selectedNoteIds = ref([])
const sourceQuery = ref(''), focus = ref(''), framework = ref(''), content = ref('')
const researchMode = ref('adaptive'), reasoningDepth = ref('deep')
const loading = ref(false), progress = ref(0), stage = ref(''), reports = ref([]), activeReport = ref(null)
const activeMode = computed(() => researchModes.find(item => item.value === researchMode.value) || researchModes[0])
const activePlan = computed(() => activeReport.value?.research_plan || {})
const hasPlan = computed(() => ['subquestions', 'analysis_axes', 'evidence_needs'].some(key => activePlan.value[key]?.length))
const scopedBooks = computed(() => knowledgeBooks.value.filter(book => knowledgeBookIds.value.includes(book.id)))
const sameScope = (ids = []) => { const a = [...ids].sort((x,y) => x-y), b = [...knowledgeBookIds.value].sort((x,y) => x-y); return a.length === b.length && a.every((id,i) => id === b[i]) }
const scopedReports = computed(() => reports.value.filter(report => sameScope(report.book_ids)))
const filteredNotes = computed(() => notes.value.filter(note => !sourceQuery.value || `${note.title}${note.book_title}`.toLowerCase().includes(sourceQuery.value.toLowerCase())))
const filteredChapters = items => items.filter(item => !sourceQuery.value || item.label.toLowerCase().includes(sourceQuery.value.toLowerCase()))
const flatten = (nodes, depth = 0, out = []) => { for (const node of nodes) { out.push({ id: node.id, label: '　'.repeat(depth) + node.title }); flatten(node.children || [], depth + 1, out) } return out }
const loadMaterials = async () => { const books = []; for (const book of scopedBooks.value) { try { const detail = await getBook(book.id); books.push({ ...book, chapters: flatten(detail.chapters || []) }) } catch { books.push({ ...book, chapters: [] }) } } materialBooks.value = books; try { const result = await listKnowledgeRecords({ book_ids: knowledgeBookIds.value, record_types: ['note'] }); notes.value = result.items || [] } catch { notes.value = [] } }
const loadReports = async () => { try { const result=await studyReports(1,50); reports.value = result.items || [] } catch (error) { ElMessage.error(error.message) } }
const openReport = async report => { try { const full=report.content!==undefined?report:await getStudyReport(report.id); activeReport.value = full; content.value = full.content; focus.value = full.focus || focus.value; framework.value = full.framework || framework.value; researchMode.value = full.research_mode || full.selection?.research_mode || 'adaptive'; reasoningDepth.value = full.reasoning_depth || full.selection?.reasoning_depth || 'standard'; selectedChapterIds.value = full.selection?.chapter_ids || []; selectedNoteIds.value = full.selection?.note_ids || [] } catch(error){ ElMessage.error(`无法打开研究报告：${error.message}`) } }
const generate = async () => { if (!focus.value.trim()) return ElMessage.warning('请先写明研究问题'); loading.value = true; progress.value = 0; stage.value = '正在提交到全局任务中心'; try { const response = await studyOverview({ book_ids: knowledgeBookIds.value, chapter_ids: selectedChapterIds.value, note_ids: selectedNoteIds.value, focus: focus.value.trim(), framework: framework.value.trim(), research_mode: researchMode.value, reasoning_depth: reasoningDepth.value }); notifyTaskSubmitted(); const task=await subscribeTask(response.task_id,next=>{progress.value=Math.round((next.progress||0)*100);stage.value=next.message||next.stage||'处理中'}); if(task.status==='failed'||task.status==='cancelled')throw new Error(task.error||task.message||'任务未完成'); await loadReports(); const latest=reports.value.find(report=>report.id===task.result?.report_id)||scopedReports.value[0];if(latest)await openReport(latest);ElMessage.success('研究报告、研究路径与主张审计已完成') } catch (error) { ElMessage.error(`生成失败：${error.message}`) } finally { loading.value = false } }
const copyReport = async () => { try { await navigator.clipboard.writeText(content.value); ElMessage.success('Markdown 已复制') } catch { ElMessage.warning('请手动复制') } }
const reportSourceScope = () => ({ book_ids: [...knowledgeBookIds.value], chapter_ids: [...selectedChapterIds.value], note_ids: [...selectedNoteIds.value], research_question: focus.value })
const reportSourceRefs = () => [...new Set((activeReport.value?.claims || []).flatMap(claim => claim.source_refs || []))]
const saveAsNote = async () => { const bookId = knowledgeBookIds.value[0]; try { await createKnowledgeNote({ book_id: bookId, source_book_ids: knowledgeBookIds.value, source_report_id: activeReport.value?.id || null, source_scope: reportSourceScope(), source_refs: reportSourceRefs(), title: (focus.value || '研究报告').slice(0,80), content: content.value, tags: ['研究报告'], origin: 'ai' }); ElMessage.success('已保存为带完整来源回链的知识笔记') } catch (error) { ElMessage.error(error.message) } }
const saveClaimsAsEvidence = async () => { const claims = activeReport.value?.claims || []; if (!claims.length) return ElMessage.warning('当前报告没有可保存的结构化主张'); let count = 0; for (const claim of claims) { const match = String(claim.source_refs?.[0] || '').match(/^B(\d+):/), bookId = match ? Number(match[1]) : knowledgeBookIds.value[0]; try { await createEvidenceCard({ book_id: bookId, source_book_ids: knowledgeBookIds.value, source_report_id: activeReport.value?.id || null, source_scope: reportSourceScope(), source_refs: claim.source_refs || [], title: claim.claim.slice(0,80), evidence_text: [claim.reason, claim.counterpoint].filter(Boolean).join('\n') || '待核验证据', claim_text: claim.claim, tags: ['研究报告'], origin: 'ai', verification_status: claim.status || 'needs_review' }); count++ } catch { /* 单条失败不阻断其余保存 */ } } ElMessage.success(`已保存 ${count} 条带来源回链的证据卡片`) }
const sendToPptx = () => { sessionStorage.setItem('deckSelectedText', content.value); sessionStorage.setItem('deckSourceBookIds', JSON.stringify(knowledgeBookIds.value)); sessionStorage.setItem('deckSourceReportId', String(activeReport.value?.id || '')); sessionStorage.setItem('deckSourceChapterIds', JSON.stringify(selectedChapterIds.value)); router.push({ path: '/literature-workbench', query: { from: 'research-report', bookId: knowledgeBookIds.value[0] } }) }
const openSourceRef = refText => { const match = String(refText).match(/^B(\d+)(?::CH\d+)?(?::P(\d+)(?:-\d+)?)?/); if (!match) return ElMessage.warning('来源锚点无法定位'); router.push({ path: `/reader/${match[1]}`, query: match[2] ? { page: match[2] } : {} }) }
const statusLabel = status => ({ supported: '支持', partial: '部分支持', needs_review: '待核验', unsupported: '不支持' }[status] || '待核验')
const typeLabel = type => ({ descriptive: '事实描述', associational: '关联判断', causal: '因果判断', interpretive: '解释判断' }[type] || '解释判断')
const confidenceLabel = confidence => ({ high: '高置信', medium: '中置信', low: '低置信' }[confidence] || '低置信')
const resetScope = async () => { activeReport.value = null; content.value = ''; selectedChapterIds.value = []; selectedNoteIds.value = []; await loadMaterials() }
watch(knowledgeBookIds, resetScope, { deep: true })
onMounted(async () => { await loadKnowledgeBooks(); await Promise.all([loadMaterials(), loadReports()]) })
</script>

<style scoped>
.report-workspace{max-width:1560px}.scope-required{display:flex;flex-direction:column;gap:6px;padding:40px;border:1px dashed #d8c7b0;border-radius:12px;background:#f7f2e9;text-align:center}.workspace-grid{display:grid;grid-template-columns:280px minmax(520px,1fr) 320px;gap:10px;align-items:start}.panel{overflow:hidden;border:1px solid #e5e7eb;border-radius:11px;background:#fffdf9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.panel>header{display:flex;align-items:center;justify-content:space-between;padding:13px 14px;border-bottom:1px solid #ece4d8}.panel>header>div,.source-pane header,.audit-pane header{display:flex;flex-direction:column}.panel header small{color:var(--el-text-color-secondary);font-size:11px}.source-pane>.el-input{margin:10px;width:calc(100% - 20px)}.source-pane section,.audit-pane>section{padding:0 12px 12px;border-top:1px solid #eee7dc}.panel h3{margin:12px 0 8px;color:#786b5b;font-size:12px}.source-book>b{display:block;margin:9px 0 4px;font-size:13px}.source-pane :deep(.el-checkbox-group){display:grid;gap:2px}.source-pane :deep(.el-checkbox){height:auto;margin:0;padding:4px 0;white-space:normal}.source-pane :deep(.el-checkbox__label){display:flex;min-width:0;flex-direction:column;font-size:12px}.source-pane :deep(.el-checkbox__label small){color:#988b7b}.report-pane{min-height:700px}.research-brief{display:grid;gap:10px;padding:14px;background:#f8f3ea}.method-row{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:12px}.research-brief label{display:grid;gap:5px;color:#675d50;font-size:12px;font-weight:700}.method-hint{margin:0;padding:8px 10px;border-left:2px solid #b98a58;background:rgba(255,255,255,.55);color:#75695c;font-size:11px;line-height:1.6}.progress span{font-size:11px;color:#7b7165}.research-plan{margin:14px;border:1px solid #e7ddcf;border-radius:9px;background:#fffcf7}.research-plan>header{display:flex;align-items:center;justify-content:space-between;padding:11px 12px;border-bottom:1px solid #eee5d9}.research-plan>header div{display:flex;flex-direction:column}.research-plan>header small{color:#8b7f71;font-size:10px}.plan-columns{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:10px;padding:0 12px 10px}.plan-columns ol,.plan-columns ul,.open-questions ul{margin:0;padding-left:18px;color:#5e554b;font-size:11px;line-height:1.65}.report-content{max-height:calc(100vh - 430px);min-height:300px;overflow-y:auto;padding:18px 24px;line-height:1.9}.output-actions{display:flex;justify-content:flex-end;gap:7px;padding:12px;border-top:1px solid #ece4d8}.source-summary{display:grid;gap:5px}.source-summary button,.history button{padding:7px;border:0;border-radius:7px;background:#f7f1e7;color:#82582e;cursor:pointer;text-align:left;transition:transform .15s ease,background-color .15s ease}.source-summary button:hover,.history button:hover,.source-links button:hover{transform:translateY(-1px);background:#f0e5d5}.claim-card{margin-bottom:8px;padding:9px;border:1px solid #ece3d7;border-radius:8px;box-shadow:0 1px 2px rgba(15,23,42,.04)}.claim-meta{display:flex;align-items:center;gap:7px}.claim-meta span,.claim-meta small{font-size:10px}.claim-meta span:first-child{font-weight:700}.claim-meta span.supported{color:#327052}.claim-meta span.partial{color:#8b6a20}.claim-meta span.needs_review{color:#b5661f}.claim-meta span.unsupported{color:#ad4242}.claim-meta small{margin-left:auto;color:#948779}.claim-card p{margin:7px 0;font-size:12px;line-height:1.55}.claim-card em{display:block;color:#817569;font-size:11px;font-style:normal;line-height:1.5}.claim-card .counterpoint{margin-top:4px;color:#8b5f46}.source-links{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}.source-links button{padding:2px 5px;border:0;border-radius:4px;background:#f5eee3;color:#8a5d31;cursor:pointer;font-size:9px;transition:transform .15s ease,background-color .15s ease}.source-links small{color:#a39688;font-size:10px}.history{display:grid;gap:5px}.history button{display:flex;flex-direction:column}.history button.active{outline:1px solid #b98a58}.history small{overflow:hidden;color:#82776a;text-overflow:ellipsis;white-space:nowrap}.open-questions li+li{margin-top:4px}@media(max-width:1240px){.workspace-grid{grid-template-columns:250px minmax(480px,1fr)}.audit-pane{grid-column:1/-1}}@media(max-width:780px){.workspace-grid{grid-template-columns:1fr}.audit-pane{grid-column:auto}.method-row,.plan-columns{grid-template-columns:1fr}.output-actions{flex-wrap:wrap;justify-content:flex-start}}
</style>
