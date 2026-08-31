<template>
  <main class="health-page study-page" aria-labelledby="health-title">
    <header class="health-hero">
      <div><span>KNOWLEDGE BASE QUALITY</span><h1 id="health-title">知识库健康检查</h1><p>先修复会破坏检索、引用和输出可信度的问题，再继续扩充资料。</p></div>
      <div class="score" :class="scoreTone"><strong>{{ health.score }}</strong><span>健康分</span></div>
    </header>

    <section class="health-overview" aria-label="检查概况">
      <div><strong>{{ health.book_count }}</strong><span>全部资料</span></div>
      <div><strong>{{ health.healthy_book_count }}</strong><span>未发现问题</span></div>
      <div><strong>{{ health.issue_count }}</strong><span>待处理项</span></div>
      <button :disabled="loading" @click="loadHealth"><span>{{ loading ? '检查中…' : '重新检查' }}</span><small>{{ checkedAt }}</small></button>
    </section>

    <nav class="health-filters" aria-label="健康问题筛选">
      <button :class="{active:activeType==='all'}" @click="activeType='all'"><span>全部问题</span><b>{{ health.issue_count }}</b></button>
      <button v-for="item in auditTypes" :key="item.key" :class="[{active:activeType===item.key},categoryFor(item).severity]" @click="activeType=item.key"><span>{{ item.label }}</span><b>{{ categoryFor(item).count }}</b></button>
    </nav>

    <section class="issue-ledger" v-loading="loading" aria-live="polite">
      <header><div><h2>{{ activeLabel }}</h2><p>每一项都给出本地检查依据和可执行的恢复路径。</p></div><div class="ledger-actions"><el-checkbox :model-value="allVisibleSelected" :indeterminate="someVisibleSelected" @change="toggleVisible">选择当前</el-checkbox><el-button :disabled="!selectedIds.length" :loading="batchRepairing" type="primary" plain @click="repairSelected">处理所选 {{ selectedIds.length || '' }}</el-button><span>{{ filteredItems.length }} 项</span></div></header>
      <article v-for="item in filteredItems" :key="item.id" class="issue-row">
        <div class="severity" :class="item.severity"><el-checkbox v-model="selectedIds" :value="item.id" :aria-label="`选择 ${item.book_title} 的${item.label}`" /><i></i><span>{{ item.label }}</span></div>
        <div class="issue-copy"><h3>{{ item.book_title }}</h3><p>{{ item.reason }}</p><small>{{ evidenceText(item.evidence) }}</small><button v-if="item.evidence?.objects?.length" class="object-link" @click="showObjects(item)">查看 {{ item.evidence.objects.length }} 个失效对象</button></div>
        <div class="issue-action">
          <el-button v-if="['unparsed','low_quality_ocr','broken_anchor'].includes(item.type)" :loading="repairingId===item.id" @click="repairOne(item)">{{ item.action_label }}</el-button>
          <el-button v-else @click="openAction(item)">{{ item.action_label }}</el-button>
        </div>
      </article>
      <StudyEmptyState v-if="!loading&&!filteredItems.length" title="当前范围没有这类问题" description="健康检查不会自动删除重复资料，也不会擅自重写目录或锚点。" />
    </section>
    <p class="health-boundary">{{ health.boundary }}</p>
    <el-dialog v-model="objectDialog" :title="objectIssue?.book_title ? `失效对象｜${objectIssue.book_title}` : '失效对象'" width="min(720px,94vw)" append-to-body>
      <div class="broken-object" v-for="object in objectIssue?.evidence?.objects || []" :key="`${object.type}:${object.id}:${object.ref || ''}`"><div><b>{{ object.title || object.type }}</b><small>{{ object.reason }}<template v-if="object.ref"> · {{ object.ref }}</template></small></div><el-button size="small" @click="router.push(object.action_path);objectDialog=false">打开核对</el-button></div>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getKnowledgeBaseHealth, repairKnowledgeBaseHealth } from '../api'
import { notifyTaskSubmitted } from '../stores/taskCenter'
import StudyEmptyState from '../components/StudyEmptyState.vue'

const router=useRouter(),loading=ref(false),repairingId=ref(''),activeType=ref('all'),selectedIds=ref([]),batchRepairing=ref(false),objectDialog=ref(false),objectIssue=ref(null)
const auditTypes=[
  {key:'unparsed',label:'未解析',severity:'danger'},
  {key:'low_quality_ocr',label:'低质量 OCR',severity:'warning'},
  {key:'missing_toc',label:'无目录',severity:'warning'},
  {key:'missing_metadata',label:'无元数据',severity:'info'},
  {key:'duplicate',label:'重复资料',severity:'warning'},
  {key:'broken_anchor',label:'失效锚点',severity:'danger'},
]
const health=ref({score:100,book_count:0,healthy_book_count:0,issue_count:0,categories:{},items:[],boundary:'',checked_at:null})
const categoryFor=item=>health.value.categories?.[item.key]||{count:0,severity:item.severity}
const filteredItems=computed(()=>activeType.value==='all'?health.value.items:health.value.items.filter(item=>item.type===activeType.value))
const activeLabel=computed(()=>activeType.value==='all'?'全部待处理项':health.value.categories?.[activeType.value]?.label||'问题')
const scoreTone=computed(()=>health.value.score>=90?'good':health.value.score>=70?'watch':'risk')
const checkedAt=computed(()=>health.value.checked_at?`上次 ${new Date(health.value.checked_at).toLocaleString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`:'尚未检查')
const allVisibleSelected=computed(()=>filteredItems.value.length>0&&filteredItems.value.every(item=>selectedIds.value.includes(item.id)))
const someVisibleSelected=computed(()=>!allVisibleSelected.value&&filteredItems.value.some(item=>selectedIds.value.includes(item.id)))
const loadHealth=async()=>{loading.value=true;try{health.value=await getKnowledgeBaseHealth();selectedIds.value=selectedIds.value.filter(id=>health.value.items.some(item=>item.id===id))}catch(error){ElMessage.error(`健康检查失败：${error.message}`)}finally{loading.value=false}}
const evidenceText=evidence=>{if(!evidence||!Object.keys(evidence).length)return '本地结构与档案字段检查';const labels={status:'状态',chunk_count:'文本块',average_chars_per_page:'每页平均字符',replacement_ratio:'乱码比例',chapter_count:'目录项',duplicate_of:'疑似重复于',count:'失效数量'};return Object.entries(evidence).filter(([key,value])=>value!==null&&key!=='file_hash'&&key!=='objects').map(([key,value])=>`${labels[key]||key}：${value}`).join(' · ')}
const toggleVisible=value=>{const visible=new Set(filteredItems.value.map(item=>item.id));selectedIds.value=value?[...new Set([...selectedIds.value,...visible])]:selectedIds.value.filter(id=>!visible.has(id))}
const runRepair=async ids=>{const result=await repairKnowledgeBaseHealth(ids);if(result.submitted)notifyTaskSubmitted();const manual=(result.results||[]).filter(item=>item.status==='manual_required').length;ElMessage.success(`已提交 ${result.submitted||0} 项，自动修复 ${result.repaired||0} 项${manual?`，${manual} 项需人工确认`:''}`);await loadHealth();return result}
const repairOne=async item=>{repairingId.value=item.id;try{await runRepair([item.id])}catch(error){ElMessage.error(error.message)}finally{repairingId.value=''}}
const repairSelected=async()=>{batchRepairing.value=true;try{await runRepair(selectedIds.value)}catch(error){ElMessage.error(error.message)}finally{batchRepairing.value=false}}
const showObjects=item=>{objectIssue.value=item;objectDialog.value=true}
const openAction=item=>router.push(item.action_path)
onMounted(loadHealth)
</script>

<style scoped>
.health-page{box-sizing:border-box;width:100%;min-height:calc(100dvh - 76px);padding:22px 24px 32px;border:1px solid var(--study-card-border);border-radius:14px;background:#fbf8f1;box-shadow:var(--study-shadow-sm)}
.health-hero>div:first-child{min-width:0}
@media(max-width:760px){.score{display:none!important}.health-page{width:calc(100vw - 16px);max-width:calc(100vw - 16px);padding-inline:16px}}
.health-page{max-width:1320px}.health-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:32px;padding:28px 4px 22px;border-bottom:1px solid var(--study-card-border)}.health-hero>div:first-child{max-width:720px}.health-hero span{color:#8f7559;font-size:11px;letter-spacing:1.6px}.health-hero h1{margin:7px 0 8px;font-family:var(--study-font-display);font-size:34px;font-weight:600;line-height:1.2}.health-hero p{color:var(--study-text-secondary);font-size:15px;line-height:1.7}.score{display:flex;width:112px;height:112px;flex:none;align-items:center;justify-content:center;flex-direction:column;border:1px solid var(--study-card-border);border-radius:50%;background:var(--study-surface-paper)}.score strong{font:600 38px var(--study-font-latin);font-variant-numeric:tabular-nums}.score.good strong{color:#3f7157}.score.watch strong{color:#9a681e}.score.risk strong{color:#a7473f}.score span{margin-top:2px;font-size:11px;letter-spacing:.4px}.health-overview{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr)) minmax(190px,1.25fr);margin:18px 0;border-block:1px solid var(--study-card-border)}.health-overview>div,.health-overview>button{display:flex;min-height:82px;align-items:flex-start;justify-content:center;flex-direction:column;padding:14px 20px;border:0;border-right:1px solid var(--study-card-border);background:transparent;text-align:left}.health-overview strong{font:600 25px var(--study-font-latin);font-variant-numeric:tabular-nums}.health-overview span{margin-top:4px;color:var(--study-text-secondary);font-size:12px}.health-overview button{border-right:0;cursor:pointer}.health-overview button:hover{background:var(--study-surface-muted)}.health-overview button small{margin-top:5px;color:var(--study-text-muted)}.health-filters{display:flex;gap:6px;margin-bottom:12px;overflow-x:auto}.health-filters button{display:flex;min-width:max-content;align-items:center;gap:10px;padding:8px 11px;border:1px solid transparent;border-radius:7px;background:transparent;color:var(--study-text-secondary);cursor:pointer}.health-filters button:hover,.health-filters button.active{border-color:var(--study-card-border);background:var(--study-surface-paper);color:var(--study-text-primary)}.health-filters b{font-variant-numeric:tabular-nums}.health-filters .danger b{color:#a7473f}.health-filters .warning b{color:#99671d}.issue-ledger{min-height:320px;border-top:2px solid #625548}.issue-ledger>header{display:flex;align-items:flex-start;justify-content:space-between;padding:17px 4px 13px}.ledger-actions{display:flex;align-items:center;gap:10px}.issue-ledger h2{font-size:18px}.issue-ledger header p{margin-top:4px;color:var(--study-text-secondary);font-size:12px}.ledger-actions>span{font-variant-numeric:tabular-nums;color:var(--study-text-secondary)}.issue-row{display:grid;grid-template-columns:175px minmax(0,1fr) 190px;align-items:center;gap:18px;min-height:104px;padding:15px 4px;border-top:1px solid var(--study-card-border)}.severity{display:flex;align-items:center;gap:8px;color:var(--study-text-secondary);font-size:12px}.severity i{width:8px;height:8px;border-radius:50%;background:#8a8175}.severity.danger i{background:#ad4d45}.severity.warning i{background:#b47a26}.severity.info i{background:#5d7485}.issue-copy{min-width:0}.issue-copy h3{overflow-wrap:anywhere;font-family:var(--study-font-reading);font-size:16px;line-height:1.45}.issue-copy p{margin:5px 0;color:var(--study-text-secondary);font-size:13px;line-height:1.6}.issue-copy small{color:var(--study-text-muted);font-size:11px}.object-link{display:block;margin-top:7px;padding:0;border:0;background:transparent;color:var(--el-color-primary);cursor:pointer;font-size:11px}.issue-action{text-align:right}.health-boundary{margin:16px 0;color:var(--study-text-muted);font-size:11px;line-height:1.6}.broken-object{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:11px 0;border-top:1px solid var(--study-card-border)}.broken-object>div{display:flex;min-width:0;flex-direction:column}.broken-object small{margin-top:4px;color:var(--study-text-secondary);overflow-wrap:anywhere}@media(max-width:760px){.health-hero{align-items:flex-start}.score{width:82px;height:82px}.score strong{font-size:28px}.health-overview{grid-template-columns:1fr 1fr}.health-overview>div,.health-overview>button{border-bottom:1px solid var(--study-card-border)}.issue-ledger>header{gap:10px;flex-direction:column}.issue-row{grid-template-columns:1fr;gap:8px}.issue-action{text-align:left}.health-hero h1{font-size:27px}}@media(max-width:480px){.health-hero{flex-direction:column-reverse}.health-overview{grid-template-columns:1fr}.health-overview>div,.health-overview>button{border-right:0}}
</style>
