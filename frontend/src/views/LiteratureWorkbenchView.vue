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
              <el-alert :closable="false" type="info" show-icon title="AI 路由：先识别论文类型，再按主张—证据—边界组织；所有事实页保留 chunk / 页码来源。" />
              <el-button class="generate" type="primary" size="large" :loading="generating" @click="generate">提交到任务中心</el-button>
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
            <el-table-column label="操作" width="120"><template #default="{row}"><el-link v-if="row.download_ready" type="primary" :href="presentationDownloadUrl(row.id)">下载 PPTX</el-link></template></el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="合法获取全文" name="access">
        <div class="access-grid">
          <el-card shadow="never">
            <template #header><b>开放获取与馆藏入口</b></template>
            <el-form label-position="top">
              <el-form-item label="DOI、arXiv ID 或开放获取 PDF 地址"><el-input v-model="access.query" placeholder="10.xxxx/... / 2401.01234 / https://...pdf" /></el-form-item>
              <el-form-item label="是否需要补充材料（必须明确选择）"><el-radio-group v-model="access.include_si"><el-radio :value="false">不需要</el-radio><el-radio :value="true">需要</el-radio></el-radio-group></el-form-item>
              <div class="access-actions"><el-button type="primary" :loading="resolving" @click="resolveAccess">查找开放全文</el-button><el-button @click="handoff">通过图书馆 / CARSI</el-button></div>
            </el-form>
            <div v-if="candidates.length" class="candidate-list">
              <div v-for="c in candidates" :key="c.url" class="candidate"><div><b>{{ c.label }}</b><small>{{ c.url }}</small></div><el-button type="success" plain @click="importCandidate(c)">校验并导入</el-button></div>
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
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listBooks, getBook, generatePresentation, listPresentations, presentationDownloadUrl,
  getLiteratureConfig, updateLiteratureConfig, resolveLiterature, importOpenAccess, openLibraryHandoff } from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'

const route = useRoute(); const tab = ref(route.query.tab === 'access' ? 'access' : 'deck')
const books = ref([]); const chapters = ref([]); const chapterTree = ref(); const decks = ref([]); const checkedChapterCount = ref(0)
const generating = ref(false); const resolving = ref(false); const candidates = ref([])
const submittedTaskId = ref('')
const form = ref({ book_id: Number(route.query.bookId) || null, selected_text: sessionStorage.getItem('deckSelectedText') || '', audience: '课题组', purpose: '文献精读汇报', duration_minutes: 15, slide_count: 12 })
sessionStorage.removeItem('deckSelectedText')
const access = ref({ query: '', include_si: null }); const config = ref({ library_resource_url: '', unpaywall_email: '', providers: [] })
const readyBooks = computed(() => books.value.filter(x => x.status === 'ready'))
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
const statusLabel = (x) => ({pending:'排队中',running:'生成中',done:'已完成',failed:'失败'}[x] || x)
const loadBook = async () => { if (!form.value.book_id) return; const b = await getBook(form.value.book_id); chapters.value = b.chapters || []; await loadDecks() }
const loadDecks = async () => { decks.value = await listPresentations(form.value.book_id) }
const generate = async () => {
  if (!form.value.book_id) return ElMessage.warning('请先选择文献')
  generating.value = true
  try { const r = await generatePresentation({...form.value, chapter_ids: chapterTree.value?.getCheckedKeys() || [], chunk_ids: [], include_figures: true}); submittedTaskId.value=r.task_id; notifyTaskSubmitted(); ElMessage.success('已进入全局任务中心，可离开本页继续工作')
    await loadDecks()
  } catch(e){ElMessage.error(e.message)} finally { generating.value=false }
}
const resolveAccess = async () => { if(access.value.include_si===null) return ElMessage.warning('请明确是否需要补充材料'); resolving.value=true; try{const r=await resolveLiterature(access.value); candidates.value=r.candidates; if(!r.candidates.length) ElMessage.info('未发现直接开放全文，可尝试馆藏入口')}catch(e){ElMessage.error(e.message)}finally{resolving.value=false} }
const importCandidate = async (c) => { try{const r=await importOpenAccess({...access.value,url:c.url,provider:c.provider,title:''}); if(r.task_id) notifyTaskSubmitted(); ElMessage.success(r.duplicate?'文献已在资料库':'PDF 已校验，已进入任务中心解析')}catch(e){ElMessage.error(e.message)} }
const handoff = async () => { if(access.value.include_si===null) return ElMessage.warning('请明确是否需要补充材料'); try{const r=await openLibraryHandoff(access.value); window.open(r.url,'_blank','noopener'); ElMessage.info(r.instruction)}catch(e){ElMessage.error(e.message)} }
const saveConfig = async () => { try{await updateLiteratureConfig(config.value); ElMessage.success('入口设置已保存')}catch(e){ElMessage.error(e.message)} }
onMounted(async()=>{ try{const [b,c]=await Promise.all([listBooks({page_size:100}),getLiteratureConfig()]); books.value=b.items; config.value=c; if(form.value.book_id) await loadBook(); else await loadDecks()}catch(e){ElMessage.error(e.message)} })
</script>

<style scoped>
.workbench{max-width:1500px;margin:0 auto}.hero{display:flex;justify-content:space-between;align-items:end;gap:24px;padding:24px 28px;border-radius:18px;color:#f7f1e8;background:linear-gradient(120deg,#173638,#536354);box-shadow:0 14px 35px rgba(19,43,43,.18)}.hero h1{margin:5px 0;font:700 30px Georgia,'STSong',serif;letter-spacing:2px}.hero p{color:rgba(247,241,232,.72)}.eyebrow{font-size:10px;letter-spacing:2.5px;color:#d6b691}.guardrails{display:flex;flex-direction:column;text-align:right;font-size:12px}.guardrails span{margin-top:5px;color:rgba(247,241,232,.65)}.workspace-tabs{margin-top:12px}.workspace-tabs :deep(.el-tabs__item){color:rgba(245,240,232,.78);font-weight:650}.workspace-tabs :deep(.el-tabs__item:hover),.workspace-tabs :deep(.el-tabs__item.is-active){color:#d8a264}.workspace-tabs :deep(.el-tabs__nav-wrap::after){background:rgba(245,240,232,.38)}.deck-grid,.access-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,.72fr);gap:14px}.section-label{margin:18px 0 8px;font-size:12px;font-weight:700;color:#756958;letter-spacing:.5px}.chapter-tree{max-height:280px;overflow:auto;padding:8px;border-radius:8px;background:var(--el-fill-color-extra-light)}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}.generate{width:100%;margin-top:24px}.history{margin-top:14px}.card-head,.candidate,.provider-list div{display:flex;align-items:center;justify-content:space-between;gap:12px}.candidate-list{margin-top:16px}.candidate{padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}.candidate div{min-width:0}.candidate small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--el-text-color-secondary);max-width:680px}.boundary{margin-top:18px}.provider-list{margin-top:22px;padding-top:14px;border-top:1px solid var(--el-border-color-lighter)}.provider-list div{padding:7px 0;color:var(--el-text-color-regular)}@media(max-width:900px){.hero{align-items:flex-start;flex-direction:column}.guardrails{text-align:left}.deck-grid,.access-grid{grid-template-columns:1fr}.two-col{grid-template-columns:1fr}}
.deck-steps{display:grid;grid-template-columns:minmax(0,1fr) 250px;align-items:center;gap:20px;margin-bottom:14px;padding:16px 18px;border:1px solid rgba(139,90,43,.15);border-radius:14px;background:#f7f2e9}.scope-summary{display:flex;flex-direction:column;padding-left:18px;border-left:1px solid #ded3c3}.scope-summary b{font-size:11px;color:#8b5a2b}.scope-summary span{margin-top:5px;font-size:12px;color:#6d6559}@media(max-width:760px){.deck-steps{grid-template-columns:1fr}.scope-summary{padding:10px 0 0;border-left:0;border-top:1px solid #ded3c3}}
</style>
