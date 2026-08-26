<template>
  <div class="workbench study-page">
    <section class="workbench-commandbar">
      <div><span class="eyebrow study-eyebrow">EVIDENCE · ACCESS · PRESENT</span><h1>文献工作台</h1><p>明确资料范围，先审提纲，再生成可追溯的中文文献汇报。</p></div>
      <div class="workbench-summary" aria-label="汇报任务概况"><span><b>{{ decks.length }}</b>全部任务</span><span><b>{{ outlineReadyCount }}</b>待审提纲</span><span><b>{{ finishedDeckCount }}</b>可下载</span></div>
      <div class="workbench-boundary"><b>本地优先</b><span>不读取 Chrome Cookie、密码或会话文件</span></div>
    </section>

    <el-tabs v-model="tab" class="workspace-tabs workbench-tabs">
      <el-tab-pane label="研究任务" name="deck">
        <div class="deck-flowbar">
          <el-steps :active="stepActive" finish-status="success" simple>
            <el-step title="选择证据" /><el-step title="设定汇报目标" /><el-step title="审阅提纲" /><el-step title="渲染与验收" />
          </el-steps>
          <div class="scope-summary"><b>当前范围</b><span>{{ scopeSummary }}</span></div>
        </div>
        <div class="deck-workspace">
          <el-card shadow="never" class="scope-panel">
            <template #header><div class="panel-heading"><span>01</span><div><b>资料与证据范围</b><small>所有输出都受这里的选择约束</small></div></div></template>
            <el-select v-model="form.book_id" filterable placeholder="选择已解析文献" style="width:100%" @change="loadBook">
              <el-option v-for="b in readyBooks" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <div class="section-label">章节范围</div>
            <el-tree ref="chapterTree" :data="chapters" node-key="id" show-checkbox @check="onChapterCheck"
              :props="{ label: 'title', children: 'children' }" class="chapter-tree" empty-text="选择文献后显示章节" />
            <template v-if="linkedResources.length">
              <div class="section-label">补充材料与其他资源</div>
              <el-checkbox-group v-model="form.resource_ids" class="resource-checks"><el-checkbox v-for="resource in linkedResources" :key="resource.id" :value="resource.id">{{ resource.role === 'supplementary' ? 'SI' : resource.role }} · {{ resource.title }}</el-checkbox></el-checkbox-group>
            </template>
            <div class="section-label">补充选段</div>
            <el-input v-model="form.selected_text" type="textarea" :rows="5" maxlength="20000" show-word-limit placeholder="从阅读器带入选段，或在此粘贴需要重点汇报的原文。" />
          </el-card>

          <el-card shadow="never" class="brief-panel">
            <template #header><div class="panel-heading"><span>02</span><div><b>汇报目标与输出约束</b><small>先定义听众应该理解什么，再生成提纲</small></div></div></template>
            <div class="communication-job"><span>汇报目标</span><p>{{ communicationJob }}</p></div>
            <el-form label-position="top">
              <div class="two-col"><el-form-item label="目标受众"><el-input v-model="form.audience" placeholder="例如：公共管理课程同学" /></el-form-item><el-form-item label="汇报用途"><el-input v-model="form.purpose" placeholder="例如：解释核心发现并讨论局限" /></el-form-item></div>
              <div class="two-col"><el-form-item label="汇报时长"><el-input-number v-model="form.duration_minutes" :min="5" :max="90" /><span class="field-unit">分钟</span></el-form-item><el-form-item label="目标页数"><el-input-number v-model="form.slide_count" :min="6" :max="18" /><span class="field-unit">页</span></el-form-item></div>
              <el-form-item label="使用场景"><el-segmented v-model="form.use_scope" :options="useScopeOptions" /></el-form-item>
              <el-collapse class="advanced-settings"><el-collapse-item title="高级证据与图像设置" name="advanced"><el-form-item label="证据取样预算"><el-slider v-model="form.max_source_chars" :min="16000" :max="100000" :step="4000" show-input /></el-form-item><el-checkbox v-model="form.include_figures">优先选择支持核心主张的来源图像</el-checkbox><el-checkbox v-if="form.include_figures" v-model="form.rights_acknowledged">我已核对所选资源的使用范围；未知权利内容仍可能被排除</el-checkbox></el-collapse-item></el-collapse>
            </el-form>
          </el-card>

          <el-card shadow="never" class="delivery-panel">
            <template #header><div class="panel-heading"><span>03</span><div><b>输出门禁</b><small>生成前确认范围与质量规则</small></div></div></template>
            <div class="gate-list">
              <div :class="{ready:form.book_id}"><i>{{ form.book_id ? '✓' : '1' }}</i><span><b>来源已限定</b><small>{{ form.book_id ? scopeSummary : '请选择主文献' }}</small></span></div>
              <div class="ready"><i>✓</i><span><b>证据叙事</b><small>一页一个主张，按论文类型组织论证</small></span></div>
              <div class="ready"><i>✓</i><span><b>来源可回溯</b><small>事实页写入 chunk、章节或页码来源</small></span></div>
              <div :class="{ready:!form.include_figures || form.rights_acknowledged}"><i>{{ !form.include_figures || form.rights_acknowledged ? '✓' : '!' }}</i><span><b>图像权利</b><small>{{ !form.include_figures ? '本次不复用来源图像' : form.rights_acknowledged ? '已确认，仍会执行资源门禁' : '请在高级设置中确认使用范围' }}</small></span></div>
            </div>
            <div class="output-spec"><b>默认输出</b><span>中文可编辑 PPTX</span><span>术语保持一致</span><span>逐页演讲者备注</span><span>真实渲染与溢出检查</span></div>
            <el-button class="generate" type="primary" size="large" :loading="generating" :disabled="!form.book_id" @click="generate">生成并进入提纲审阅</el-button>
            <p class="generate-tip">AI 只使用所选来源；资料不足时保留缺口，不补写未经支持的结论。</p>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="合法获取全文" name="access">
        <div class="access-route" aria-label="全文获取流程">
          <div><i>1</i><span><b>识别文献</b><small>DOI、arXiv 或详情页</small></span></div>
          <div><i>2</i><span><b>选择合法路径</b><small>开放全文优先，馆藏登录接续</small></span></div>
          <div><i>3</i><span><b>校验后导入</b><small>拒绝登录页、HTML 与伪 PDF</small></span></div>
        </div>
        <div class="access-grid">
          <el-card shadow="never" class="access-main">
            <template #header><div class="card-head"><div><b>查找可用全文</b><small>系统只解析公开链接；需要登录时交给浏览器完成</small></div><el-tag type="success" effect="plain">开放获取优先</el-tag></div></template>
            <el-form label-position="top">
              <el-form-item label="DOI、arXiv ID、论文页面或 PDF 地址"><el-input v-model="access.query" placeholder="10.xxxx/... / 2401.01234 / https://论文详情页或PDF" /></el-form-item>
              <el-form-item label="是否需要补充材料（必须明确选择）"><el-radio-group v-model="access.include_si"><el-radio :value="false">不需要</el-radio><el-radio :value="true">需要</el-radio></el-radio-group></el-form-item>
              <div class="access-actions"><el-button type="primary" :loading="resolving" @click="resolveAccess">查找开放全文</el-button><el-button @click="handoff">通过图书馆 / CARSI</el-button></div>
            </el-form>
            <div v-if="candidates.length" class="candidate-list">
              <div class="result-heading"><b>发现 {{ candidates.length }} 条获取路径</b><span>直接下载仍会先验证文件类型</span></div>
              <div v-for="c in candidates" :key="c.url" class="candidate"><div><b>{{ c.label }}</b><small>{{ c.url }}</small><small v-if="c.message" class="candidate-note">{{ c.message }}</small></div><el-button v-if="c.direct_download" type="success" plain :loading="importingUrl===c.url" @click="importCandidate(c)">校验并导入</el-button><el-button v-else type="primary" plain @click="browserCandidate(c)">在 Chrome 打开</el-button></div>
            </div>
            <div v-else-if="accessSearched" class="access-empty"><b>没有发现可直接导入的开放 PDF</b><span>可继续使用学校馆藏入口，或在 Chrome 登录后打开原始详情页并手动下载。</span><el-button plain @click="handoff">转到图书馆 / CARSI</el-button></div>
            <el-alert class="boundary" type="warning" :closable="false" title="不会绕过付费墙、DRM、验证码或双因素认证；若拿到登录页而非 PDF，校验会拒绝导入。" />
          </el-card>
          <el-card shadow="never" class="access-config">
            <template #header><div class="card-head"><div><b>馆藏与开放服务</b><small>设置一次，之后作为失败后的接续路径</small></div></div></template>
            <el-form label-position="top"><el-form-item label="学校图书馆 / CARSI 检索入口（HTTPS）"><el-input v-model="config.library_resource_url" /></el-form-item>
              <el-form-item label="Unpaywall 联系邮箱"><el-input v-model="config.unpaywall_email" /></el-form-item>
              <el-button type="primary" plain :loading="savingConfig" @click="saveConfig">保存入口</el-button></el-form>
            <div class="provider-list"><div v-for="p in config.providers || []" :key="p.id"><span>{{ p.label }}</span><el-tag size="small" :type="p.implemented?'success':'info'">{{ p.implemented ? '可用' : '扩展点' }}</el-tag></div></div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="来源与权利" name="rights">
        <el-card shadow="never">
          <template #header><div class="card-head"><div><b>正文、SI 与来源权利</b><small>权利状态按资源记录，不自动推断法律许可</small></div><el-button type="primary" :disabled="!form.book_id" @click="newResource">添加资源</el-button></div></template>
          <el-select v-model="form.book_id" filterable placeholder="选择主文献" style="width:min(520px,100%)" @change="loadBook"><el-option v-for="b in readyBooks" :key="b.id" :label="b.title" :value="b.id" /></el-select>
          <div v-if="form.book_id" class="rights-summary"><span><b>{{ resources.length }}</b>登记资源</span><span><b>{{ reusableResourceCount }}</b>确认可复用</span><span><b>{{ unresolvedResourceCount }}</b>待核对权利</span></div>
          <el-table :data="resources" style="margin-top:14px" empty-text="尚未登记来源资源">
            <el-table-column prop="title" label="资源" min-width="240" /><el-table-column prop="role" label="类型" width="120" />
            <el-table-column prop="license_expression" label="许可证" width="150" /><el-table-column prop="rights_status" label="权利状态" width="150" />
            <el-table-column label="可复用" width="90"><template #default="{row}">{{ row.allow_reuse ? '是' : '否' }}</template></el-table-column>
            <el-table-column label="操作" width="100"><template #default="{row}"><el-button link @click="editResource(row)">编辑</el-button></template></el-table-column>
          </el-table>
          <el-alert class="boundary" type="warning" :closable="false" title="许可证说明授权条件；Rights Statement 描述版权状态。系统不会把“开放获取”自动等同于“允许复制图表”。" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="输出记录" name="records">
        <el-card shadow="never" class="history records-card">
          <template #header><div class="card-head"><div><b>汇报任务与输出</b><small>提纲、审计、预览和下载集中管理</small></div><el-button plain size="small" @click="loadDecks">刷新状态</el-button></div></template>
          <el-table :data="decks" empty-text="还没有汇报任务，请先在“研究任务”中生成提纲">
            <el-table-column prop="title" label="文献汇报" min-width="280" />
            <el-table-column label="叙事类型" width="130"><template #default="{row}">{{ typeLabel(row.paper_type) }}</template></el-table-column>
            <el-table-column label="当前阶段" width="110"><template #default="{row}"><el-tag :type="row.status==='done'?'success':row.status==='failed'?'danger':'warning'">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="证据覆盖" width="130"><template #default="{row}">{{ percent(row.qa?.coverage?.content_coverage) }} / {{ percent(row.qa?.coverage?.structure_coverage) }}</template></el-table-column>
            <el-table-column label="来源审计" width="120"><template #default="{row}">{{ row.qa?.claim_source?.ok ? '已通过' : row.qa?.claim_source?.blocking_slides?.length ? `${row.qa.claim_source.blocking_slides.length} 页待处理` : '待执行' }}</template></el-table-column>
            <el-table-column label="操作" width="250"><template #default="{row}"><el-button v-if="row.outline_editable" link type="primary" @click="openOutline(row)">审阅提纲</el-button><el-button v-if="row.preview_count" link @click="openPreview(row)">视觉预览</el-button><el-link v-if="row.download_ready" type="success" :href="presentationDownloadUrl(row.id)">下载 PPTX</el-link></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="outlineVisible" title="PPTX 提纲审阅" width="min(1320px,96vw)" destroy-on-close>
      <div v-if="editingDeck" class="outline-toolbar">
        <div><b>内容覆盖 {{ percent(editingDeck.qa?.coverage?.content_coverage) }}</b><small>结构覆盖 {{ percent(editingDeck.qa?.coverage?.structure_coverage) }} · {{ editingDeck.qa?.coverage?.covered_groups || 0 }}/{{ editingDeck.qa?.coverage?.total_groups || 0 }} 个来源组</small></div>
        <el-tag :type="editingDeck.qa?.claim_source?.ok ? 'success' : 'warning'">{{ editingDeck.qa?.claim_source?.ok ? '来源审计通过' : `${editingDeck.qa?.claim_source?.blocking_slides?.length || 0} 页需处理` }}</el-tag>
      </div>
      <div v-if="activeSlide" class="outline-workspace">
        <aside class="outline-nav"><div class="outline-nav-title"><b>页面顺序</b><span>{{ outlineDraft.length }} 页</span></div><button v-for="(slide,index) in outlineDraft" :key="index" :class="{active:activeSlideIndex===index,warning:auditForSlide(index+1)?.issues?.length}" @click="activeSlideIndex=index"><i>{{ String(index+1).padStart(2,'0') }}</i><span><b>{{ slide.title || '未命名页面' }}</b><small>{{ index===0 ? '封面' : slide.claim || '尚未填写主张' }}</small></span></button><el-button plain class="outline-add" @click="addSlide">＋ 添加一页</el-button></aside>
        <main class="outline-editor"><div class="outline-editor-head"><div><span>第 {{ activeSlideIndex+1 }} 页</span><b>{{ activeSlideIndex===0 ? '封面信息' : '页面内容与叙事职责' }}</b></div><div><el-button text :disabled="activeSlideIndex===0" @click="moveSlide(activeSlideIndex,-1)">上移</el-button><el-button text :disabled="activeSlideIndex===outlineDraft.length-1" @click="moveSlide(activeSlideIndex,1)">下移</el-button><el-button text type="danger" :disabled="activeSlideIndex===0 || outlineDraft.length<=2" @click="removeActiveSlide">删除</el-button></div></div><label>页面标题<el-input v-model="activeSlide.title" maxlength="70" show-word-limit placeholder="用一句可直接讲出的结论作为标题" /></label><label v-if="activeSlideIndex>0">本页核心主张<el-input v-model="activeSlide.claim" type="textarea" :rows="3" maxlength="220" show-word-limit placeholder="这一页希望听众理解、相信或讨论什么？" /></label><label v-if="activeSlideIndex>0">支撑要点<el-input v-model="activeSlide.bulletsText" type="textarea" :rows="6" placeholder="每行一个要点，最多四条；细节放入演讲者备注。" /></label><div class="slide-writing-rule"><b>页面写作规则</b><span>一个叙事职责 · 一个主要主张 · 证据解释紧随事实 · 不用语言掩盖证据缺口</span></div></main>
        <aside class="outline-source"><div class="outline-source-title"><b>来源与审计</b><span>{{ activeSlideIndex===0 ? '封面无需来源' : `${activeSlide.source_ids?.length || 0} 个来源` }}</span></div><template v-if="activeSlideIndex>0"><el-select v-model="activeSlide.source_ids" multiple filterable collapse-tags placeholder="至少选择一个来源" style="width:100%"><el-option v-for="source in sourceOptions" :key="source.value" :label="source.label" :value="source.value" /></el-select><div v-if="activeSlideAudit?.issues?.length" class="audit-issues"><b>需要处理</b><span v-for="issue in activeSlideAudit.issues" :key="issue">{{ issue }}</span></div><div v-else class="audit-pass"><b>当前页没有阻断问题</b><span>保存后仍会重新检查数字、关键词和来源定位。</span></div></template><div class="source-note"><b>演讲者备注</b><span>最终文件会为外部主张与素材写入来源块；内部规划说明不会出现在观众可见页面。</span></div></aside>
      </div>
      <template #footer><el-button @click="outlineVisible=false">稍后继续</el-button><el-button :loading="savingOutline" @click="saveOutline">保存并重新审计</el-button><el-button type="primary" :loading="rendering" @click="submitRender">确认提纲并生成 PPTX</el-button></template>
    </el-dialog>

    <el-dialog v-model="previewVisible" title="真实 PowerPoint 渲染预览" width="min(1200px,96vw)">
      <div v-if="previewDeck" class="preview-toolbar"><div><b>{{ previewDeck.preview_count }} 页已渲染</b><span>逐页检查标题换行、内容溢出、图像裁切与视觉一致性</span></div><el-tag :type="previewDeck.qa?.visual?.ok ? 'success' : 'warning'">{{ previewDeck.qa?.visual?.ok ? '视觉检查通过' : '存在待复核项' }}</el-tag></div>
      <div class="preview-grid"><figure v-for="i in previewDeck?.preview_count || 0" :key="i"><img :src="presentationPreviewUrl(previewDeck.id,i)" :alt="`第 ${i} 页渲染预览`" loading="lazy" /><figcaption>第 {{ i }} 页</figcaption></figure></div>
    </el-dialog>

    <el-dialog v-model="resourceVisible" title="来源与权利信息" width="min(620px,94vw)">
      <el-form label-position="top"><div class="two-col"><el-form-item label="资源类型"><el-select v-model="resourceForm.role"><el-option label="正文" value="main" /><el-option label="补充材料 SI" value="supplementary" /><el-option label="图" value="figure" /><el-option label="表" value="table" /><el-option label="数据集" value="dataset" /><el-option label="其他" value="other" /></el-select></el-form-item><el-form-item label="关联已导入资料"><el-select v-model="resourceForm.resource_book_id" clearable filterable><el-option v-for="b in readyBooks" :key="b.id" :label="b.title" :value="b.id" /></el-select></el-form-item></div>
        <el-form-item label="资源标题"><el-input v-model="resourceForm.title" /></el-form-item><el-form-item label="来源 HTTPS 地址"><el-input v-model="resourceForm.source_url" /></el-form-item>
        <div class="two-col"><el-form-item label="许可证表达式"><el-input v-model="resourceForm.license_expression" placeholder="例如 CC-BY-4.0" /></el-form-item><el-form-item label="权利状态"><el-select v-model="resourceForm.rights_status"><el-option label="未评估" value="not_evaluated" /><el-option label="状态不确定" value="undetermined" /><el-option label="受版权保护" value="in_copyright" /><el-option label="开放许可" value="open_license" /><el-option label="已获授权" value="permission_granted" /><el-option label="公有领域" value="public_domain" /><el-option label="限制使用" value="restricted" /></el-select></el-form-item></div>
        <el-form-item label="RightsStatements URI"><el-input v-model="resourceForm.rights_statement_uri" /></el-form-item><el-form-item label="署名文本"><el-input v-model="resourceForm.attribution" type="textarea" :rows="2" /></el-form-item><el-checkbox v-model="resourceForm.allow_reuse">已确认允许在汇报中复用</el-checkbox>
      </el-form>
      <template #footer><el-button @click="resourceVisible=false">取消</el-button><el-button type="primary" :loading="savingResource" @click="saveResource">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBooks, getBook, listPresentations, presentationDownloadUrl,
  createPresentationOutline, updatePresentationOutline, renderPresentation, getPresentation, presentationPreviewUrl,
  getLiteratureConfig, updateLiteratureConfig, resolveLiterature, importOpenAccess, openLibraryHandoff, openBrowserHandoff,
  listLiteratureResources, createLiteratureResource, updateLiteratureResource } from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'

const route = useRoute(); const tab = ref(['deck','access','rights','records'].includes(route.query.tab) ? route.query.tab : 'deck')
const books = ref([]); const chapters = ref([]); const chapterTree = ref(); const decks = ref([]); const checkedChapterCount = ref(0)
const generating = ref(false); const resolving = ref(false); const candidates = ref([]); const accessSearched = ref(false); const importingUrl = ref(''); const savingConfig = ref(false)
const submittedTaskId = ref('')
const form = ref({ book_id: Number(route.query.bookId) || null, selected_text: sessionStorage.getItem('deckSelectedText') || '', resource_ids: [], audience: '课题组', purpose: '文献精读汇报', duration_minutes: 15, slide_count: 12, max_source_chars: 52000, use_scope: 'personal', include_figures: true, rights_acknowledged: false })
sessionStorage.removeItem('deckSelectedText')
const access = ref({ query: '', include_si: null }); const config = ref({ library_resource_url: '', unpaywall_email: '', providers: [] })
const resources = ref([]); const outlineVisible = ref(false); const previewVisible = ref(false); const resourceVisible = ref(false)
const editingDeck = ref(null); const previewDeck = ref(null); const outlineDraft = ref([])
const activeSlideIndex = ref(0)
const savingOutline = ref(false); const rendering = ref(false); const savingResource = ref(false)
const resourceForm = ref({ id: null, role: 'supplementary', resource_book_id: null, title: '', source_url: '', license_expression: '', rights_statement_uri: '', rights_status: 'not_evaluated', attribution: '', allow_reuse: false })
const readyBooks = computed(() => books.value.filter(x => x.status === 'ready'))
const outlineReadyCount = computed(() => decks.value.filter(x => x.status === 'outline_ready').length)
const finishedDeckCount = computed(() => decks.value.filter(x => x.download_ready).length)
const reusableResourceCount = computed(() => resources.value.filter(x => x.allow_reuse || ['open_license','permission_granted','public_domain'].includes(x.rights_status)).length)
const unresolvedResourceCount = computed(() => resources.value.filter(x => ['not_evaluated','undetermined','in_copyright','restricted'].includes(x.rights_status)).length)
const useScopeOptions = [{label:'个人学习',value:'personal'},{label:'内部组会',value:'internal'},{label:'公开发布',value:'public'},{label:'商业用途',value:'commercial'}]
const linkedResources = computed(() => resources.value.filter(x => x.resource_book_id && x.resource_book_id !== form.value.book_id))
const sourceOptions = computed(() => (editingDeck.value?.selection?.sources || []).map(source => ({ value: source.source_id, label: `${source.source_id}${source.chapter_title ? ` · ${source.chapter_title}` : ''}${source.page_start ? ` · p.${source.page_start}` : ' · 无页码'}` })))
const scopeSummary = computed(() => {
  if (!form.value.book_id) return '尚未选择文献'
  const segments = []
  if (checkedChapterCount.value) segments.push(`${checkedChapterCount.value} 个章节`)
  if (form.value.selected_text.trim()) segments.push(`${form.value.selected_text.trim().length} 字选段`)
  return segments.length ? segments.join(' + ') : '整篇文献（优先使用前部证据）'
})
const communicationJob = computed(() => `汇报结束时，${form.value.audience || '听众'}应能够${form.value.purpose || '理解论文的核心问题、证据与边界'}。`)
const activeSlide = computed(() => outlineDraft.value[activeSlideIndex.value] || null)
const activeSlideAudit = computed(() => auditForSlide(activeSlideIndex.value + 1))
const stepActive = computed(() => submittedTaskId.value ? 2 : form.value.book_id ? 1 : 0)
const typeLabel = (x) => ({discovery:'发现 / 机制',methods:'方法 / 算法',resource:'资源 / 数据集',clinical:'临床 / 人群',materials:'材料 / 工程',review:'综述 / 观点'}[x] || '待识别')
const onChapterCheck = (_node, state) => { checkedChapterCount.value = state.checkedKeys?.length || 0 }
const statusLabel = (x) => ({pending:'排队中',outlining:'生成提纲',outline_ready:'待确认',rendering:'真实渲染',running:'生成中',done:'已完成',failed:'失败'}[x] || x)
const percent = (value) => value == null ? '—' : `${Math.round(value * 100)}%`
const loadResources = async () => { resources.value = form.value.book_id ? await listLiteratureResources(form.value.book_id) : [] }
const loadBook = async () => { if (!form.value.book_id) return; try { const [b] = await Promise.all([getBook(form.value.book_id), loadResources()]); chapters.value = b.chapters || []; checkedChapterCount.value = 0; form.value.resource_ids = form.value.resource_ids.filter(id => resources.value.some(r => r.id === id)); await loadDecks() } catch(e) { ElMessage.error(`无法加载文献范围：${e.message}。请返回资料库确认解析已经完成。`) } }
const loadDecks = async () => { decks.value = await listPresentations(form.value.book_id) }
const generate = async () => {
  if (!form.value.book_id) return ElMessage.warning('请先选择主文献，再限定章节或选段')
  generating.value = true
  try { const r = await createPresentationOutline({...form.value, chapter_ids: chapterTree.value?.getCheckedKeys() || [], chunk_ids: []}); submittedTaskId.value=r.task_id; notifyTaskSubmitted(); ElMessage.success(`提纲已进入任务中心；当前内容覆盖约 ${percent(r.coverage?.content_coverage)}`)
    await loadDecks()
  } catch(e){ElMessage.error(`提纲没有生成：${e.message}。当前选择会保留，可调整范围或模型设置后重试。`)} finally { generating.value=false }
}
const normalizeOutline = (outline) => outline.map(slide => ({...slide, source_ids: [...(slide.source_ids || [])], bulletsText: (slide.bullets || []).join('\n')}))
const openOutline = async (row) => { try { editingDeck.value = await getPresentation(row.id); outlineDraft.value = normalizeOutline(editingDeck.value.outline || []); activeSlideIndex.value = 0; outlineVisible.value = true } catch(e){ ElMessage.error(`无法打开提纲：${e.message}。请刷新输出记录后重试。`) } }
const moveSlide = (index, delta) => { const target=index+delta; if(target<0 || target>=outlineDraft.value.length) return; const [item]=outlineDraft.value.splice(index,1); outlineDraft.value.splice(target,0,item); activeSlideIndex.value=target }
const addSlide = () => { const source = sourceOptions.value[0]?.value; outlineDraft.value.push({title:'新增页面',kind:'content',claim:'',bulletsText:'',source_ids:source?[source]:[]}); activeSlideIndex.value=outlineDraft.value.length-1 }
const removeActiveSlide = () => { if(activeSlideIndex.value===0 || outlineDraft.value.length<=2) return; outlineDraft.value.splice(activeSlideIndex.value,1); activeSlideIndex.value=Math.min(activeSlideIndex.value,outlineDraft.value.length-1) }
const serializeOutline = () => outlineDraft.value.map((slide,index) => ({ title:slide.title, kind:index===0?'cover':slide.kind || 'content', claim:slide.claim || '', bullets:(slide.bulletsText || '').split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4), source_ids:index===0?[]:slide.source_ids || [] }))
const saveOutline = async () => { savingOutline.value=true; try { editingDeck.value=await updatePresentationOutline(editingDeck.value.id,serializeOutline()); outlineDraft.value=normalizeOutline(editingDeck.value.outline); ElMessage.success('提纲与来源审计已更新') } catch(e){ElMessage.error(e.message); throw e} finally{savingOutline.value=false} }
const submitRender = async () => { try { await saveOutline(); let confirmUnsupported=false; const blocked=editingDeck.value?.qa?.claim_source?.blocking_slides || []; if(blocked.length){ await ElMessageBox.confirm(`第 ${blocked.join('、')} 页存在无效来源或数字不一致。确认保留并继续渲染吗？`,'主张—来源审计',{type:'warning',confirmButtonText:'确认并继续'}); confirmUnsupported=true } rendering.value=true; const r=await renderPresentation(editingDeck.value.id,{use_scope:form.value.use_scope,include_figures:form.value.include_figures,rights_acknowledged:form.value.rights_acknowledged,confirm_unsupported_claims:confirmUnsupported}); submittedTaskId.value=r.task_id; notifyTaskSubmitted(); outlineVisible.value=false; ElMessage.success('已提交真实 PowerPoint 渲染与视觉审计'); await loadDecks() } catch(e){ if(e!=='cancel') ElMessage.error(e.message || '已取消') } finally{rendering.value=false} }
const auditForSlide = (slideNo) => editingDeck.value?.qa?.claim_source?.items?.find(x => x.slide === slideNo)
const openPreview = (row) => { previewDeck.value=row; previewVisible.value=true }
const newResource = () => { resourceForm.value={id:null,role:'supplementary',resource_book_id:null,title:'',source_url:'',license_expression:'',rights_statement_uri:'',rights_status:'not_evaluated',attribution:'',allow_reuse:false}; resourceVisible.value=true }
const editResource = (row) => { resourceForm.value={...row}; resourceVisible.value=true }
const saveResource = async () => { if(!resourceForm.value.title.trim()) return ElMessage.warning('请填写资源标题'); savingResource.value=true; try{const payload={...resourceForm.value}; delete payload.id; delete payload.book_id; delete payload.created_at; delete payload.updated_at; if(!payload.source_url) payload.source_url=null; if(!payload.license_expression) payload.license_expression=null; if(!payload.rights_statement_uri) payload.rights_statement_uri=null; if(resourceForm.value.id) await updateLiteratureResource(resourceForm.value.id,payload); else await createLiteratureResource(form.value.book_id,payload); resourceVisible.value=false; await loadResources(); ElMessage.success('来源与权利信息已保存')}catch(e){ElMessage.error(e.message)}finally{savingResource.value=false} }
const resolveAccess = async () => { if(!access.value.query.trim()) return ElMessage.warning('请先输入 DOI、arXiv ID 或文献页面地址'); if(access.value.include_si===null) return ElMessage.warning('请选择是否同时查找补充材料，系统不会替你推断'); resolving.value=true; accessSearched.value=false; candidates.value=[]; try{const r=await resolveLiterature(access.value); candidates.value=r.candidates || []; accessSearched.value=true; ElMessage[candidates.value.length?'success':'info'](candidates.value.length?`已找到 ${candidates.value.length} 条合法获取路径`:'未发现开放 PDF，可继续通过馆藏入口获取')}catch(e){ElMessage.error(`全文检索失败：${e.message}。输入内容已保留，可检查标识符或改走馆藏入口。`)}finally{resolving.value=false} }
const importCandidate = async (c) => { importingUrl.value=c.url; try{const r=await importOpenAccess({...access.value,url:c.url,provider:c.provider,title:''}); if(r.task_id) notifyTaskSubmitted(); ElMessage.success(r.duplicate?'该文献已在资料库，可直接前往阅读':'文件通过 PDF 校验，已进入全局任务中心解析')}catch(e){ElMessage.error(`无法导入：${e.message}。该地址可能返回登录页或 HTML，请改用浏览器下载后从资料库导入。`)}finally{importingUrl.value=''} }
const browserCandidate = async (c) => { try{const r=await openBrowserHandoff({...access.value,url:c.url}); window.open(r.url,'_blank','noopener'); ElMessage.info(`${r.instruction}；若没有新页面，请允许浏览器弹窗后重试。`)}catch(e){ElMessage.error(`无法打开浏览器接续路径：${e.message}`)} }
const handoff = async () => { if(access.value.include_si===null) return ElMessage.warning('请选择是否需要补充材料'); try{const r=await openLibraryHandoff(access.value); window.open(r.url,'_blank','noopener'); ElMessage.info(`${r.instruction}；若没有新页面，请允许浏览器弹窗后重试。`)}catch(e){ElMessage.error(`馆藏入口不可用：${e.message}。请在右侧检查 HTTPS 入口设置。`)} }
const saveConfig = async () => { savingConfig.value=true; try{await updateLiteratureConfig(config.value); ElMessage.success('馆藏入口与开放服务设置已保存')}catch(e){ElMessage.error(`入口没有保存：${e.message}`)}finally{savingConfig.value=false} }
onMounted(async()=>{ try{const [b,c]=await Promise.all([listBooks({page_size:100}),getLiteratureConfig()]); books.value=b.items; config.value=c; if(form.value.book_id) await loadBook(); else await loadDecks()}catch(e){ElMessage.error(e.message)} })
</script>

<style scoped>
.workbench{max-width:1500px}.hero h1{margin:5px 0;font:700 28px var(--study-font-reading);letter-spacing:2px}.hero p{color:rgba(247,241,232,.76)}.eyebrow{font-size:var(--study-font-size-xs);letter-spacing:2px;color:#d6b691}.guardrails{display:flex;flex-direction:column;text-align:right;font-size:var(--study-font-size-xs)}.guardrails span{margin-top:5px;color:rgba(247,241,232,.72)}.workspace-tabs{margin-top:12px}.workspace-tabs :deep(.el-tabs__item){color:rgba(245,240,232,.86);font-weight:650}.workspace-tabs :deep(.el-tabs__item:hover),.workspace-tabs :deep(.el-tabs__item.is-active){color:#e0ad70}.workspace-tabs :deep(.el-tabs__nav-wrap::after){background:rgba(245,240,232,.38)}.deck-grid,.access-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,.72fr);gap:14px}.section-label{margin:18px 0 8px;font-size:var(--study-font-size-xs);font-weight:700;color:#756958;letter-spacing:.5px}.chapter-tree{max-height:280px;overflow:auto;padding:8px;border-radius:8px;background:var(--el-fill-color-extra-light)}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}.generate{width:100%;margin-top:24px}.history{margin-top:14px}.card-head,.candidate,.provider-list div{display:flex;align-items:center;justify-content:space-between;gap:12px}.candidate-list{margin-top:16px}.candidate{padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}.candidate div{min-width:0}.candidate small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--el-text-color-secondary);max-width:680px}.candidate .candidate-note{margin-top:4px;color:#8b5a2b;white-space:normal}.boundary{margin-top:18px}.provider-list{margin-top:22px;padding-top:14px;border-top:1px solid var(--el-border-color-lighter)}.provider-list div{padding:7px 0;color:var(--el-text-color-regular)}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.guardrails{text-align:left}.deck-grid,.access-grid{grid-template-columns:1fr}.two-col{grid-template-columns:1fr}}
.deck-steps{display:grid;grid-template-columns:minmax(0,1fr) 250px;align-items:center;gap:20px;margin-bottom:14px;padding:16px 18px;border:1px solid rgba(139,90,43,.15);border-radius:14px;background:#f7f2e9}.scope-summary{display:flex;flex-direction:column;padding-left:18px;border-left:1px solid #ded3c3}.scope-summary b{font-size:11px;color:#8b5a2b}.scope-summary span{margin-top:5px;font-size:12px;color:#6d6559}@media(max-width:760px){.deck-steps{grid-template-columns:1fr}.scope-summary{padding:10px 0 0;border-left:0;border-top:1px solid #ded3c3}}
.resource-checks{display:flex;flex-direction:column;gap:5px}.card-head small{display:block;margin-top:4px;color:var(--el-text-color-secondary);font-weight:400}.outline-toolbar{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;margin-bottom:12px;border:1px solid #e5e7eb;border-radius:10px;background:#f7f2e9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.outline-toolbar div{display:flex;flex-direction:column}.outline-toolbar small{margin-top:4px;color:#7b7165}.outline-list{display:grid;gap:10px;max-height:62vh;overflow:auto;padding:2px 4px 10px}.outline-slide{border:1px solid #e5e7eb;box-shadow:0 1px 2px rgba(15,23,42,.06)}.outline-slide :deep(.el-card__body){display:grid;gap:9px}.audit-issues{padding:8px 10px;border-radius:7px;color:#9a5d28;background:#fff5e7;font-size:12px}.preview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.preview-grid img{width:100%;border:1px solid #e5e7eb;border-radius:8px;box-shadow:0 1px 2px rgba(15,23,42,.06)}@media(max-width:760px){.preview-grid{grid-template-columns:1fr}.outline-toolbar{align-items:flex-start;gap:8px;flex-direction:column}}

/* 文献工作台迁移：任务流 + 证据范围 + 输出门禁 */
.workbench-commandbar{display:grid;grid-template-columns:minmax(360px,1fr) auto minmax(220px,auto);align-items:center;gap:28px;padding:16px 22px;border:1px solid rgba(245,240,232,.16);border-radius:var(--study-radius-lg);background:linear-gradient(120deg,#173638,#536354);color:#f7f1e8;box-shadow:0 8px 24px rgba(19,43,43,.16)}.workbench-commandbar h1{margin:3px 0;font:700 25px var(--study-font-reading);letter-spacing:1.5px}.workbench-commandbar p{color:rgba(247,241,232,.76);font-size:var(--study-font-size-sm)}
.workbench-summary{display:flex;gap:22px}.workbench-summary span{display:flex;flex-direction:column;color:rgba(247,241,232,.72);font-size:var(--study-font-size-xs);text-align:center}.workbench-summary b{margin-bottom:2px;color:#fff;font:700 21px Georgia,serif}.workbench-boundary{display:flex;flex-direction:column;padding-left:18px;border-left:1px solid rgba(245,240,232,.22);font-size:var(--study-font-size-xs);text-align:right}.workbench-boundary span{margin-top:4px;color:rgba(247,241,232,.7)}
.workbench-tabs>.el-tabs__content{overflow:visible}.deck-flowbar{display:grid;grid-template-columns:minmax(0,1fr) 270px;align-items:center;gap:18px;margin-bottom:12px;padding:12px 16px;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:#f7f2e9;box-shadow:var(--study-shadow-sm)}.deck-flowbar :deep(.el-steps--simple){padding:8px 4px;background:transparent}.deck-flowbar .scope-summary{padding-left:16px}
.deck-workspace{display:grid;grid-template-columns:minmax(280px,.82fr) minmax(400px,1.25fr) minmax(280px,.78fr);align-items:start;gap:12px}.scope-panel,.brief-panel,.delivery-panel{min-width:0}.scope-panel{position:sticky;top:76px}.panel-heading{display:flex;align-items:center;gap:10px}.panel-heading>span{display:grid;width:28px;height:28px;place-items:center;border-radius:50%;background:#eee2d1;color:#8b5a2b;font:700 12px Georgia,serif}.panel-heading>div{display:flex;flex-direction:column}.panel-heading small{margin-top:3px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);font-weight:400}.chapter-tree{max-height:360px}.communication-job{margin-bottom:16px;padding:12px 14px;border-left:3px solid var(--el-color-primary);border-radius:0 8px 8px 0;background:#f7f0e5}.communication-job span{color:#8b5a2b;font-size:var(--study-font-size-xs);font-weight:700}.communication-job p{margin-top:5px;color:#49463f;line-height:1.65}.field-unit{margin-left:6px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.advanced-settings{margin-top:4px;border-top:1px solid var(--el-border-color-lighter);border-bottom:0}.advanced-settings :deep(.el-collapse-item__header){background:transparent;color:#716352}.advanced-settings :deep(.el-collapse-item__wrap){background:transparent}.advanced-settings :deep(.el-checkbox){height:auto;margin:5px 0;white-space:normal}
.gate-list{display:grid;gap:6px}.gate-list>div{display:flex;align-items:flex-start;gap:9px;padding:9px;border:1px solid #e7ddd0;border-radius:8px;background:#fbf7f0}.gate-list i{display:grid;width:22px;height:22px;flex:none;place-items:center;border-radius:50%;background:#ead9c5;color:#8b5a2b;font-style:normal;font-size:12px;font-weight:700}.gate-list .ready i{background:#dce9df;color:#47715c}.gate-list span{display:flex;min-width:0;flex-direction:column}.gate-list small{margin-top:2px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);line-height:1.45}.output-spec{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px;padding-top:13px;border-top:1px solid var(--el-border-color-lighter)}.output-spec b{width:100%;font-size:var(--study-font-size-sm)}.output-spec span{padding:3px 7px;border-radius:6px;background:#f0e8dc;color:#6d6254;font-size:var(--study-font-size-xs)}.generate{margin-top:16px}.generate-tip{margin-top:8px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);line-height:1.6;text-align:center}.records-card{margin-top:0}.records-card :deep(.el-table__row){transition:background .16s ease}.records-card :deep(.el-table__row:hover){background:#fbf6ed}
.candidate{border-radius:8px;transition:background .16s ease,padding .16s ease}.candidate:hover{padding-inline:10px;background:#f7f0e5}.access-actions{display:flex;gap:8px;flex-wrap:wrap}
.access-route{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin-bottom:12px;overflow:hidden;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-card-border);box-shadow:var(--study-shadow-sm)}.access-route>div{display:flex;align-items:center;gap:10px;padding:12px 16px;background:#f7f2e9}.access-route i{display:grid;width:26px;height:26px;flex:none;place-items:center;border-radius:50%;background:#e5d4bd;color:#7c532f;font:700 12px Georgia,serif}.access-route span{display:flex;min-width:0;flex-direction:column}.access-route small{margin-top:2px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.result-heading{display:flex;justify-content:space-between;gap:12px;padding:12px 0 6px;border-bottom:1px solid var(--el-border-color-lighter)}.result-heading span{color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.access-empty{display:flex;align-items:flex-start;flex-direction:column;gap:7px;margin-top:16px;padding:16px;border:1px dashed #d8c7b0;border-radius:10px;background:#fbf7ef}.access-empty span{color:var(--el-text-color-secondary);font-size:var(--study-font-size-sm);line-height:1.6}.access-empty .el-button{margin-top:3px}.rights-summary{display:flex;gap:1px;width:min(520px,100%);margin-top:12px;overflow:hidden;border:1px solid var(--study-card-border);border-radius:9px;background:var(--study-card-border)}.rights-summary span{display:flex;flex:1;flex-direction:column;padding:9px 12px;background:#f8f3ea;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.rights-summary b{margin-bottom:2px;color:#514a40;font:700 17px Georgia,serif}

/* 提纲审阅：页导航、当前页编辑、来源审计 */
.outline-workspace{display:grid;grid-template-columns:230px minmax(380px,1fr) 300px;height:min(64vh,680px);overflow:hidden;border:1px solid var(--study-card-border);border-radius:10px;background:#fbf8f1}.outline-nav,.outline-source{min-width:0;overflow-y:auto;background:#f3ecdf}.outline-nav{padding:10px;border-right:1px solid var(--el-border-color-lighter)}.outline-nav-title,.outline-source-title{display:flex;align-items:center;justify-content:space-between;padding:4px 5px 10px}.outline-nav-title span,.outline-source-title span{color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.outline-nav>button{display:flex;width:100%;align-items:flex-start;gap:9px;margin-bottom:5px;padding:9px;border:1px solid transparent;border-radius:8px;background:transparent;color:#514c43;text-align:left;cursor:pointer;transition:background .16s ease,border-color .16s ease,transform .16s ease}.outline-nav>button:hover{background:#fbf7ef;transform:translateX(2px)}.outline-nav>button.active{border-color:#d9c4a8;background:#fffaf2}.outline-nav>button.warning{box-shadow:inset 3px 0 0 #c78a3d}.outline-nav>button i{color:#9a7958;font:12px Georgia,serif}.outline-nav>button span{display:flex;min-width:0;flex-direction:column}.outline-nav>button b,.outline-nav>button small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.outline-nav>button small{margin-top:3px;color:var(--el-text-color-secondary);font-size:11px}.outline-nav .outline-add{width:100%;margin-top:8px}
.outline-editor{overflow-y:auto;padding:18px 20px;background:#fffdf8}.outline-editor-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.outline-editor-head>div:first-child{display:flex;flex-direction:column}.outline-editor-head span{color:#9a7958;font-size:var(--study-font-size-xs)}.outline-editor-head b{margin-top:3px;font-size:16px}.outline-editor label{display:grid;gap:6px;margin-bottom:15px;color:#665d50;font-size:var(--study-font-size-sm);font-weight:700}.slide-writing-rule{display:flex;flex-direction:column;margin-top:18px;padding:11px 12px;border-radius:8px;background:#f2ece1}.slide-writing-rule span{margin-top:4px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);line-height:1.55}
.outline-source{padding:12px;border-left:1px solid var(--el-border-color-lighter)}.audit-issues{display:flex;flex-direction:column;gap:5px;margin-top:12px}.audit-issues b{color:#8d5f28}.audit-issues span:before{content:'·';margin-right:6px}.audit-pass{display:flex;flex-direction:column;margin-top:12px;padding:10px;border-radius:8px;background:#edf4ed;color:#47715c}.audit-pass span{margin-top:4px;color:#61766a;font-size:var(--study-font-size-xs);line-height:1.5}.source-note{display:flex;flex-direction:column;margin-top:14px;padding-top:13px;border-top:1px solid var(--el-border-color-lighter)}.source-note span{margin-top:5px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);line-height:1.6}
.preview-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;padding:10px 12px;border:1px solid var(--study-card-border);border-radius:8px;background:#f7f2e9}.preview-toolbar>div{display:flex;flex-direction:column}.preview-toolbar span{margin-top:3px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.preview-grid figure{margin:0}.preview-grid figcaption{padding:5px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs);text-align:center}

@media(max-width:1350px){.deck-workspace{grid-template-columns:minmax(280px,.8fr) minmax(420px,1.2fr)}.delivery-panel{grid-column:1/-1}.scope-panel{position:static}.workbench-commandbar{grid-template-columns:minmax(320px,1fr) auto}.workbench-boundary{grid-column:1/-1;padding:8px 0 0;border-left:0;border-top:1px solid rgba(245,240,232,.2);text-align:left}.outline-workspace{grid-template-columns:210px minmax(360px,1fr) 270px}}
@media(max-width:900px){.workbench-commandbar,.deck-flowbar,.deck-workspace{grid-template-columns:1fr}.workbench-summary{justify-content:flex-start}.workbench-summary span{text-align:left}.deck-flowbar .scope-summary{padding:10px 0 0;border-left:0;border-top:1px solid #ded3c3}.delivery-panel{grid-column:auto}.outline-workspace{grid-template-columns:190px minmax(360px,1fr)}.outline-source{grid-column:1/-1;max-height:230px;border-top:1px solid var(--el-border-color-lighter);border-left:0}.access-grid{grid-template-columns:1fr}}
@media(max-width:680px){.workbench-commandbar{padding:15px}.workbench-commandbar h1{font-size:22px}.workbench-summary{width:100%;justify-content:space-between}.two-col{grid-template-columns:1fr}.access-route{grid-template-columns:1fr}.rights-summary{flex-direction:column}.outline-workspace{display:flex;height:68vh;flex-direction:column;overflow:auto}.outline-nav{max-height:180px;border-right:0;border-bottom:1px solid var(--el-border-color-lighter)}.outline-editor{overflow:visible}.outline-source{overflow:visible;max-height:none}.preview-grid{grid-template-columns:1fr}.preview-toolbar{align-items:flex-start;flex-direction:column}}
</style>
