<template>
  <div class="workbench">
    <section class="hero">
      <div><span class="eyebrow">EVIDENCE · ACCESS · PRESENT</span><h1>文献工作台</h1>
        <p>合法获取全文，选取章节或原文选段，生成有来源定位的中文可编辑 PPTX。</p></div>
      <div class="guardrails"><b>隐私边界</b><span>不读取 Chrome Cookie / 密码 / 会话文件</span></div>
    </section>

    <el-tabs v-model="tab" class="workspace-tabs">
      <el-tab-pane label="生成中文汇报" name="deck">
        <div class="deck-steps">
          <el-steps :active="stepActive" finish-status="success" align-center>
            <el-step title="选择证据" description="整篇、章节或选段" />
            <el-step title="设置语境" description="受众、目的与时长" />
            <el-step title="后台生成" description="审计后下载 PPTX" />
          </el-steps>
          <div class="scope-summary"><b>当前范围</b><span>{{ scopeSummary }}</span></div>
        </div>
        <div class="deck-grid">
          <el-card shadow="never" class="selection-card">
            <template #header><b>01 选择证据范围</b></template>
            <el-select v-model="form.book_id" filterable placeholder="选择已解析文献" style="width:100%" @change="loadBook">
              <el-option v-for="b in readyBooks" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <div class="section-label">章节（不选则使用全文前部）</div>
            <el-tree ref="chapterTree" :data="chapters" node-key="id" show-checkbox default-expand-all @check="onChapterCheck"
              :props="{ label: 'title', children: 'children' }" class="chapter-tree" />
            <template v-if="resources.length">
              <div class="section-label">补充材料与其他资源</div>
              <el-checkbox-group v-model="form.resource_ids" class="resource-checks">
                <el-checkbox v-for="resource in linkedResources" :key="resource.id" :value="resource.id">
                  {{ resource.role === 'supplementary' ? 'SI' : resource.role }} · {{ resource.title }}
                </el-checkbox>
              </el-checkbox-group>
            </template>
            <div class="section-label">补充选段</div>
            <el-input v-model="form.selected_text" type="textarea" :rows="7" maxlength="20000" show-word-limit
              placeholder="可从阅读器选中文字后点击“用选段生成汇报”，也可在此粘贴原文。" />
          </el-card>

          <el-card shadow="never" class="settings-card">
            <template #header><b>02 设置汇报语境</b></template>
            <el-form label-position="top">
              <div class="two-col"><el-form-item label="受众"><el-input v-model="form.audience" /></el-form-item>
                <el-form-item label="汇报目的"><el-input v-model="form.purpose" /></el-form-item></div>
              <div class="two-col"><el-form-item label="时长（分钟）"><el-input-number v-model="form.duration_minutes" :min="5" :max="90" /></el-form-item>
                <el-form-item label="页数"><el-input-number v-model="form.slide_count" :min="6" :max="18" /></el-form-item></div>
              <el-form-item label="证据取样预算"><el-slider v-model="form.max_source_chars" :min="16000" :max="100000" :step="4000" show-input /></el-form-item>
              <div class="two-col"><el-form-item label="使用场景"><el-select v-model="form.use_scope" style="width:100%"><el-option label="个人学习" value="personal" /><el-option label="内部组会" value="internal" /><el-option label="公开发布" value="public" /><el-option label="商业用途" value="commercial" /></el-select></el-form-item>
                <el-form-item label="图像复用"><el-checkbox v-model="form.include_figures">尝试嵌入来源图像</el-checkbox></el-form-item></div>
              <el-checkbox v-if="form.include_figures" v-model="form.rights_acknowledged">我已核对所选资源的使用范围；未知权利内容仍可能被自动排除</el-checkbox>
              <el-alert :closable="false" type="info" show-icon title="AI 路由：先识别论文类型，再按主张—证据—边界组织；所有事实页保留 chunk / 页码来源。" />
              <el-button class="generate" type="primary" size="large" :loading="generating" @click="generate">生成可编辑提纲</el-button>
            </el-form>
          </el-card>
        </div>
        <el-card shadow="never" class="history">
          <template #header><div class="card-head"><b>汇报记录</b><el-button link @click="loadDecks">刷新</el-button></div></template>
          <el-table :data="decks" empty-text="尚未生成汇报">
            <el-table-column prop="title" label="文献汇报" min-width="280" />
            <el-table-column label="类型" width="130"><template #default="{row}">{{ typeLabel(row.paper_type) }}</template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{row}"><el-tag :type="row.status==='done'?'success':row.status==='failed'?'danger':'warning'">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="质量" width="110"><template #default="{row}">{{ row.qa?.ok ? '✓ 已审计' : row.qa?.issues?.length ? `${row.qa.issues.length} 项提示` : '—' }}</template></el-table-column>
            <el-table-column label="覆盖率" width="120"><template #default="{row}">{{ percent(row.qa?.coverage?.content_coverage) }} / {{ percent(row.qa?.coverage?.structure_coverage) }}</template></el-table-column>
            <el-table-column label="操作" width="250"><template #default="{row}"><el-button v-if="row.outline_editable" link type="primary" @click="openOutline(row)">编辑提纲</el-button><el-button v-if="row.preview_count" link @click="openPreview(row)">预览</el-button><el-link v-if="row.download_ready" type="success" :href="presentationDownloadUrl(row.id)">下载</el-link></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="合法获取全文" name="access">
        <div class="access-grid">
          <el-card shadow="never">
            <template #header><b>开放获取与馆藏入口</b></template>
            <el-form label-position="top">
              <el-form-item label="DOI、arXiv ID、论文页面或 PDF 地址"><el-input v-model="access.query" placeholder="10.xxxx/... / 2401.01234 / https://论文详情页或PDF" /></el-form-item>
              <el-form-item label="是否需要补充材料（必须明确选择）"><el-radio-group v-model="access.include_si"><el-radio :value="false">不需要</el-radio><el-radio :value="true">需要</el-radio></el-radio-group></el-form-item>
              <div class="access-actions"><el-button type="primary" :loading="resolving" @click="resolveAccess">查找开放全文</el-button><el-button @click="handoff">通过图书馆 / CARSI</el-button></div>
            </el-form>
            <div v-if="candidates.length" class="candidate-list">
              <div v-for="c in candidates" :key="c.url" class="candidate"><div><b>{{ c.label }}</b><small>{{ c.url }}</small><small v-if="c.message" class="candidate-note">{{ c.message }}</small></div><el-button v-if="c.direct_download" type="success" plain @click="importCandidate(c)">校验并导入</el-button><el-button v-else type="primary" plain @click="browserCandidate(c)">在 Chrome 打开</el-button></div>
            </div>
            <el-alert class="boundary" type="warning" :closable="false" title="不会绕过付费墙、DRM、验证码或双因素认证；若拿到登录页而非 PDF，校验会拒绝导入。" />
          </el-card>
          <el-card shadow="never">
            <template #header><b>入口设置</b></template>
            <el-form label-position="top"><el-form-item label="学校图书馆 / CARSI 检索入口（HTTPS）"><el-input v-model="config.library_resource_url" /></el-form-item>
              <el-form-item label="Unpaywall 联系邮箱"><el-input v-model="config.unpaywall_email" /></el-form-item>
              <el-button type="primary" plain @click="saveConfig">保存入口</el-button></el-form>
            <div class="provider-list"><div v-for="p in config.providers || []" :key="p.id"><span>{{ p.label }}</span><el-tag size="small" :type="p.implemented?'success':'info'">{{ p.implemented ? '可用' : '扩展点' }}</el-tag></div></div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="来源与权利" name="rights">
        <el-card shadow="never">
          <template #header><div class="card-head"><div><b>正文、SI 与来源权利</b><small>权利状态按资源记录，不自动推断法律许可</small></div><el-button type="primary" :disabled="!form.book_id" @click="newResource">添加资源</el-button></div></template>
          <el-select v-model="form.book_id" filterable placeholder="选择主文献" style="width:min(520px,100%)" @change="loadBook"><el-option v-for="b in readyBooks" :key="b.id" :label="b.title" :value="b.id" /></el-select>
          <el-table :data="resources" style="margin-top:14px" empty-text="尚未登记来源资源">
            <el-table-column prop="title" label="资源" min-width="240" /><el-table-column prop="role" label="类型" width="120" />
            <el-table-column prop="license_expression" label="许可证" width="150" /><el-table-column prop="rights_status" label="权利状态" width="150" />
            <el-table-column label="可复用" width="90"><template #default="{row}">{{ row.allow_reuse ? '是' : '否' }}</template></el-table-column>
            <el-table-column label="操作" width="100"><template #default="{row}"><el-button link @click="editResource(row)">编辑</el-button></template></el-table-column>
          </el-table>
          <el-alert class="boundary" type="warning" :closable="false" title="许可证说明授权条件；Rights Statement 描述版权状态。系统不会把“开放获取”自动等同于“允许复制图表”。" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="outlineVisible" title="PPTX 提纲预览与人工编辑" width="min(1040px,96vw)" destroy-on-close>
      <div v-if="editingDeck" class="outline-toolbar">
        <div><b>内容覆盖 {{ percent(editingDeck.qa?.coverage?.content_coverage) }}</b><small>结构覆盖 {{ percent(editingDeck.qa?.coverage?.structure_coverage) }} · {{ editingDeck.qa?.coverage?.covered_groups || 0 }}/{{ editingDeck.qa?.coverage?.total_groups || 0 }} 组</small></div>
        <el-tag :type="editingDeck.qa?.claim_source?.ok ? 'success' : 'warning'">{{ editingDeck.qa?.claim_source?.ok ? '来源审计通过' : `${editingDeck.qa?.claim_source?.blocking_slides?.length || 0} 页需处理` }}</el-tag>
      </div>
      <div class="outline-list">
        <el-card v-for="(slide,index) in outlineDraft" :key="index" shadow="never" class="outline-slide">
          <template #header><div class="card-head"><b>{{ String(index+1).padStart(2,'0') }}</b><div><el-button text :disabled="index===0" @click="moveSlide(index,-1)">上移</el-button><el-button text :disabled="index===outlineDraft.length-1" @click="moveSlide(index,1)">下移</el-button><el-button text type="danger" :disabled="index===0 || outlineDraft.length<=2" @click="outlineDraft.splice(index,1)">删除</el-button></div></div></template>
          <el-input v-model="slide.title" maxlength="70" placeholder="页面标题" />
          <el-input v-model="slide.claim" type="textarea" :rows="2" maxlength="220" placeholder="本页核心主张" />
          <el-input v-model="slide.bulletsText" type="textarea" :rows="3" placeholder="每行一个要点，最多四条" />
          <el-select v-if="index>0" v-model="slide.source_ids" multiple filterable collapse-tags placeholder="至少选择一个来源" style="width:100%"><el-option v-for="source in sourceOptions" :key="source.value" :label="source.label" :value="source.value" /></el-select>
          <div v-if="auditForSlide(index+1)?.issues?.length" class="audit-issues">{{ auditForSlide(index+1).issues.join('；') }}</div>
        </el-card>
      </div>
      <el-button plain style="width:100%" @click="addSlide">＋ 添加一页</el-button>
      <template #footer><el-button @click="outlineVisible=false">稍后处理</el-button><el-button :loading="savingOutline" @click="saveOutline">保存提纲</el-button><el-button type="primary" :loading="rendering" @click="submitRender">确认并真实渲染</el-button></template>
    </el-dialog>

    <el-dialog v-model="previewVisible" title="真实 PowerPoint 渲染预览" width="min(1200px,96vw)">
      <div class="preview-grid"><img v-for="i in previewDeck?.preview_count || 0" :key="i" :src="presentationPreviewUrl(previewDeck.id,i)" :alt="`第 ${i} 页`" loading="lazy" /></div>
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
import { listBooks, getBook, generatePresentation, listPresentations, presentationDownloadUrl,
  createPresentationOutline, updatePresentationOutline, renderPresentation, getPresentation, presentationPreviewUrl,
  getLiteratureConfig, updateLiteratureConfig, resolveLiterature, importOpenAccess, openLibraryHandoff, openBrowserHandoff,
  listLiteratureResources, createLiteratureResource, updateLiteratureResource } from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'

const route = useRoute(); const tab = ref(route.query.tab === 'access' ? 'access' : 'deck')
const books = ref([]); const chapters = ref([]); const chapterTree = ref(); const decks = ref([]); const checkedChapterCount = ref(0)
const generating = ref(false); const resolving = ref(false); const candidates = ref([])
const submittedTaskId = ref('')
const form = ref({ book_id: Number(route.query.bookId) || null, selected_text: sessionStorage.getItem('deckSelectedText') || '', resource_ids: [], audience: '课题组', purpose: '文献精读汇报', duration_minutes: 15, slide_count: 12, max_source_chars: 52000, use_scope: 'personal', include_figures: true, rights_acknowledged: false })
sessionStorage.removeItem('deckSelectedText')
const access = ref({ query: '', include_si: null }); const config = ref({ library_resource_url: '', unpaywall_email: '', providers: [] })
const resources = ref([]); const outlineVisible = ref(false); const previewVisible = ref(false); const resourceVisible = ref(false)
const editingDeck = ref(null); const previewDeck = ref(null); const outlineDraft = ref([])
const savingOutline = ref(false); const rendering = ref(false); const savingResource = ref(false)
const resourceForm = ref({ id: null, role: 'supplementary', resource_book_id: null, title: '', source_url: '', license_expression: '', rights_statement_uri: '', rights_status: 'not_evaluated', attribution: '', allow_reuse: false })
const readyBooks = computed(() => books.value.filter(x => x.status === 'ready'))
const linkedResources = computed(() => resources.value.filter(x => x.resource_book_id && x.resource_book_id !== form.value.book_id))
const sourceOptions = computed(() => (editingDeck.value?.selection?.sources || []).map(source => ({ value: source.source_id, label: `${source.source_id}${source.chapter_title ? ` · ${source.chapter_title}` : ''}${source.page_start ? ` · p.${source.page_start}` : ' · 无页码'}` })))
const scopeSummary = computed(() => {
  if (!form.value.book_id) return '尚未选择文献'
  const segments = []
  if (checkedChapterCount.value) segments.push(`${checkedChapterCount.value} 个章节`)
  if (form.value.selected_text.trim()) segments.push(`${form.value.selected_text.trim().length} 字选段`)
  return segments.length ? segments.join(' + ') : '整篇文献（优先使用前部证据）'
})
const stepActive = computed(() => submittedTaskId.value ? 3 : form.value.book_id ? 1 : 0)
const typeLabel = (x) => ({discovery:'发现 / 机制',methods:'方法 / 算法',resource:'资源 / 数据集',clinical:'临床 / 人群',materials:'材料 / 工程',review:'综述 / 观点'}[x] || '待识别')
const onChapterCheck = (_node, state) => { checkedChapterCount.value = state.checkedKeys?.length || 0 }
const statusLabel = (x) => ({pending:'排队中',outlining:'生成提纲',outline_ready:'待确认',rendering:'真实渲染',running:'生成中',done:'已完成',failed:'失败'}[x] || x)
const percent = (value) => value == null ? '—' : `${Math.round(value * 100)}%`
const loadResources = async () => { resources.value = form.value.book_id ? await listLiteratureResources(form.value.book_id) : [] }
const loadBook = async () => { if (!form.value.book_id) return; const [b] = await Promise.all([getBook(form.value.book_id), loadResources()]); chapters.value = b.chapters || []; form.value.resource_ids = form.value.resource_ids.filter(id => resources.value.some(r => r.id === id)); await loadDecks() }
const loadDecks = async () => { decks.value = await listPresentations(form.value.book_id) }
const generate = async () => {
  if (!form.value.book_id) return ElMessage.warning('请先选择文献')
  generating.value = true
  try { const r = await createPresentationOutline({...form.value, chapter_ids: chapterTree.value?.getCheckedKeys() || [], chunk_ids: []}); submittedTaskId.value=r.task_id; notifyTaskSubmitted(); ElMessage.success(`提纲已进入任务中心；当前内容覆盖约 ${percent(r.coverage?.content_coverage)}`)
    await loadDecks()
  } catch(e){ElMessage.error(e.message)} finally { generating.value=false }
}
const normalizeOutline = (outline) => outline.map(slide => ({...slide, source_ids: [...(slide.source_ids || [])], bulletsText: (slide.bullets || []).join('\n')}))
const openOutline = async (row) => { try { editingDeck.value = await getPresentation(row.id); outlineDraft.value = normalizeOutline(editingDeck.value.outline || []); outlineVisible.value = true } catch(e){ ElMessage.error(e.message) } }
const moveSlide = (index, delta) => { const target=index+delta; if(target<0 || target>=outlineDraft.value.length) return; const [item]=outlineDraft.value.splice(index,1); outlineDraft.value.splice(target,0,item) }
const addSlide = () => { const source = sourceOptions.value[0]?.value; outlineDraft.value.push({title:'新增页面',kind:'content',claim:'',bulletsText:'',source_ids:source?[source]:[]}) }
const serializeOutline = () => outlineDraft.value.map((slide,index) => ({ title:slide.title, kind:index===0?'cover':slide.kind || 'content', claim:slide.claim || '', bullets:(slide.bulletsText || '').split('\n').map(x=>x.trim()).filter(Boolean).slice(0,4), source_ids:index===0?[]:slide.source_ids || [] }))
const saveOutline = async () => { savingOutline.value=true; try { editingDeck.value=await updatePresentationOutline(editingDeck.value.id,serializeOutline()); outlineDraft.value=normalizeOutline(editingDeck.value.outline); ElMessage.success('提纲与来源审计已更新') } catch(e){ElMessage.error(e.message); throw e} finally{savingOutline.value=false} }
const submitRender = async () => { try { await saveOutline(); let confirmUnsupported=false; const blocked=editingDeck.value?.qa?.claim_source?.blocking_slides || []; if(blocked.length){ await ElMessageBox.confirm(`第 ${blocked.join('、')} 页存在无效来源或数字不一致。确认保留并继续渲染吗？`,'主张—来源审计',{type:'warning',confirmButtonText:'确认并继续'}); confirmUnsupported=true } rendering.value=true; const r=await renderPresentation(editingDeck.value.id,{use_scope:form.value.use_scope,include_figures:form.value.include_figures,rights_acknowledged:form.value.rights_acknowledged,confirm_unsupported_claims:confirmUnsupported}); submittedTaskId.value=r.task_id; notifyTaskSubmitted(); outlineVisible.value=false; ElMessage.success('已提交真实 PowerPoint 渲染与视觉审计'); await loadDecks() } catch(e){ if(e!=='cancel') ElMessage.error(e.message || '已取消') } finally{rendering.value=false} }
const auditForSlide = (slideNo) => editingDeck.value?.qa?.claim_source?.items?.find(x => x.slide === slideNo)
const openPreview = (row) => { previewDeck.value=row; previewVisible.value=true }
const newResource = () => { resourceForm.value={id:null,role:'supplementary',resource_book_id:null,title:'',source_url:'',license_expression:'',rights_statement_uri:'',rights_status:'not_evaluated',attribution:'',allow_reuse:false}; resourceVisible.value=true }
const editResource = (row) => { resourceForm.value={...row}; resourceVisible.value=true }
const saveResource = async () => { if(!resourceForm.value.title.trim()) return ElMessage.warning('请填写资源标题'); savingResource.value=true; try{const payload={...resourceForm.value}; delete payload.id; delete payload.book_id; delete payload.created_at; delete payload.updated_at; if(!payload.source_url) payload.source_url=null; if(!payload.license_expression) payload.license_expression=null; if(!payload.rights_statement_uri) payload.rights_statement_uri=null; if(resourceForm.value.id) await updateLiteratureResource(resourceForm.value.id,payload); else await createLiteratureResource(form.value.book_id,payload); resourceVisible.value=false; await loadResources(); ElMessage.success('来源与权利信息已保存')}catch(e){ElMessage.error(e.message)}finally{savingResource.value=false} }
const resolveAccess = async () => { if(access.value.include_si===null) return ElMessage.warning('请明确是否需要补充材料'); resolving.value=true; try{const r=await resolveLiterature(access.value); candidates.value=r.candidates; if(!r.candidates.length) ElMessage.info('未发现直接开放全文，可尝试馆藏入口')}catch(e){ElMessage.error(e.message)}finally{resolving.value=false} }
const importCandidate = async (c) => { try{const r=await importOpenAccess({...access.value,url:c.url,provider:c.provider,title:''}); if(r.task_id) notifyTaskSubmitted(); ElMessage.success(r.duplicate?'文献已在资料库':'PDF 已校验，已进入任务中心解析')}catch(e){ElMessage.error(e.message)} }
const browserCandidate = async (c) => { try{const r=await openBrowserHandoff({...access.value,url:c.url}); window.open(r.url,'_blank','noopener'); ElMessage.info(r.instruction)}catch(e){ElMessage.error(e.message)} }
const handoff = async () => { if(access.value.include_si===null) return ElMessage.warning('请明确是否需要补充材料'); try{const r=await openLibraryHandoff(access.value); window.open(r.url,'_blank','noopener'); ElMessage.info(r.instruction)}catch(e){ElMessage.error(e.message)} }
const saveConfig = async () => { try{await updateLiteratureConfig(config.value); ElMessage.success('入口设置已保存')}catch(e){ElMessage.error(e.message)} }
onMounted(async()=>{ try{const [b,c]=await Promise.all([listBooks({page_size:100}),getLiteratureConfig()]); books.value=b.items; config.value=c; if(form.value.book_id) await loadBook(); else await loadDecks()}catch(e){ElMessage.error(e.message)} })
</script>

<style scoped>
.workbench{max-width:1500px;margin:0 auto}.hero{display:flex;justify-content:space-between;align-items:end;gap:24px;padding:24px 28px;border-radius:18px;color:#f7f1e8;background:linear-gradient(120deg,#173638,#536354);box-shadow:0 14px 35px rgba(19,43,43,.18)}.hero h1{margin:5px 0;font:700 30px Georgia,'STSong',serif;letter-spacing:2px}.hero p{color:rgba(247,241,232,.72)}.eyebrow{font-size:10px;letter-spacing:2.5px;color:#d6b691}.guardrails{display:flex;flex-direction:column;text-align:right;font-size:12px}.guardrails span{margin-top:5px;color:rgba(247,241,232,.65)}.workspace-tabs{margin-top:12px}.workspace-tabs :deep(.el-tabs__item){color:rgba(245,240,232,.78);font-weight:650}.workspace-tabs :deep(.el-tabs__item:hover),.workspace-tabs :deep(.el-tabs__item.is-active){color:#d8a264}.workspace-tabs :deep(.el-tabs__nav-wrap::after){background:rgba(245,240,232,.38)}.deck-grid,.access-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,.72fr);gap:14px}.section-label{margin:18px 0 8px;font-size:12px;font-weight:700;color:#756958;letter-spacing:.5px}.chapter-tree{max-height:280px;overflow:auto;padding:8px;border-radius:8px;background:var(--el-fill-color-extra-light)}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}.generate{width:100%;margin-top:24px}.history{margin-top:14px}.card-head,.candidate,.provider-list div{display:flex;align-items:center;justify-content:space-between;gap:12px}.candidate-list{margin-top:16px}.candidate{padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}.candidate div{min-width:0}.candidate small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--el-text-color-secondary);max-width:680px}.candidate .candidate-note{margin-top:4px;color:#8b5a2b;white-space:normal}.boundary{margin-top:18px}.provider-list{margin-top:22px;padding-top:14px;border-top:1px solid var(--el-border-color-lighter)}.provider-list div{padding:7px 0;color:var(--el-text-color-regular)}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.guardrails{text-align:left}.deck-grid,.access-grid{grid-template-columns:1fr}.two-col{grid-template-columns:1fr}}
.deck-steps{display:grid;grid-template-columns:minmax(0,1fr) 250px;align-items:center;gap:20px;margin-bottom:14px;padding:16px 18px;border:1px solid rgba(139,90,43,.15);border-radius:14px;background:#f7f2e9}.scope-summary{display:flex;flex-direction:column;padding-left:18px;border-left:1px solid #ded3c3}.scope-summary b{font-size:11px;color:#8b5a2b}.scope-summary span{margin-top:5px;font-size:12px;color:#6d6559}@media(max-width:760px){.deck-steps{grid-template-columns:1fr}.scope-summary{padding:10px 0 0;border-left:0;border-top:1px solid #ded3c3}}
.resource-checks{display:flex;flex-direction:column;gap:5px}.card-head small{display:block;margin-top:4px;color:var(--el-text-color-secondary);font-weight:400}.outline-toolbar{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;margin-bottom:12px;border:1px solid #e5e7eb;border-radius:10px;background:#f7f2e9;box-shadow:0 1px 2px rgba(15,23,42,.06)}.outline-toolbar div{display:flex;flex-direction:column}.outline-toolbar small{margin-top:4px;color:#7b7165}.outline-list{display:grid;gap:10px;max-height:62vh;overflow:auto;padding:2px 4px 10px}.outline-slide{border:1px solid #e5e7eb;box-shadow:0 1px 2px rgba(15,23,42,.06)}.outline-slide :deep(.el-card__body){display:grid;gap:9px}.audit-issues{padding:8px 10px;border-radius:7px;color:#9a5d28;background:#fff5e7;font-size:12px}.preview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.preview-grid img{width:100%;border:1px solid #e5e7eb;border-radius:8px;box-shadow:0 1px 2px rgba(15,23,42,.06)}@media(max-width:760px){.preview-grid{grid-template-columns:1fr}.outline-toolbar{align-items:flex-start;gap:8px;flex-direction:column}}
</style>
