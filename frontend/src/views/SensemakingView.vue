<template>
  <div class="sense-page" :class="{embedded}">
    <header v-if="!embedded" class="sense-intro">
      <p class="eyebrow">READ · RECONSTRUCT · DISCOVER</p>
      <h1>理解与发现</h1>
      <p>先重建作者的论证，再检查两篇材料是否真的在讨论同一件事。所有 AI 判断都可以回原页核对。</p>
    </header>

    <el-tabs v-model="mode" class="sense-tabs">
      <el-tab-pane label="读懂一篇" name="reading">
        <section class="setup-card">
          <h2>重建一篇文献的论证</h2>
          <p>逐段研读全部已解析正文，再分层重建问题、概念、方法、结果与结论之间的关系。</p>
          <el-select v-model="bookId" filterable remote :remote-method="searchBookOptions" :loading="booksLoading"
            placeholder="搜索并选择已解析文献" class="book-select">
            <el-option v-for="book in books" :key="book.id" :label="book.title" :value="book.id" />
          </el-select>
          <el-input v-model="focus" maxlength="300" show-word-limit placeholder="关注的问题（可留空，默认重建核心论证）" />
          <p v-if="readingEstimate" class="estimate-line">预计处理 {{ readingEstimate.text_chars.toLocaleString() }} 字、{{ readingEstimate.nonempty_chunks }} 个非空文本块，分 {{ readingEstimate.window_count }} 个阅读窗口；约 {{ readingEstimate.estimated_calls }} 次模型调用、{{ readingEstimate.estimated_tokens.toLocaleString() }} 估算 Token。</p>
          <el-alert v-if="readingEstimate?.pages_without_extracted_text" type="warning" :closable="false" :title="`原文件约有 ${readingEstimate.pages_without_extracted_text} 页未抽取到文字；全文研读只能覆盖已解析正文`" />
          <el-alert v-if="readingEstimate && !readingEstimate.within_budget" type="warning" :closable="false" title="预计超出当前 AI 任务预算，请在设置中调整后再启动全文研读" />
          <div class="setup-footer">
            <span>启动后将逐段发送全部已解析正文至当前研究模型；已完成窗口可在失败后续读。文本覆盖和理解判断会分开显示。</span>
            <el-button type="primary" :loading="submitting || estimateLoading" :disabled="!bookId || !!taskId || (readingEstimate && !readingEstimate.within_budget)" @click="generateReading">研读全文并生成论证地图</el-button>
          </div>
        </section>
      </el-tab-pane>
      <el-tab-pane label="比较两篇" name="discovery">
        <section class="setup-card">
          <h2>从概念分歧发现新问题</h2>
          <p>先核对定义、测量与研究对象是否可比；有真正的张力时，再提出竞争解释与区分性问题。</p>
          <div class="pair">
            <el-select v-model="pairIds[0]" filterable remote :remote-method="searchBookOptions" :loading="booksLoading" placeholder="第一篇文献">
              <el-option v-for="book in books" :key="book.id" :label="book.title" :value="book.id" />
            </el-select>
            <el-select v-model="pairIds[1]" filterable remote :remote-method="searchBookOptions" :loading="booksLoading" placeholder="第二篇文献">
              <el-option v-for="book in books" :key="book.id" :label="book.title" :value="book.id" />
            </el-select>
          </div>
          <el-input v-model="concept" maxlength="160" show-word-limit placeholder="输入两篇材料中要比较的概念或议题" />
          <div class="setup-footer">
            <span>会在这两篇文献中检索该概念，并将命中的正文片段发送至当前研究模型。</span>
            <el-button type="primary" :loading="submitting" :disabled="!canCompare || !!taskId" @click="generateDiscovery">对齐概念并发现张力</el-button>
          </div>
        </section>
      </el-tab-pane>
    </el-tabs>

    <section v-if="taskId" class="task-card" aria-live="polite">
      <div><b>{{ taskMessage || '任务已提交' }}</b><small>离开页面后任务仍在后台运行，返回后可继续查看。</small></div>
      <el-progress :percentage="taskProgress" :stroke-width="7" />
      <el-button size="small" plain @click="stopTask">取消任务</el-button>
    </section>

    <div class="sense-workspace">
      <aside class="history-panel">
        <h2>已保存的理解资产</h2>
        <button v-for="item in history" :key="item.id" class="history-item" :class="{ active: artifact?.id === item.id }" @click="openArtifact(item.id)">
          <span>{{ item.kind === 'reading' ? '论证地图' : '发现卡' }}</span>
          <b>{{ item.focus || (item.kind === 'reading' ? '核心论证' : '概念对齐') }}</b>
          <small>{{ item.created_at?.slice(0, 16).replace('T', ' ') }}</small>
        </button>
        <el-empty v-if="!history.length" description="生成后可从这里继续阅读" :image-size="65" />
      </aside>

      <main class="result-panel" v-loading="opening">
        <el-empty v-if="!artifact" description="选择材料并开始重建论证，或打开历史结果" :image-size="95" />
        <template v-else>
          <div class="result-head">
            <div><p class="eyebrow">{{ artifact.kind === 'reading' ? 'ARGUMENT MAP' : 'CONCEPT ALIGNMENT' }}</p>
              <h2>{{ artifact.focus || (artifact.kind === 'reading' ? '核心论证' : '跨文献发现') }}</h2>
              <small>模型 {{ artifact.model_name || '未知' }} · {{ artifact.prompt_version }} · AI 初判，需核对原文</small>
            </div>
            <el-tag type="warning" effect="plain">{{ artifact.payload.semantic_status === 'ai_unchecked' ? '语义支持待核对' : '待复核' }}</el-tag>
          </div>
          <el-alert v-if="artifact.stale_chunk_ids?.length" type="warning" :closable="false" title="来源文本已变化，请重新生成后再使用这些判断" class="stale-alert" />

          <template v-if="artifact.kind === 'reading'">
            <div class="coverage-line">
              <el-tag size="small" effect="plain">{{ materialLabel(artifact.payload.material_type) }}</el-tag>
              <span v-if="artifact.payload.coverage?.mode === 'all_extracted_text'">已处理提取正文 {{ artifact.payload.coverage?.text_chars?.toLocaleString() }} 字、{{ artifact.payload.coverage?.processed_chunks }}/{{ artifact.payload.coverage?.nonempty_chunks }} 个非空块、{{ artifact.payload.coverage?.processed_windows }} 个窗口。文本覆盖完成不代表论证已核实或用户已理解。</span>
              <span v-else>旧版抽样阅读 {{ artifact.payload.coverage?.selected_chunks }}/{{ artifact.payload.coverage?.total_chunks }} 块；抽样覆盖不代表已理解全文。</span>
            </div>
            <el-alert v-if="artifact.payload.coverage?.missing_page_anchors" type="warning" :closable="false" :title="`${artifact.payload.coverage.missing_page_anchors} 个文本块缺少原页定位；相关判断需人工核对`" />
            <el-alert v-if="artifact.payload.coverage?.pages_without_extracted_text" type="warning" :closable="false" :title="`${artifact.payload.coverage.pages_without_extracted_text} 页未抽取到文字，请查看原页或重新 OCR`" />
            <details v-if="artifact.payload.reading_passes?.length" class="pass-trail"><summary>查看逐段阅读轨迹（{{ artifact.payload.reading_passes.length }} 段）</summary>
              <article v-for="section in artifact.payload.reading_passes" :key="section.id">
                <h3>{{ section.chapter_title }} · {{ section.id }} <small>第 {{ section.page_start || '?' }}–{{ section.page_end || '?' }} 页 · {{ passRoleLabel(section.role) }}</small></h3>
                <p>AI 段落作用概述（待核对）：{{ section.summary || '该段未提取到可定位的论点' }}</p>
                <ul><li v-for="(claim, index) in section.claims" :key="index"><small>{{ epistemicLabel(claim.epistemic_status) }} · AI 初判</small> {{ claim.statement }}
                  <button v-for="ev in claim.evidence" :key="ev.ref" @click="openEvidence(ev)">{{ ev.ref }} ↗</button>
                </li></ul>
              </article>
            </details>
            <div class="argument-chain">
              <article v-for="node in artifact.payload.nodes" :key="node.id" class="argument-node">
                <div class="node-index">{{ node.id }}</div>
                <div class="node-content">
                  <div class="node-top"><el-tag size="small" effect="plain">{{ kindLabel(node.kind) }}</el-tag><span>{{ epistemicLabel(node.epistemic_status) }} · AI 初判 · {{ nodeReviewLabel(node.review_status) }}</span></div>
                  <h3>{{ node.statement }}</h3><p v-if="node.reasoning">{{ node.reasoning }}</p>
                  <div class="evidence-row"><button v-for="ev in node.evidence" :key="ev.ref" @click="openEvidence(ev)">
                    <b>{{ ev.ref }} ↗</b><span>“{{ ev.quote }}”</span>
                  </button></div>
                  <div class="node-review"><el-select v-model="node.review_status" size="small" @change="status => saveNodeReview(node, status)">
                      <el-option label="待复读" value="unreviewed" /><el-option label="我同意" value="agree" />
                      <el-option label="仍不清楚" value="unclear" /><el-option label="我不同意" value="disagree" />
                    </el-select><el-input v-model="node.review_note" size="small" maxlength="1000" placeholder="记下你的解释或疑问" @change="saveNodeReview(node, node.review_status)" /></div>
                  <el-input v-model="node.remaining_doubt" size="small" maxlength="1000" placeholder="仍存疑处（可选）" @change="saveNodeReview(node, node.review_status)" />
                  <el-select v-model="node.trigger_ref" clearable size="small" placeholder="选取促使你修订的原文页（可选）" class="trigger-select">
                    <el-option v-for="ev in node.evidence" :key="ev.ref" :label="ev.ref" :value="ev.ref" />
                  </el-select>
                  <el-button size="small" text @click="chooseNode(node)">用自己的话解释这一步</el-button>
                </div>
              </article>
            </div>
            <section v-if="artifact.payload.edges?.length" class="relation-list"><h3>论证关系</h3>
              <div v-for="(edge, index) in artifact.payload.edges" :key="index" class="relation-item">
                <b>{{ edge.from }} → {{ edge.to }} · {{ relationLabel(edge.relation) }}</b><p>{{ edge.reason }}</p>
                <button v-for="ev in edge.evidence" :key="ev.ref" @click="openEvidence(ev)">{{ ev.ref }} ↗ “{{ ev.quote }}”</button>
              </div>
            </section>
            <section class="teachback">
              <h3>我来解释</h3>
              <p>{{ selectedNode ? `请解释 ${selectedNode.id}：${selectedNode.statement}` : artifact.payload.teach_back_question }}</p>
              <el-select v-model="selectedNodeId" placeholder="选择要解释的论证节点" @change="clearFeedback">
                <el-option v-for="node in artifact.payload.nodes" :key="node.id" :label="`${node.id} · ${node.statement.slice(0, 45)}`" :value="node.id" />
              </el-select>
              <div class="coach-actions"><el-button size="small" :loading="coachLoading" :disabled="!selectedNodeId || !!artifact.stale_chunk_ids?.length" @click="requestCoach('explain')">解释给我听</el-button>
                <el-button size="small" :loading="coachLoading" :disabled="!selectedNodeId || !!artifact.stale_chunk_ids?.length" @click="requestCoach('counterexample')">给我一个反例</el-button></div>
              <div v-if="coachResult" class="feedback-box"><b>{{ coachResult.mode === 'explain' ? 'AI 解释' : 'AI 提出的反例路径' }}</b><p>{{ coachResult.answer }}</p>
                <p>{{ coachResult.next_question }}</p><small>依据：{{ coachResult.source_refs.join('、') || '请回原页核对' }}</small></div>
              <el-input v-model="explanation" type="textarea" :rows="4" maxlength="3000" show-word-limit placeholder="先写下你自己的解释：这一步为什么能支持结论？适用范围是什么？" />
              <el-button type="primary" :loading="feedbackLoading" :disabled="!selectedNodeId || explanation.trim().length < 20 || !!artifact.stale_chunk_ids?.length" @click="submitExplanation">对照原文检查理解</el-button>
              <div v-if="feedback" class="feedback-box"><b>AI 反馈，仍可回原页核查</b>
                <p v-for="(item, index) in feedback.matches" :key="`m${index}`">✓ {{ item }}</p>
                <p v-for="(item, index) in feedback.missing" :key="`o${index}`">遗漏：{{ item }}</p>
                <p v-for="(item, index) in feedback.overreach" :key="`e${index}`">超出材料：{{ item }}</p>
                <strong>{{ feedback.next_question }}</strong>
              </div>
              <details v-if="attempts.length"><summary>以往解释与修订（{{ attempts.length }}）</summary>
                <article v-for="item in attempts" :key="item.id"><b>{{ item.node_id }}</b><p>{{ item.response }}</p><small>{{ item.feedback.next_question }}</small></article>
              </details>
            </section>
            <section v-if="artifact.payload.coverage?.mode === 'all_extracted_text'" class="interpretation-panel">
              <h3>基于全文阅读记录继续生成</h3>
              <p>从全篇论证和逐段论点中寻找相关依据。每段生成内容标明原文观察、作者解释或 AI 推断；材料未回答的部分会单列。</p>
              <el-input v-model="interpretQuestion" type="textarea" :rows="2" maxlength="500" show-word-limit placeholder="例如：作者为何认为这种机制能解释结论？哪些案例可能推翻它？" />
              <div class="interpret-actions"><el-select v-model="interpretMode" size="small"><el-option label="解释论证" value="explain" /><el-option label="审查薄弱环节" value="critical" /><el-option label="教我理解" value="teach" /></el-select>
                <el-button type="primary" :loading="interpretLoading" :disabled="interpretQuestion.trim().length < 6 || !!artifact.stale_chunk_ids?.length" @click="requestInterpretation">依据全文生成</el-button></div>
              <article v-for="entry in interpretations" :key="entry.id" class="interpret-result"><h4>{{ entry.answer.question }}</h4>
                <div v-for="(paragraph, index) in entry.answer.paragraphs" :key="index"><small>{{ epistemicLabel(paragraph.status) }} · AI 初判</small><p>{{ paragraph.text }}</p>
                  <div class="evidence-row"><button v-for="ev in paragraph.evidence" :key="ev.ref" @click="openEvidence(ev)">{{ ev.ref }} ↗ “{{ ev.quote }}”</button></div></div>
                <p v-for="(missing, index) in entry.answer.unanswered" :key="`u${index}`" class="unanswered">材料尚不能回答：{{ missing }}</p>
              </article>
            </section>
          </template>

          <template v-else>
            <section class="alignment-card">
              <div class="alignment-title"><h3>先检查能否比较</h3><el-tag :type="comparisonTag(artifact.payload.alignment.status)">{{ comparisonLabel(artifact.payload.alignment.status) }}</el-tag></div>
              <p>{{ artifact.payload.alignment.reason }}</p>
              <div class="alignment-scroll"><table class="alignment-table"><thead><tr><th>比较维度</th>
                <th v-for="def in artifact.payload.alignment.definitions" :key="def.book_id">{{ bookTitle(def.book_id) }}</th>
              </tr></thead><tbody>
                <tr v-for="field in alignmentFields" :key="field.key"><th>{{ field.label }}</th>
                  <td v-for="def in artifact.payload.alignment.definitions" :key="def.book_id">{{ def[field.key] || '未说明' }}</td></tr>
                <tr><th>相关原页</th><td v-for="def in artifact.payload.alignment.definitions" :key="def.book_id">
                  <div class="evidence-row"><button v-for="ev in def.evidence" :key="ev.ref" @click="openEvidence(ev)">{{ ev.ref }} ↗ “{{ ev.quote }}”</button></div>
                </td></tr>
              </tbody></table></div>
            </section>
            <section class="discoveries"><h3>值得继续查证的问题</h3>
              <el-empty v-if="!artifact.payload.discoveries?.length" :description="artifact.payload.no_tension_reason || '当前材料没有形成可定位的发现卡；可能是概念不可比或证据不足'" :image-size="65" />
              <article v-for="card in artifact.payload.discoveries" :key="card.id" class="discovery-card">
                <div class="card-head"><span>研究想法 · {{ tensionLabel(card.tension_type) }} · {{ originLabel(card.candidate_origin) }}</span><el-tag size="small" effect="plain">{{ reviewLabel(card.review_status) }}</el-tag></div>
                <h4>{{ card.title }}</h4><p><b>原页现象：</b>{{ card.observation || card.tension }}</p><p><b>为何形成张力：</b>{{ card.why_tension || card.tension }}</p>
                <div class="rivals"><b>竞争解释与可观察差异</b><ol><li v-for="(item, index) in card.rival_explanations" :key="index">
                  <strong>{{ item.statement }}</strong><p>前提：{{ item.assumption }}</p><p>若成立，应看到：{{ item.prediction }}</p>
                </li></ol></div>
                <div class="question-box"><b>什么证据能改变判断？</b><p>{{ card.discriminating_question }}</p>
                  <small>下一步：{{ card.next_step }} · {{ feasibilityLabel(card.feasibility) }}</small></div>
                <p><b>所选材料已回答：</b>{{ card.already_answered }}</p><p><b>尚缺证据：</b>{{ card.still_missing }}</p>
                <div class="evidence-row"><button v-for="ev in card.evidence" :key="ev.ref" @click="openEvidence(ev)">{{ ev.ref }} ↗ “{{ ev.quote }}”</button></div>
                <small>竞争解释是 AI 提议；以下判断由你确认。</small>
                <div class="review-row"><el-select v-model="card.review_status" size="small" @change="status => saveCardReview(card, status)">
                    <el-option label="待复核" value="unreviewed" /><el-option label="有价值" value="valuable" />
                    <el-option label="伪冲突" value="false_conflict" /><el-option label="不清楚" value="unclear" />
                  </el-select><el-input v-model="card.review_note" placeholder="记录你的判断或下一步" maxlength="1000" @change="saveCardReview(card, card.review_status)" /></div>
                <el-input v-model="card.remaining_doubt" size="small" maxlength="1000" placeholder="仍存疑处（可选）" @change="saveCardReview(card, card.review_status)" />
                <el-select v-model="card.trigger_ref" clearable size="small" placeholder="选取促使你修订的原文页（可选）" class="trigger-select">
                  <el-option v-for="ev in card.evidence" :key="ev.ref" :label="ev.ref" :value="ev.ref" />
                </el-select>
                <el-button v-if="card.review_status === 'valuable'" size="small" plain @click="continueResearch(card)">带着这个问题继续研究</el-button>
              </article>
            </section>
          </template>
          <details v-if="revisions.length" class="revision-history"><summary>理解修订记录（{{ revisions.length }}）</summary>
            <article v-for="entry in revisions" :key="entry.id"><b>{{ entry.item_kind === 'node' ? '论证节点' : '发现卡' }} {{ entry.item_id }}</b>
              <p>原判断：{{ entry.before.status }} · {{ entry.before.note || '未说明' }}</p>
              <p>修订后：{{ entry.after.status }} · {{ entry.after.note || '未说明' }}</p>
              <p v-if="entry.after.remaining_doubt">仍存疑处：{{ entry.after.remaining_doubt }}</p>
              <button v-if="entry.trigger_ref" @click="openRevisionEvidence(entry)">触发来源 {{ entry.trigger_ref }} ↗</button>
              <small>{{ entry.created_at?.slice(0, 16).replace('T', ' ') }}</small></article>
          </details>
        </template>
      </main>
    </div>
  </div>
</template>

<script setup>
defineProps({ embedded: { type: Boolean, default: false } })
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  cancelTask, coachArgumentNode, estimateFullReading, generateFullTextInterpretation, getSensemakingArtifact, listBooks,
  listFullTextInterpretations, listSensemakingArtifacts, listSensemakingRevisions, listUnderstandingAttempts,
  reviewArgumentNode, reviewDiscoveryCard, startArgumentMap, startConceptDiscovery, submitUnderstandingAttempt, subscribeTask,
} from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'
import { searchKnowledgeBooks, setKnowledgeScope } from '../stores/knowledgeScope'

const route = useRoute(), router = useRouter()
const mode = ref('reading'), bookId = ref(Number(route.query.bookId) || null), pairIds = ref([Number(route.query.bookId) || null, null])
const focus = ref(''), concept = ref(''), books = ref([]), booksLoading = ref(false)
const readingEstimate = ref(null), estimateLoading = ref(false)
const history = ref([]), artifact = ref(null), opening = ref(false), submitting = ref(false)
const taskId = ref(''), taskMessage = ref(''), taskProgress = ref(0)
const selectedNodeId = ref(''), explanation = ref(''), feedback = ref(null), feedbackLoading = ref(false), attempts = ref([])
const interpretQuestion = ref(''), interpretMode = ref('explain'), interpretLoading = ref(false), interpretations = ref([])
const revisions = ref([]), coachResult = ref(null), coachLoading = ref(false)
const canCompare = computed(() => pairIds.value.every(Boolean) && pairIds.value[0] !== pairIds.value[1] && concept.value.trim().length >= 2)
const selectedNode = computed(() => artifact.value?.payload?.nodes?.find(item => item.id === selectedNodeId.value))
let taskAbort = null

const searchBookOptions = async (query = '') => {
  booksLoading.value = true
  try { books.value = (await listBooks({ status: 'ready', q: query || undefined, page_size: 100 })).items || [] }
  catch (error) { ElMessage.error(error.message) }
  finally { booksLoading.value = false }
}
const loadHistory = async () => { try { history.value = await listSensemakingArtifacts() } catch (error) { ElMessage.error(error.message) } }
const openArtifact = async id => {
  opening.value = true
  try {
    artifact.value = await getSensemakingArtifact(id)
    mode.value = artifact.value.kind
    selectedNodeId.value = ''; explanation.value = ''; feedback.value = null; coachResult.value = null
    const [savedAttempts, savedRevisions, savedInterpretations] = await Promise.all([
      artifact.value.kind === 'reading' ? listUnderstandingAttempts(id) : Promise.resolve([]),
      listSensemakingRevisions(id),
      artifact.value.kind === 'reading' ? listFullTextInterpretations(id) : Promise.resolve([])])
    attempts.value = savedAttempts; revisions.value = savedRevisions; interpretations.value = savedInterpretations
    if (artifact.value.kind === 'reading') {
      selectedNodeId.value = artifact.value.payload.nodes.find(node => node.remaining_doubt || ['unclear', 'disagree'].includes(node.review_status))?.id
        || savedAttempts.find(attempt => attempt.feedback?.missing?.length || attempt.feedback?.overreach?.length)?.node_id
        || artifact.value.payload.nodes[0]?.id || ''
    }
  } catch (error) { ElMessage.error(error.message) }
  finally { opening.value = false }
}
const followTask = async id => {
  taskAbort?.abort(); taskAbort = new AbortController(); taskId.value = id
  sessionStorage.setItem('sensemakingTaskId', id)
  let finished = false
  try {
    const finalTask = await subscribeTask(id, task => {
      taskMessage.value = task.message || task.stage || '正在处理'
      taskProgress.value = Math.round((task.progress || 0) * 100)
    }, { signal: taskAbort.signal })
    finished = true
    if (finalTask.status === 'done' && finalTask.result?.artifact_id) {
      await loadHistory(); await openArtifact(finalTask.result.artifact_id)
      ElMessage.success('理解结果已保存')
    } else if (finalTask.status === 'failed') ElMessage.error(finalTask.error || '任务失败')
    else if (finalTask.status === 'cancelled') ElMessage.info('任务已取消')
  } catch (error) { if (error.name !== 'AbortError') ElMessage.error(error.message) }
  finally { if (finished && taskId.value === id) { taskId.value = ''; sessionStorage.removeItem('sensemakingTaskId') } }
}
const generateReading = async () => {
  submitting.value = true
  try { const task = await startArgumentMap(bookId.value, focus.value.trim()); notifyTaskSubmitted(); followTask(task.task_id) }
  catch (error) { ElMessage.error(error.message) }
  finally { submitting.value = false }
}
const generateDiscovery = async () => {
  submitting.value = true
  try { const task = await startConceptDiscovery(pairIds.value, concept.value.trim()); notifyTaskSubmitted(); followTask(task.task_id) }
  catch (error) { ElMessage.error(error.message) }
  finally { submitting.value = false }
}
const stopTask = async () => { try { await cancelTask(taskId.value); taskMessage.value = '正在取消任务' } catch (error) { ElMessage.error(error.message) } }
const openEvidence = ev => router.push({ path: `/reader/${ev.book_id}`, query: ev.page ? { page: ev.page } : {} })
const chooseNode = node => { selectedNodeId.value = node.id; clearFeedback(); document.querySelector('.teachback')?.scrollIntoView({ behavior: 'smooth' }) }
const clearFeedback = () => { feedback.value = null; coachResult.value = null }
const requestCoach = async mode => {
  coachLoading.value = true
  try { coachResult.value = await coachArgumentNode(artifact.value.id, selectedNodeId.value, mode) }
  catch (error) { ElMessage.error(error.message) }
  finally { coachLoading.value = false }
}
const requestInterpretation = async () => {
  interpretLoading.value = true
  try {
    const result = await generateFullTextInterpretation(artifact.value.id, interpretQuestion.value.trim(), interpretMode.value)
    interpretations.value.unshift(result)
  } catch (error) { ElMessage.error(error.message) }
  finally { interpretLoading.value = false }
}
const submitExplanation = async () => {
  feedbackLoading.value = true
  try {
    const result = await submitUnderstandingAttempt(artifact.value.id, selectedNodeId.value, explanation.value.trim())
    feedback.value = result.feedback; attempts.value.unshift(result)
  } catch (error) { ElMessage.error(error.message) }
  finally { feedbackLoading.value = false }
}
const saveCardReview = async (card, status) => {
  try { artifact.value = await reviewDiscoveryCard(artifact.value.id, card.id, status, card.review_note || '', card.remaining_doubt || '', card.trigger_ref || null); revisions.value = await listSensemakingRevisions(artifact.value.id); ElMessage.success('判断已保存') }
  catch (error) { ElMessage.error(error.message) }
}
const saveNodeReview = async (node, status) => {
  try { artifact.value = await reviewArgumentNode(artifact.value.id, node.id, status, node.review_note || '', node.remaining_doubt || '', node.trigger_ref || null); revisions.value = await listSensemakingRevisions(artifact.value.id); ElMessage.success('理解记录已保存') }
  catch (error) { ElMessage.error(error.message) }
}
const continueResearch = async card => {
  setKnowledgeScope(artifact.value.book_ids)
  try { await searchKnowledgeBooks() } catch (error) { ElMessage.warning(`资料列表刷新失败：${error.message}`) }
  router.push({ path: '/knowledge-hub', query: { view: 'study', focus: card.discriminating_question,
    discoveryId: artifact.value.id, cardId: card.id } })
}
const bookTitle = id => artifact.value?.payload?.book_titles?.[String(id)] || books.value.find(book => book.id === id)?.title || `文献 ${id}`
const kindLabel = kind => ({ question: '研究问题', concept: '核心概念', assumption: '前提', method: '方法/推理', finding: '发现', conclusion: '结论', boundary: '适用边界', uncertainty: '待解释' })[kind] || kind
const materialLabel = kind => ({ quantitative: '定量研究', qualitative: '质性研究', theoretical: '理论文本', review: '综述/报告', other: '材料类型待确认' })[kind] || '材料类型待确认'
const comparisonLabel = kind => ({ comparable: '可比较', partial: '部分可比较', incomparable: '不可比较', unknown: '尚不清楚' })[kind] || '尚不清楚'
const comparisonTag = kind => kind === 'comparable' ? 'success' : kind === 'partial' ? 'warning' : 'info'
const reviewLabel = kind => ({ unreviewed: '待复核', valuable: '有价值', false_conflict: '伪冲突', unclear: '不清楚' })[kind] || '待复核'
const nodeReviewLabel = kind => ({ unreviewed: '待复读', agree: '我同意', understood: '曾标记理解', unclear: '仍不清楚', disagree: '我不同意' })[kind] || '待复读'
const passRoleLabel = kind => ({ body: '正文', frontmatter: '前置部分', references: '参考文献', appendix: '附录', other: '其他' })[kind] || '未分类'
const epistemicLabel = kind => ({ source_observation: '原文观察', author_interpretation: '作者解释', ai_inference: 'AI 推断' })[kind] || 'AI 推断'
const alignmentFields = [
  { key: 'term', label: '作者术语' }, { key: 'meaning', label: '原文定义' },
  { key: 'measurement', label: '测量/观察' }, { key: 'unit', label: '分析单位' },
  { key: 'period_place', label: '时间/地点' }, { key: 'population', label: '研究对象' },
  { key: 'method', label: '方法' }, { key: 'result_direction', label: '结论方向' },
]
const relationLabel = kind => ({ supports: '支持', depends_on: '依赖', limits: '限制', challenges: '挑战' })[kind] || kind
const tensionLabel = kind => ({ result: '事实结果', mechanism: '机制解释', scope: '适用范围', measurement: '测量口径', method: '方法假设', normative: '规范立场' })[kind] || kind
const originLabel = kind => ({ cross_mechanism: '机制分歧', concept_scope: '概念失效', claim_vs_evidence: '主张与证据', assumption: '关键假设', negative_case: '负例', concept_transfer: '概念迁移' })[kind] || '待确认来源'
const feasibilityLabel = kind => ({ selected_materials: '可先用所选材料核查', findable_source: '需寻找其他资料', new_data: '需要新数据' })[kind] || '可行性待核查'
const openRevisionEvidence = entry => {
  const items = entry.item_kind === 'node' ? artifact.value?.payload?.nodes : artifact.value?.payload?.discoveries
  const item = items?.find(value => value.id === entry.item_id)
  const ev = item?.evidence?.find(value => value.ref === entry.trigger_ref)
  if (ev) openEvidence(ev)
}

watch(() => route.query.bookId, value => { if (value) { bookId.value = Number(value); pairIds.value[0] = Number(value) } })
let estimateRequest = 0
watch(bookId, async value => {
  const request = ++estimateRequest
  readingEstimate.value = null
  if (!value) return
  estimateLoading.value = true
  try { const result = await estimateFullReading(value); if (request === estimateRequest) readingEstimate.value = result }
  catch (error) { if (request === estimateRequest) ElMessage.warning(`全文估算失败：${error.message}`) }
  finally { if (request === estimateRequest) estimateLoading.value = false }
}, { immediate: true })
onMounted(async () => {
  await Promise.all([searchBookOptions(), loadHistory()])
  const savedTask = sessionStorage.getItem('sensemakingTaskId')
  if (savedTask) followTask(savedTask)
})
onUnmounted(() => taskAbort?.abort())
</script>

<style scoped>
.sense-page{max-width:1480px;margin:0 auto;padding:20px 18px 50px;color:#253631}.sense-intro{padding:26px 30px;border-radius:18px;background:linear-gradient(125deg,#163d39,#31564a 65%,#947653);color:#f9f5ec}.eyebrow{margin:0 0 8px;color:#cbb28e;font-size:11px;font-weight:700;letter-spacing:2px}.sense-intro h1{margin:0;font-size:29px;font-weight:600}.sense-intro>p:last-child{max-width:730px;margin:10px 0 0;color:#e7e7db;font-size:14px;line-height:1.7}.sense-tabs{margin-top:18px}.setup-card,.task-card,.history-panel,.result-panel{border:1px solid #e4ded2;border-radius:14px;background:#fffdf9;box-shadow:0 3px 18px rgba(28,41,31,.04)}.setup-card{display:grid;gap:13px;padding:23px}.setup-card h2{margin:0;font-size:18px}.setup-card>p{margin:0;color:#6c786e;font-size:13px}.book-select{width:100%}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.setup-footer{display:flex;align-items:center;justify-content:space-between;gap:20px}.setup-footer span{max-width:660px;color:#7b776d;font-size:12px;line-height:1.5}.task-card{display:flex;align-items:center;gap:15px;margin:16px 0;padding:14px 18px}.task-card>div{display:flex;min-width:180px;flex-direction:column}.task-card small{color:#8b8a7e;font-size:11px}.task-card .el-progress{flex:1}.sense-workspace{display:grid;grid-template-columns:255px minmax(0,1fr);gap:16px;margin-top:20px}.history-panel{align-self:start;padding:15px}.history-panel h2{margin:0 0 12px;font-size:14px}.history-item{display:flex;width:100%;flex-direction:column;gap:4px;margin:5px 0;padding:10px;border:1px solid #ebe4d9;border-radius:8px;background:#faf7f0;color:#344239;text-align:left;cursor:pointer}.history-item.active{border-color:#5d8375;background:#edf4ee}.history-item span,.history-item small{color:#777e72;font-size:11px}.history-item b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.result-panel{min-height:420px;padding:23px}.result-head{display:flex;justify-content:space-between;gap:12px;align-items:start;border-bottom:1px solid #eee8df;padding-bottom:16px}.result-head h2{margin:0 0 5px;font-size:22px}.result-head small{color:#798176}.stale-alert{margin-top:12px}.coverage-line{display:flex;align-items:center;gap:9px;margin:15px 0;color:#68776d;font-size:12px}.argument-chain{position:relative;margin:18px 0}.argument-node{display:flex;gap:14px;margin:0 0 10px}.node-index{display:grid;flex:0 0 37px;height:37px;place-items:center;border-radius:50%;background:#dfece5;color:#315a47;font-weight:700}.node-content{flex:1;padding:14px;border:1px solid #e7e3d9;border-radius:10px}.node-top{display:flex;align-items:center;justify-content:space-between;color:#92958a;font-size:11px}.node-content h3{margin:10px 0 5px;font-size:15px}.node-content p{margin:0 0 10px;color:#5b685f;font-size:13px;line-height:1.65}.evidence-row{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.evidence-row button{display:flex;max-width:100%;gap:6px;align-items:baseline;padding:7px 9px;border:1px solid #d9e4db;border-radius:7px;background:#f3f8f3;color:#345c48;text-align:left;cursor:pointer;font-size:11px}.evidence-row button span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.node-review{display:grid;grid-template-columns:130px 1fr;gap:7px;margin:8px 0}.relation-list{display:flex;flex-wrap:wrap;gap:7px;padding:13px;border-radius:9px;background:#f6f4ec}.relation-list h3{width:100%;margin:0;font-size:13px}.relation-list span{padding:3px 7px;border-radius:5px;background:#fff;color:#657166;font-size:11px}.teachback,.alignment-card,.discoveries{margin-top:23px}.teachback{display:grid;gap:12px;padding:19px;border-radius:11px;background:#f7f3e8}.teachback h3,.alignment-card h3,.discoveries h3{margin:0;font-size:17px}.teachback>p{margin:0;color:#526358}.feedback-box{padding:13px;border-left:3px solid #658a74;background:#fff}.feedback-box p{margin:7px 0;font-size:13px}.feedback-box strong{font-size:13px}.teachback details{font-size:12px}.teachback details article{margin-top:10px;padding:10px;border-top:1px solid #e0dccc}.alignment-title,.card-head{display:flex;justify-content:space-between;align-items:center}.alignment-card>p{color:#617066;line-height:1.7}.definitions{display:grid;grid-template-columns:1fr 1fr;gap:12px}.definitions article{padding:13px;border:1px solid #e5e5db;border-radius:9px;background:#fcfcf8}.definitions p{margin:7px 0;color:#58685d;font-size:13px;line-height:1.5}.discoveries{border-top:1px solid #ece9e0;padding-top:18px}.discovery-card{margin-top:13px;padding:16px;border:1px solid #e1e5da;border-radius:10px;background:#fff}.card-head{color:#7a826f;font-size:11px}.discovery-card h4{margin:9px 0;font-size:16px}.discovery-card>p{line-height:1.65}.rivals{padding:10px 13px;border-radius:8px;background:#f4f5ee;font-size:13px}.rivals ol{margin:7px 0 0;padding-left:20px}.question-box{margin-top:9px;padding:11px 13px;border-left:3px solid #b48a52;background:#fbf6ed}.question-box p{margin:6px 0}.question-box small{color:#756d60}.review-row{display:grid;grid-template-columns:135px 1fr;gap:9px}@media(max-width:800px){.sense-workspace,.definitions,.pair{grid-template-columns:1fr}.setup-footer,.task-card{flex-wrap:wrap}.history-panel{max-height:240px;overflow:auto}.sense-page{padding:12px}.result-panel{padding:14px}}
.relation-item{width:100%;padding:10px 12px;border:1px solid #e9e5d8;border-radius:8px;background:#fff;font-size:12px}
.relation-item p{margin:6px 0;color:#58685d;line-height:1.5}
.relation-item button,.revision-history button{border:0;background:transparent;color:#34634d;text-align:left;cursor:pointer}
.relation-item button{display:block;padding:3px 0;font-size:11px}
.coach-actions{display:flex;flex-wrap:wrap;gap:8px}
.trigger-select{width:100%;max-width:360px;margin:6px 0}
.rivals li{margin-bottom:10px}.rivals li p{margin:3px 0;color:#58685d}
.revision-history{margin-top:24px;padding:14px;border-top:1px solid #e6e2d6;font-size:12px}
.revision-history article{margin:10px 0;padding:10px;border-radius:8px;background:#f7f6ef}
.revision-history p{margin:4px 0}.revision-history small{display:block;color:#7b8178}
.alignment-scroll{overflow-x:auto;margin-top:12px}
.alignment-table{width:100%;min-width:650px;border-collapse:collapse;font-size:12px;line-height:1.55}
.alignment-table th,.alignment-table td{padding:9px 11px;border:1px solid #e6e6db;text-align:left;vertical-align:top}
.alignment-table thead th{background:#eaf0e9}.alignment-table tbody th{width:120px;background:#f5f7f0}
.alignment-table td{width:40%}.alignment-table .evidence-row{margin:0}
.sense-page.embedded{box-sizing:border-box;width:100%;max-width:none;padding:0;color:var(--study-text-primary)}
.sense-page.embedded .sense-tabs{margin-top:0}
.estimate-line{padding:9px 11px;border-radius:7px;background:#edf4ee;color:#365846!important}
.pass-trail{margin:15px 0;padding:12px 15px;border:1px solid #e3e8df;border-radius:9px;background:#f9fbf7;font-size:12px}
.pass-trail summary{cursor:pointer;font-weight:700}.pass-trail article{margin:11px 0;padding:10px;border-top:1px solid #e2e8e0}
.pass-trail h3{margin:0 0 5px;font-size:13px}.pass-trail h3 small{font-weight:400;color:#728075}
.pass-trail p{margin:0 0 5px;color:#59685c}.pass-trail ul{margin:4px 0;padding-left:19px}
.pass-trail li{margin:5px 0}.pass-trail button{margin-left:5px;border:0;background:none;color:#35704d;cursor:pointer}
.interpretation-panel{display:grid;gap:12px;margin-top:23px;padding:18px;border:1px solid #e2e7dd;border-radius:10px;background:#fafcf8}
.interpretation-panel h3{margin:0;font-size:17px}.interpretation-panel>p{margin:0;color:#607065;font-size:12px;line-height:1.6}
.interpret-actions{display:flex;gap:10px;align-items:center}.interpret-actions .el-select{width:155px}
.interpret-result{padding:15px;border:1px solid #e1e5dc;border-radius:8px;background:#fff}
.interpret-result h4{margin:0 0 12px}.interpret-result p{margin:6px 0;line-height:1.65;font-size:13px}
.interpret-result small{color:#687c6e}.interpret-result .unanswered{color:#866943}
</style>
