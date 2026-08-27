<template>
  <div class="knowledge-inbox">
    <KnowledgeScopeSelector v-if="!embedded" />
    <div v-if="!knowledgeBookIds.length" class="scope-required"><b>先选择本次整理的书目</b><span>知识记录只在界面聚合，原始标注、笔记和证据卡片仍分别保存。</span></div>
    <template v-else>
      <header class="inbox-head">
        <div><b>笔记与证据</b><span>阅读标注 → 知识笔记 → 证据与主张，每条记录均可回到原文</span></div>
        <el-input v-model="filters.q" clearable placeholder="搜索原文、笔记或主张" @keyup.enter="loadRecords" />
        <el-button type="primary" @click="openNewNote">新建知识笔记</el-button>
      </header>
      <section class="filter-row">
        <el-checkbox-group v-model="filters.record_types" @change="loadRecords">
          <el-checkbox-button value="highlight">高亮</el-checkbox-button><el-checkbox-button value="underline">划线</el-checkbox-button>
          <el-checkbox-button value="annotation">批注</el-checkbox-button><el-checkbox-button value="note">笔记</el-checkbox-button>
          <el-checkbox-button value="evidence">证据卡片</el-checkbox-button>
        </el-checkbox-group>
        <el-select v-model="filters.chapter_id" clearable filterable placeholder="全部章节" @change="loadRecords"><el-option v-for="chapter in chapters" :key="chapter.id" :label="chapter.label" :value="chapter.id" /></el-select>
        <el-select v-model="filters.color" clearable placeholder="全部颜色" @change="loadRecords"><el-option v-for="color in COLORS" :key="color.value" :label="color.label" :value="color.value"><span class="color-option"><i :style="{background:color.value}" />{{ color.label }}</span></el-option></el-select>
        <el-input v-model="filters.tag" clearable placeholder="标签" @keyup.enter="loadRecords" />
        <el-date-picker v-model="filters.dates" type="daterange" start-placeholder="开始时间" end-placeholder="结束时间" @change="loadRecords" />
        <el-button @click="loadRecords">筛选</el-button>
      </section>
      <div class="bulk-bar" v-if="selectedEvidence.length"><span>已选 {{ selectedEvidence.length }} 条证据</span><el-button type="primary" plain @click="combineClaim">组合为主张</el-button></div>
      <main class="record-grid" v-loading="loading">
        <article v-for="record in records" :key="record.key" class="record-card" :class="'type-' + record.record_type">
          <div class="record-top"><el-checkbox v-if="record.record_type==='evidence'" :model-value="selectedKeys.includes(record.key)" @change="toggleEvidence(record)" /><span class="kind">{{ typeLabel(record.record_type) }}</span><span class="origin" :class="record.origin">{{ record.origin==='ai'?'AI 生成':'用户创建' }}</span><time>{{ formatTime(record.created_at) }}</time></div>
          <h3><button v-if="record.record_type==='note'" class="record-open" type="button" @click="openNote(record)">{{ record.title }}</button><template v-else>{{ record.title }}</template></h3><p v-if="record.content">{{ contentPreview(record.content) }}</p><blockquote v-if="record.quote">{{ record.quote }}</blockquote>
          <div class="record-source"><button @click="goSource(record)">《{{ record.book_title }}》<template v-if="record.chapter_title"> · {{ record.chapter_title }}</template><template v-if="record.page"> · 第 {{ record.page }} 页</template> ↗</button></div>
          <div class="tag-row" v-if="record.tags?.length"><span v-for="tag in record.tags" :key="tag"># {{ tag }}</span></div>
          <div class="verification" v-if="record.record_type==='evidence'">主张审计：<b :class="record.verification_status">{{ statusLabel(record.verification_status) }}</b></div>
          <footer><el-button v-if="record.record_type==='note'" text type="primary" @click="openNote(record)">打开阅读</el-button><el-button v-if="['highlight','underline','annotation'].includes(record.record_type)" text type="primary" @click="promote(record)">提升为笔记</el-button><el-button v-if="['highlight','underline','annotation'].includes(record.record_type)" text @click="makeEvidence(record)">制成证据卡</el-button><el-button v-if="record.record_type==='note'" text @click="addToTree(record)">加入知识树</el-button><el-button v-if="['note','evidence'].includes(record.record_type)" text @click="editRecord(record)">编辑{{ record.record_type==='evidence'?' / 核验':'' }}</el-button></footer>
        </article>
        <el-empty v-if="!loading&&!records.length" description="当前筛选条件下没有知识记录" />
      </main>
    </template>
    <el-dialog v-model="editor.visible" :title="editor.mode==='note'?'知识笔记':'证据卡片'" width="min(600px,94vw)">
      <el-form label-position="top"><el-form-item label="来源书目"><el-select v-model="editor.book_id" :disabled="!!editor.id" style="width:100%"><el-option v-for="book in scopedBooks" :key="book.id" :label="book.title" :value="book.id" /></el-select></el-form-item><el-form-item label="标题"><el-input v-model="editor.title" maxlength="255" /></el-form-item><el-form-item v-if="editor.mode==='note'" label="我的理解"><el-input v-model="editor.content" type="textarea" :rows="7" /></el-form-item><template v-else><el-form-item label="原文证据"><el-input v-model="editor.evidence_text" type="textarea" :rows="5" /></el-form-item><el-form-item label="支持的主张"><el-input v-model="editor.claim_text" type="textarea" :rows="3" /></el-form-item><el-form-item label="一致性状态"><el-select v-model="editor.verification_status"><el-option label="待核验" value="needs_review"/><el-option label="支持" value="supported"/><el-option label="部分支持" value="partial"/><el-option label="不支持" value="unsupported"/></el-select></el-form-item></template><el-form-item label="标签（逗号分隔）"><el-input v-model="editor.tagsText" /></el-form-item></el-form>
      <template #footer><el-button @click="editor.visible=false">取消</el-button><el-button type="primary" @click="saveEditor">保存</el-button></template>
    </el-dialog>
    <el-drawer v-model="reader.visible" :title="reader.note?.title || '知识笔记'" size="min(760px,94vw)" destroy-on-close>
      <div class="note-reader" v-loading="reader.loading">
        <template v-if="reader.note">
          <div class="note-reader-meta">
            <span class="origin" :class="reader.note.origin">{{ reader.note.origin==='ai'?'AI 生成':'用户创建' }}</span>
            <span>《{{ reader.note.book_title }}》</span>
            <span v-if="reader.note.chapter_title">{{ reader.note.chapter_title }}</span>
            <time>{{ formatTime(reader.note.created_at) }}</time>
          </div>
          <div class="tag-row" v-if="reader.note.tags?.length"><span v-for="tag in reader.note.tags" :key="tag"># {{ tag }}</span></div>
          <article class="markdown-body note-reader-content" v-html="renderMarkdown(reader.note.content)" />
        </template>
      </div>
      <template #footer>
        <div class="note-reader-actions">
          <el-button v-if="reader.note?.source_link" @click="goSource(reader.note)">回到来源</el-button>
          <el-button v-if="reader.note" @click="editFromReader">编辑笔记</el-button>
          <el-button type="primary" @click="reader.visible=false">完成阅读</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
import { knowledgeBooks, knowledgeBookIds, loadKnowledgeBooks } from '../stores/knowledgeScope'
import { renderMarkdown } from '../utils/markdown'
import { listKnowledgeRecords, promoteAnnotation, createKnowledgeNote, getKnowledgeNote, updateKnowledgeNote, addKnowledgeNoteToTree, createEvidenceCard, updateEvidenceCard, getBook } from '../api'
defineProps({ embedded: { type: Boolean, default: false } })
const router=useRouter(), loading=ref(false), records=ref([]), chapters=ref([]), selectedKeys=ref([])
const COLORS=[{label:'黄色',value:'#f9e572'},{label:'绿色',value:'#9be5a0'},{label:'蓝色',value:'#8ec8f5'},{label:'粉色',value:'#f5b8c8'}]
const filters=reactive({q:'',record_types:['highlight','underline','annotation','note','evidence'],chapter_id:null,color:null,tag:'',dates:null})
const editor=reactive({visible:false,mode:'note',id:null,book_id:null,chapter_id:null,page:null,title:'',content:'',evidence_text:'',claim_text:'',verification_status:'needs_review',tagsText:'',origin:'user'})
const reader=reactive({visible:false,loading:false,note:null})
const scopedBooks=computed(()=>knowledgeBooks.value.filter(book=>knowledgeBookIds.value.includes(book.id))), selectedEvidence=computed(()=>records.value.filter(r=>selectedKeys.value.includes(r.key)))
const typeLabel=t=>({highlight:'高亮',underline:'划线',annotation:'批注',note:'知识笔记',evidence:'证据卡片'}[t]||t), statusLabel=s=>({supported:'支持',partial:'部分支持',needs_review:'待核验',unsupported:'不支持'}[s]||'待核验'), formatTime=value=>value?new Date(value).toLocaleDateString('zh-CN'):''
const contentPreview=value=>String(value||'').replace(/```[\s\S]*?```/g,' [代码或材料块] ').replace(/[#>*_`~\[\]()!-]+/g,' ').replace(/\s+/g,' ').trim()
const loadChapters=async()=>{const out=[];for(const book of scopedBooks.value){try{const detail=await getBook(book.id);const walk=(nodes,d=0)=>nodes.forEach(n=>{out.push({id:n.id,label:`《${book.title}》 ${'　'.repeat(d)}${n.title}`});walk(n.children||[],d+1)});walk(detail.chapters||[])}catch{}}chapters.value=out}
const loadRecords=async()=>{loading.value=true;try{const params={book_ids:knowledgeBookIds.value,record_types:filters.record_types,q:filters.q||undefined,chapter_id:filters.chapter_id||undefined,color:filters.color||undefined,tag:filters.tag||undefined};if(filters.dates?.length){params.date_from=filters.dates[0].toISOString();const end=new Date(filters.dates[1]);end.setHours(23,59,59,999);params.date_to=end.toISOString()}const result=await listKnowledgeRecords(params);records.value=result.items||[];selectedKeys.value=selectedKeys.value.filter(key=>records.value.some(r=>r.key===key))}catch(e){ElMessage.error(`无法加载知识记录：${e.message}`)}finally{loading.value=false}}
const goSource=record=>record.source_link&&router.push(record.source_link), promote=async record=>{try{await promoteAnnotation(record.id);await loadRecords();ElMessage.success('已提升为独立知识笔记，原标注仍保留')}catch(e){ElMessage.error(e.message)}}, addToTree=async record=>{try{await addKnowledgeNoteToTree(record.id);ElMessage.success('已创建知识树节点；原笔记仍独立保留')}catch(e){ElMessage.error(e.message)}}
const resetEditor=()=>Object.assign(editor,{visible:true,mode:'note',id:null,book_id:knowledgeBookIds.value.length===1?knowledgeBookIds.value[0]:null,chapter_id:null,page:null,title:'',content:'',evidence_text:'',claim_text:'',verification_status:'needs_review',tagsText:'',origin:'user'}), openNewNote=()=>resetEditor()
const makeEvidence=record=>{resetEditor();Object.assign(editor,{mode:'evidence',book_id:record.book_id,chapter_id:record.chapter_id,page:record.page,title:(record.title||'原文证据').slice(0,80),evidence_text:record.quote||record.content,origin:'user'})}
const openNote=async record=>{reader.visible=true;reader.loading=true;reader.note=null;try{reader.note=await getKnowledgeNote(record.id)}catch(e){reader.visible=false;ElMessage.error(`无法打开笔记：${e.message}`)}finally{reader.loading=false}}
const editRecord=async record=>{let full=record;if(record.record_type==='note'){try{full=await getKnowledgeNote(record.id)}catch(e){return ElMessage.error(`无法读取完整笔记：${e.message}`)}}resetEditor();Object.assign(editor,{mode:full.record_type==='evidence'?'evidence':'note',id:full.id,book_id:full.book_id,chapter_id:full.chapter_id,page:full.page,title:full.title,content:full.content||'',evidence_text:full.quote||'',claim_text:full.content||'',verification_status:full.verification_status||'needs_review',tagsText:(full.tags||[]).join(','),origin:full.origin||'user'})}
const editFromReader=async()=>{if(!reader.note)return;const note=reader.note;reader.visible=false;await editRecord({...note,record_type:'note'})}
const tags=()=>editor.tagsText.split(/[,，]/).map(x=>x.trim()).filter(Boolean).slice(0,20)
const saveEditor=async()=>{if(!editor.book_id||!editor.title.trim())return ElMessage.warning('请选择来源并填写标题');try{if(editor.mode==='note'){const data={book_id:editor.book_id,chapter_id:editor.chapter_id,page:editor.page,title:editor.title.trim(),content:editor.content,tags:tags(),origin:editor.origin};editor.id?await updateKnowledgeNote(editor.id,data):await createKnowledgeNote(data)}else{if(!editor.evidence_text.trim())return ElMessage.warning('证据原文不能为空');const data={book_id:editor.book_id,chapter_id:editor.chapter_id,page:editor.page,title:editor.title.trim(),evidence_text:editor.evidence_text,claim_text:editor.claim_text,tags:tags(),origin:editor.origin,verification_status:editor.verification_status};editor.id?await updateEvidenceCard(editor.id,data):await createEvidenceCard(data)}editor.visible=false;await loadRecords();ElMessage.success('已保存')}catch(e){ElMessage.error(e.message)}}
const toggleEvidence=record=>{selectedKeys.value=selectedKeys.value.includes(record.key)?selectedKeys.value.filter(x=>x!==record.key):[...selectedKeys.value,record.key]}
const combineClaim=async()=>{const books=[...new Set(selectedEvidence.value.map(x=>x.book_id))];if(books.length!==1)return ElMessage.warning('组合主张时请先选择同一本书的证据，避免来源归属含混');try{const {value}=await ElMessageBox.prompt('写出这些证据共同支持的可核验主张','组合为主张',{inputType:'textarea',confirmButtonText:'生成待核验主张'});const source=selectedEvidence.value[0];await createEvidenceCard({book_id:source.book_id,title:value.trim().slice(0,80),evidence_text:selectedEvidence.value.map((x,i)=>`[证据 ${i+1}] ${x.quote}`).join('\n\n').slice(0,12000),claim_text:value.trim(),tags:[],origin:'user',verification_status:'needs_review'});selectedKeys.value=[];await loadRecords();ElMessage.success('主张已创建并标记为待核验')}catch{}}
watch(knowledgeBookIds,async()=>{filters.chapter_id=null;await loadChapters();await loadRecords()},{deep:true});onMounted(async()=>{await loadKnowledgeBooks();await loadChapters();await loadRecords()})
</script>

<style scoped>
.knowledge-inbox{max-width:1500px}.scope-required{display:flex;flex-direction:column;gap:7px;padding:40px;border:1px dashed #d8c7b0;border-radius:12px;background:#f7f2e9;text-align:center}.scope-required span{color:var(--el-text-color-secondary)}.inbox-head{display:grid;grid-template-columns:minmax(320px,1fr) minmax(260px,420px) auto;align-items:center;gap:12px;padding:14px 16px;border:1px solid var(--study-card-border);border-radius:12px;background:#fffdf8;box-shadow:var(--study-shadow-sm)}.inbox-head>div{display:flex;flex-direction:column}.inbox-head b{font-size:18px}.inbox-head span{margin-top:4px;color:var(--el-text-color-secondary);font-size:12px}.filter-row{display:flex;align-items:center;gap:8px;margin:10px 0;padding:10px;border:1px solid var(--study-card-border);border-radius:10px;background:#f8f3ea;box-shadow:var(--study-shadow-sm);overflow-x:auto}.filter-row>.el-select{width:180px;flex:none}.filter-row>.el-input{width:130px;flex:none}.filter-row :deep(.el-date-editor){width:240px;flex:none}.color-option{display:flex;align-items:center;gap:7px}.color-option i{width:10px;height:10px;border-radius:50%}.bulk-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;padding:9px 12px;border:1px solid #d9c4a8;border-radius:9px;background:#fff8ec}.record-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.record-card{display:flex;min-height:230px;flex-direction:column;padding:15px;border:1px solid #e5e7eb;border-radius:11px;background:#fffdf9;box-shadow:0 1px 2px rgba(15,23,42,.06);transition:transform .16s ease,box-shadow .16s ease}.record-card:hover{transform:translateY(-1px);box-shadow:0 5px 16px rgba(70,52,33,.09)}.record-top{display:flex;align-items:center;gap:7px;color:#8b8174;font-size:12px}.record-top time{margin-left:auto}.kind{color:#8b572a;font-weight:700}.origin{padding:2px 6px;border-radius:9px;background:#ece8e0}.origin.ai{background:#e7eef7;color:#476884}.record-card h3{margin:12px 0 7px;font-size:16px;line-height:1.45}.record-open{padding:0;border:0;background:transparent;color:inherit;font:inherit;font-weight:inherit;line-height:inherit;text-align:left;cursor:pointer}.record-open:hover{color:var(--el-color-primary);text-decoration:underline;text-underline-offset:3px}.record-card p{display:-webkit-box;overflow:hidden;margin:0 0 8px;color:#4f493f;line-height:1.65;-webkit-line-clamp:3;-webkit-box-orient:vertical}.record-card blockquote{display:-webkit-box;overflow:hidden;margin:4px 0 10px;padding:9px 11px;border-left:3px solid #d4a565;background:#f6f0e6;color:#6e6559;font-size:13px;line-height:1.65;-webkit-line-clamp:4;-webkit-box-orient:vertical}.record-source button{padding:0;border:0;background:transparent;color:#97602f;cursor:pointer;text-align:left}.tag-row{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.tag-row span{color:#7d6e5e;font-size:12px}.verification{margin-top:8px;color:#746a5e;font-size:12px}.verification b.needs_review{color:#b66a22}.verification b.supported{color:#347455}.verification b.partial{color:#8c6b1f}.verification b.unsupported{color:#ad4242}.record-card footer{display:flex;flex-wrap:wrap;margin-top:auto;padding-top:12px;border-top:1px solid #eee6da}.record-card footer .el-button{margin-left:0}.note-reader{min-height:240px}.note-reader-meta{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding-bottom:12px;border-bottom:1px solid var(--study-card-border);color:var(--study-text-secondary);font-size:12px}.note-reader-meta time{margin-left:auto}.note-reader-content{padding:20px 6px;color:var(--study-text-primary);font-family:var(--study-font-reading);font-size:16px;line-height:1.85}.note-reader-content :deep(h1){font-size:24px}.note-reader-content :deep(h2){margin-top:1.6em;font-size:20px}.note-reader-content :deep(h3){margin-top:1.4em;font-size:17px}.note-reader-content :deep(p),.note-reader-content :deep(li){line-height:1.85}.note-reader-content :deep(blockquote){margin:16px 0;padding:10px 14px;border-left:3px solid var(--el-color-primary-light-3);background:var(--study-surface-muted)}.note-reader-actions{display:flex;justify-content:flex-end;gap:8px}@media(max-width:1100px){.record-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:720px){.inbox-head{grid-template-columns:1fr}.record-grid{grid-template-columns:1fr}.note-reader-content{padding-inline:0;font-size:15px}.note-reader-meta time{width:100%;margin-left:0}}
</style>
