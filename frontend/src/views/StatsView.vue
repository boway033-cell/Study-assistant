<template>
  <main class="insights-page study-page" aria-labelledby="insights-title">
    <header class="insights-head">
      <div><span>KNOWLEDGE BASE INSIGHTS</span><h1 id="insights-title">知识库洞察</h1><p>观察资料是否真正成为可检索、可取证、可复用且能回到原文的个人知识。</p></div>
      <div class="head-actions"><el-select v-model="days" aria-label="活动统计范围" @change="loadInsights"><el-option label="近 30 天" :value="30"/><el-option label="近 90 天" :value="90"/><el-option label="近一年" :value="365"/></el-select><el-button :loading="loading" @click="loadInsights">刷新洞察</el-button></div>
    </header>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false"><el-button text @click="loadInsights">重新加载</el-button></el-alert>

    <section class="metric-strip" v-loading="loading" aria-label="知识库核心指标">
      <button @click="router.push('/knowledge-health')"><span>健康分</span><strong :class="healthTone">{{ data.overview.health_score }}</strong><small>{{ data.health.issue_count }} 个待处理项</small></button>
      <button @click="router.push('/library')"><span>解析就绪</span><strong>{{ data.overview.ready_count }}<i>/{{ data.overview.book_count }}</i></strong><small>可进入检索与研究链</small></button>
      <button @click="router.push('/knowledge-hub?view=notes')"><span>知识对象</span><strong>{{ data.overview.knowledge_object_count }}</strong><small>标注、笔记、证据与节点</small></button>
      <button @click="router.push('/knowledge-hub?view=notes')"><span>来源可追溯率</span><strong>{{ percent(data.overview.traceable_rate) }}</strong><small>笔记与证据卡来源链</small></button>
      <button @click="router.push('/knowledge-hub?view=study')"><span>研究报告</span><strong>{{ data.overview.report_count }}</strong><small>跨文献分析沉淀</small></button>
    </section>

    <div class="insights-grid">
      <section class="paper-panel coverage-panel">
        <header><div><h2>资料到知识的覆盖</h2><p>不是统计“拥有多少文件”，而是检查资料走到了知识链的哪一步。</p></div><span>{{ data.overview.book_count }} 本资料</span></header>
        <button v-for="item in data.coverage" :key="item.key" class="coverage-row" @click="router.push(item.path)">
          <span>{{ item.label }}</span><div class="coverage-track"><i :style="{width:percent(item.rate)}" /></div><b>{{ item.value }}/{{ item.total }}</b><em>{{ percent(item.rate) }}</em>
        </button>
        <StudyEmptyState v-if="!loading&&!data.coverage.length" compact title="还没有资料覆盖数据" description="导入第一份文献后，这里会显示解析、目录、元数据与知识沉淀覆盖。" />
      </section>

      <section class="paper-panel health-panel">
        <header><div><h2>优先治理</h2><p>先处理会影响检索和引用可信度的问题。</p></div><el-button text @click="router.push('/knowledge-health')">完整检查</el-button></header>
        <div v-if="data.health.categories.length" class="health-categories"><button v-for="item in data.health.categories" :key="item.key" :class="item.severity" @click="router.push('/knowledge-health')"><span>{{ item.label }}</span><b>{{ item.count }}</b></button></div>
        <article v-for="item in data.health.items.slice(0,4)" :key="item.id" class="attention-row" @click="router.push(item.action_path)"><i :class="item.severity"/><div><b>{{ item.book_title }}</b><span>{{ item.reason }}</span></div></article>
        <StudyEmptyState v-if="!loading&&!data.health.issue_count" compact title="当前未发现结构性问题" description="健康检查仍是启发式审计；重要引用与 OCR 结果需要人工抽查。" />
      </section>
    </div>

    <section class="paper-panel activity-panel">
      <header><div><h2>知识活动 · 近 {{ days }} 天</h2><p>区分导入、原文取证、知识沉淀和研究输出，不以在线时长或连续打卡评价质量。</p></div><div class="legend"><span class="imports">导入</span><span class="evidence">取证</span><span class="knowledge">沉淀</span><span class="outputs">输出</span></div></header>
      <div ref="activityChart" class="activity-chart" role="img" aria-label="知识库活动趋势图" />
    </section>

    <div class="insights-grid lower-grid">
      <section class="paper-panel composition-panel">
        <header><div><h2>知识对象构成</h2><p>从原文痕迹到可审查主张，保持底层对象彼此独立。</p></div><span>{{ data.knowledge.traceable_objects }} 条带来源</span></header>
        <div class="object-grid"><div><strong>{{ data.knowledge.annotations }}</strong><span>原文标注</span></div><div><strong>{{ data.knowledge.notes }}</strong><span>知识笔记</span></div><div><strong>{{ data.knowledge.evidence_cards }}</strong><span>证据卡</span></div><div><strong>{{ data.knowledge.tree_nodes }}</strong><span>知识节点</span></div></div>
        <div class="audit-ledger"><span><i class="supported"/>支持 <b>{{ data.knowledge.evidence_audit.supported }}</b></span><span><i class="partial"/>部分支持 <b>{{ data.knowledge.evidence_audit.partial }}</b></span><span><i class="review"/>待核验 <b>{{ data.knowledge.evidence_audit.needs_review }}</b></span><span><i class="unsupported"/>不支持 <b>{{ data.knowledge.evidence_audit.unsupported }}</b></span></div>
      </section>

      <section class="paper-panel output-panel">
        <header><div><h2>研究与输出</h2><p>产出数量只说明工作流是否贯通，不代表内容质量。</p></div><div class="output-counts"><span>报告 <b>{{ data.outputs.reports }}</b></span><span>写作 <b>{{ data.outputs.writing }}</b></span><span>PPTX <b>{{ data.outputs.decks }}</b></span></div></header>
        <button v-for="item in data.outputs.recent" :key="`${item.type}-${item.created_at}-${item.title}`" class="output-row" @click="router.push(item.path)"><span :class="item.type">{{ item.label }}</span><b>{{ item.title }}</b><time>{{ formatDate(item.created_at) }}</time></button>
        <StudyEmptyState v-if="!loading&&!data.outputs.recent.length" compact title="还没有研究输出" description="批判性审查、写作稿与 PPTX 会集中显示在这里。" />
      </section>
    </div>
    <p class="insights-boundary">{{ data.boundary }}</p>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { getKnowledgeBaseInsights } from '../api'
import StudyEmptyState from '../components/StudyEmptyState.vue'

const emptyData=()=>({overview:{book_count:0,ready_count:0,health_score:100,knowledge_object_count:0,traceable_rate:0,report_count:0},coverage:[],health:{issue_count:0,categories:[],items:[]},knowledge:{annotations:0,notes:0,evidence_cards:0,tree_nodes:0,traceable_objects:0,evidence_audit:{supported:0,partial:0,needs_review:0,unsupported:0}},outputs:{reports:0,writing:0,decks:0,finished_decks:0,recent:[]},activity:[],boundary:''})
const router=useRouter(),days=ref(30),loading=ref(false),error=ref(''),data=ref(emptyData()),activityChart=ref(null)
let chart
const percent=value=>`${Math.round(Number(value||0)*100)}%`
const formatDate=value=>value?new Date(value).toLocaleDateString('zh-CN',{month:'short',day:'numeric'}):'—'
const healthTone=computed(()=>data.value.overview.health_score>=90?'good':data.value.overview.health_score>=70?'watch':'risk')
const renderActivity=()=>{
  if(!activityChart.value)return
  chart=chart||echarts.init(activityChart.value)
  const rows=data.value.activity||[]
  const series=[
    ['导入','imports','#7b8f8a'],
    ['取证','evidence','#b8834d'],
    ['沉淀','knowledge','#466f5b'],
    ['输出','outputs','#835f78'],
  ].map(([name,key,color])=>({
    name,type:'line',smooth:.25,showSymbol:false,
    lineStyle:{width:2,color},itemStyle:{color},areaStyle:{color,opacity:.035},
    data:rows.map(row=>row[key]),
  }))
  chart.setOption({
    animationDuration:220,tooltip:{trigger:'axis'},grid:{left:34,right:18,top:20,bottom:28},
    xAxis:{type:'category',boundaryGap:false,data:rows.map(row=>row.date.slice(5)),axisLabel:{color:'#85796a',interval:days.value>90?29:days.value>30?9:4},axisLine:{lineStyle:{color:'#d8cec0'}}},
    yAxis:{type:'value',minInterval:1,axisLabel:{color:'#85796a'},splitLine:{lineStyle:{color:'#ece4d8'}}},
    series,
  })
}
const loadInsights=async()=>{loading.value=true;error.value='';try{data.value=await getKnowledgeBaseInsights(days.value);await nextTick();renderActivity()}catch(err){error.value=`知识库洞察加载失败：${err.message}`}finally{loading.value=false}}
const resize=()=>chart?.resize()
onMounted(()=>{loadInsights();window.addEventListener('resize',resize)})
onBeforeUnmount(()=>{window.removeEventListener('resize',resize);chart?.dispose()})
</script>

<style scoped>
.insights-page{box-sizing:border-box;width:100%;max-width:1500px}.insights-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:20px 22px;border:1px solid rgba(250,246,237,.18);border-radius:13px;background:rgba(22,43,44,.9);color:#f7f1e6}.insights-head>div:first-child{min-width:0}.insights-head>div>span{color:#c39a70;font-size:10px;letter-spacing:1.5px}.insights-head h1{margin:6px 0;font-family:var(--study-font-display);font-size:30px}.insights-head p{max-width:780px;color:rgba(247,241,230,.72);font-size:14px;line-height:1.7}.head-actions{display:flex;flex:none;gap:8px}.head-actions .el-select{width:120px}.metric-strip{display:grid;grid-template-columns:repeat(5,1fr);margin:12px 0;border:1px solid var(--study-card-border);border-radius:12px;background:#fbf8f1;overflow:hidden}.metric-strip button{display:flex;min-height:114px;align-items:flex-start;justify-content:center;flex-direction:column;padding:16px 18px;border:0;border-right:1px solid var(--study-card-border);background:transparent;text-align:left;cursor:pointer}.metric-strip button:last-child{border-right:0}.metric-strip button:hover{background:#f4ede2}.metric-strip span,.metric-strip small{color:var(--study-text-secondary);font-size:11px}.metric-strip strong{margin:6px 0;color:var(--study-text-primary);font:600 27px var(--study-font-latin)}.metric-strip strong.good{color:#3f7157}.metric-strip strong.watch{color:#99671d}.metric-strip strong.risk{color:#a7473f}.metric-strip strong i{margin-left:3px;color:#8c8173;font-size:13px;font-style:normal}.insights-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(360px,.75fr);gap:12px}.paper-panel{border:1px solid var(--study-card-border);border-radius:12px;background:#fbf8f1;box-shadow:var(--study-shadow-sm);overflow:hidden}.paper-panel>header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:15px 17px;border-bottom:1px solid var(--study-card-border)}.paper-panel h2{font-family:var(--study-font-display);font-size:18px}.paper-panel header p{margin-top:4px;color:var(--study-text-secondary);font-size:11px;line-height:1.55}.paper-panel>header>span{color:var(--study-text-secondary);font-size:12px}.coverage-row{display:grid;width:100%;grid-template-columns:145px minmax(80px,1fr) 70px 52px;align-items:center;gap:12px;padding:12px 17px;border:0;border-bottom:1px solid #ebe3d7;background:transparent;color:var(--study-text-primary);text-align:left;cursor:pointer}.coverage-row:hover{background:#f5eee3}.coverage-row>span{font-size:13px}.coverage-track{height:6px;border-radius:4px;background:#e4dbcf;overflow:hidden}.coverage-track i{display:block;height:100%;border-radius:inherit;background:#536f64}.coverage-row b,.coverage-row em{font-size:12px;font-style:normal;text-align:right}.coverage-row em{color:var(--study-text-secondary)}.health-categories{display:flex;flex-wrap:wrap;gap:6px;padding:12px 15px}.health-categories button{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid #ded4c6;border-radius:7px;background:#fffdf8;cursor:pointer}.health-categories b{font-variant-numeric:tabular-nums}.health-categories .danger b{color:#a7473f}.health-categories .warning b{color:#99671d}.attention-row{display:grid;grid-template-columns:8px minmax(0,1fr);gap:9px;padding:10px 16px;border-top:1px solid #ebe3d7;cursor:pointer}.attention-row:hover{background:#f5eee3}.attention-row>i{width:7px;height:7px;margin-top:5px;border-radius:50%;background:#6f8290}.attention-row>i.warning{background:#b07929}.attention-row>i.danger{background:#ae4e46}.attention-row div{display:flex;min-width:0;flex-direction:column}.attention-row b,.attention-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.attention-row b{font-family:var(--study-font-reading);font-size:12px}.attention-row span{margin-top:3px;color:var(--study-text-secondary);font-size:10px}.activity-panel{margin-top:12px}.activity-panel>header{align-items:center}.activity-chart{height:280px}.legend{display:flex;gap:12px}.legend span{color:var(--study-text-secondary);font-size:11px}.legend span::before{display:inline-block;width:7px;height:7px;margin-right:5px;border-radius:50%;background:#777;content:''}.legend .imports::before{background:#7b8f8a}.legend .evidence::before{background:#b8834d}.legend .knowledge::before{background:#466f5b}.legend .outputs::before{background:#835f78}.lower-grid{grid-template-columns:minmax(360px,.75fr) minmax(0,1.25fr);margin-top:12px}.object-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--study-card-border)}.object-grid div{display:flex;align-items:center;flex-direction:column;padding:16px 8px;background:#fbf8f1}.object-grid strong{font:600 23px var(--study-font-latin)}.object-grid span{margin-top:4px;color:var(--study-text-secondary);font-size:11px}.audit-ledger{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:13px}.audit-ledger span{display:grid;grid-template-columns:8px 1fr auto;align-items:center;gap:6px;color:var(--study-text-secondary);font-size:11px}.audit-ledger i{width:7px;height:7px;border-radius:50%}.audit-ledger .supported{background:#4c775f}.audit-ledger .partial{background:#ae842d}.audit-ledger .review{background:#b36b2c}.audit-ledger .unsupported{background:#aa4d48}.output-counts{display:flex;gap:9px;color:var(--study-text-secondary);font-size:11px}.output-row{display:grid;width:100%;grid-template-columns:70px minmax(0,1fr) 64px;align-items:center;gap:10px;padding:11px 16px;border:0;border-bottom:1px solid #ebe3d7;background:transparent;text-align:left;cursor:pointer}.output-row:hover{background:#f5eee3}.output-row>span{padding:3px 5px;border-radius:5px;background:#e8e2d8;color:#6e6254;font-size:10px;text-align:center}.output-row>span.report{background:#e1ebe4;color:#40654f}.output-row>span.writing{background:#ede2eb;color:#73526e}.output-row>span.deck{background:#e4e8ec;color:#566a7b}.output-row b{overflow:hidden;font-family:var(--study-font-reading);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.output-row time{color:var(--study-text-secondary);font-size:10px;text-align:right}.insights-boundary{margin:12px 3px;color:rgba(247,241,230,.68);font-size:11px;line-height:1.6}@media(max-width:1120px){.metric-strip{grid-template-columns:repeat(3,1fr)}.metric-strip button:nth-child(3){border-right:0}.insights-grid,.lower-grid{grid-template-columns:1fr}}@media(max-width:700px){.insights-page{width:calc(100vw - 16px);max-width:calc(100vw - 16px)}.insights-head{align-items:flex-start;flex-direction:column}.head-actions{width:100%}.head-actions .el-select{flex:1}.metric-strip{grid-template-columns:1fr 1fr}.metric-strip button{min-height:96px;border-bottom:1px solid var(--study-card-border)}.metric-strip button:nth-child(odd){border-right:1px solid var(--study-card-border)}.metric-strip button:nth-child(even){border-right:0}.coverage-row{grid-template-columns:110px minmax(54px,1fr) 52px}.coverage-row em{display:none}.paper-panel>header{align-items:flex-start;flex-direction:column}.legend{flex-wrap:wrap}.activity-chart{height:230px}.object-grid{grid-template-columns:1fr 1fr}.output-counts{flex-wrap:wrap}.output-row{grid-template-columns:64px minmax(0,1fr)}.output-row time{display:none}}
@media(max-width:700px){.head-actions{display:grid;grid-template-columns:minmax(0,1fr) auto}.head-actions .el-select{width:100%;min-width:0}.head-actions .el-button{margin-left:0}.metric-strip button:last-child{grid-column:1/-1;border-right:0}}
</style>
