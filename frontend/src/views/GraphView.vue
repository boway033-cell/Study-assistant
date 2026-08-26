<template>
  <div class="graph-page study-page">
    <KnowledgeScopeSelector v-if="!embedded" />
    <div v-if="!knowledgeBookIds.length" class="scope-required"><b>先选择用于发现关系的书目</b><span>单本用于梳理内部概念，多本用于发现共同概念与跨文献连接。</span></div>
    <template v-else>
      <div class="graph-toolbar"><div><b>概念关系探索</b><span>{{ nodes.length }} 个概念 · {{ edges.length }} 条共现关系</span></div><el-input v-model="conceptFilter" clearable placeholder="筛选概念" /><el-slider v-model="minCount" :min="1" :max="maxCount" :show-tooltip="false" /><span>至少出现 {{ minCount }} 次</span><el-button :loading="loading" @click="loadGraph">重新生成</el-button></div>
      <div class="graph-workspace">
        <el-card shadow="never" class="graph-main"><div v-if="filteredNodes.length" ref="chart" class="chart-box" v-loading="loading"></div><el-empty v-else :description="loading?'正在建立概念关系':'当前范围尚无可用概念；请先在资料库执行智能分析'" /></el-card>
        <el-card shadow="never" class="graph-side"><template #header><div class="side-title"><div><b>{{ selectedConcept || '概念出处' }}</b><small>{{ selectedConcept ? `${sources.length} 条可回溯来源` : '点击图中概念查看它来自哪里' }}</small></div><el-button v-if="selectedConcept" text @click="clearConcept">清除</el-button></div></template><div v-loading="loadingSources" class="source-list"><el-empty v-if="!selectedConcept" description="图谱用于发现，原文用于验证" :image-size="72" /><el-empty v-else-if="!sources.length&&!loadingSources" description="没有找到可定位出处" :image-size="72" /><button v-for="source in sources" :key="source.chunk_id" class="source-item" @click="goSource(source)"><div class="source-meta"><span>{{ source.book_title }}</span><i>{{ source.chapter_title || '未分章' }}{{ source.page ? ` · 第 ${source.page} 页` : '' }}</i></div><div class="source-snippet" v-html="sanitizeHtml(source.snippet)"></div><small>回到原文核对 →</small></button></div></el-card>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
import { knowledgeBookIds, loadKnowledgeBooks } from '../stores/knowledgeScope'
import { getGraph, getConceptSources } from '../api'
import { sanitizeHtml } from '../utils/markdown'
defineProps({ embedded: { type: Boolean, default: false } })
const router=useRouter();const loading=ref(false);const loadingSources=ref(false);const nodes=ref([]);const edges=ref([]);const selectedConcept=ref('');const sources=ref([]);const chart=ref(null);const conceptFilter=ref('');const minCount=ref(1);let chartInstance=null
const maxCount=computed(()=>Math.max(1,...nodes.value.map(node=>node.count||1)))
const filteredNodes=computed(()=>{const q=conceptFilter.value.trim().toLowerCase();return nodes.value.filter(node=>(node.count||1)>=minCount.value&&(!q||node.name.toLowerCase().includes(q)))})
const filteredNames=computed(()=>new Set(filteredNodes.value.map(node=>node.name)))
const filteredEdges=computed(()=>edges.value.filter(edge=>filteredNames.value.has(edge.source)&&filteredNames.value.has(edge.target)))
const loadGraph=async()=>{selectedConcept.value='';sources.value=[];if(!knowledgeBookIds.value.length){nodes.value=[];edges.value=[];disposeChart();return}loading.value=true;try{const data=await getGraph(knowledgeBookIds.value);nodes.value=data.nodes||[];edges.value=data.edges||[];minCount.value=1;await nextTick();renderChart()}catch(e){ElMessage.error(`图谱没有生成：${e.message}。请确认所选资料已完成智能分析。`)}finally{loading.value=false}}
const renderChart=()=>{if(!chart.value||!filteredNodes.value.length)return;chartInstance=chartInstance||echarts.init(chart.value);chartInstance.setOption({animationDuration:320,tooltip:{trigger:'item',formatter:p=>p.dataType==='node'?`${p.name}<br/>出现 ${p.data.value||1} 次`:''},series:[{type:'graph',layout:'force',roam:true,draggable:true,data:filteredNodes.value.map(node=>({name:node.name,value:node.count||1,symbolSize:Math.max(16,Math.min(46,14+(node.count||1)*3))})),edges:filteredEdges.value.map(edge=>({source:edge.source,target:edge.target,lineStyle:{width:Math.min(3,.6+edge.weight*.45)}})),force:{repulsion:145,edgeLength:[65,105],gravity:.08},label:{show:true,fontSize:11,color:'#76502f',position:'right'},itemStyle:{color:'#29484a',borderColor:'#f5f0e8',borderWidth:1},lineStyle:{color:'#b79777',opacity:.34,curveness:.08},emphasis:{focus:'adjacency',lineStyle:{width:3,opacity:.85}}}]},true);chartInstance.off('click');chartInstance.on('click',event=>{if(event.dataType==='node')selectConcept(event.name)})}
const selectConcept=async(name)=>{selectedConcept.value=name;sources.value=[];loadingSources.value=true;try{const data=await getConceptSources(name,knowledgeBookIds.value);sources.value=data.items||[]}catch(e){ElMessage.error(`无法加载概念出处：${e.message}`)}finally{loadingSources.value=false}}
const clearConcept=()=>{selectedConcept.value='';sources.value=[]}
const goSource=source=>router.push(`/reader/${source.book_id}?page=${source.page||1}`)
const resize=()=>chartInstance?.resize();const disposeChart=()=>{chartInstance?.dispose();chartInstance=null}
watch([conceptFilter,minCount],async()=>{await nextTick();renderChart()});watch(knowledgeBookIds,loadGraph,{deep:true})
onMounted(async()=>{await loadKnowledgeBooks();await loadGraph();window.addEventListener('resize',resize)});onBeforeUnmount(()=>{window.removeEventListener('resize',resize);disposeChart()})
</script>

<style scoped>
.graph-page{max-width:1500px}.scope-required{display:flex;flex-direction:column;gap:6px;padding:40px;border:1px dashed #d8c7b0;border-radius:var(--study-radius-md);background:#f7f2e9;text-align:center}.scope-required span{color:var(--el-text-color-secondary)}.graph-toolbar{display:grid;grid-template-columns:minmax(240px,1fr) 220px 170px auto auto;align-items:center;gap:12px;margin-bottom:12px;padding:11px 16px;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:#f8f3ea;box-shadow:var(--study-shadow-sm)}.graph-toolbar>div{display:flex;flex-direction:column}.graph-toolbar>div span,.graph-toolbar>span{color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.graph-workspace{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:12px;height:calc(100vh - 270px);min-height:560px}.graph-main,.graph-side{min-width:0;height:100%}.graph-main :deep(.el-card__body){height:100%}.chart-box{height:100%}.side-title{display:flex;align-items:center;justify-content:space-between}.side-title>div{display:flex;flex-direction:column}.side-title small{margin-top:3px;color:var(--el-text-color-secondary);font-weight:400}.source-list{max-height:calc(100vh - 370px);overflow-y:auto}.source-item{display:flex;width:100%;flex-direction:column;gap:7px;padding:12px 4px;border:0;border-bottom:1px solid var(--el-border-color-lighter);background:transparent;color:#514a40;cursor:pointer;text-align:left;transition:padding .16s ease,background .16s ease}.source-item:hover{padding-inline:10px;background:#faf4ea}.source-meta{display:flex;justify-content:space-between;gap:8px;font-size:var(--study-font-size-xs)}.source-meta span{color:#8b5a2b;font-weight:700}.source-meta i{overflow:hidden;color:var(--el-text-color-secondary);font-style:normal;text-overflow:ellipsis;white-space:nowrap}.source-snippet{font-size:var(--study-font-size-sm);line-height:1.65}.source-item>small{color:#9a6a3c}@media(max-width:1050px){.graph-toolbar{grid-template-columns:1fr 220px}.graph-workspace{grid-template-columns:1fr;height:auto}.graph-main{height:560px}.source-list{max-height:420px}}@media(max-width:680px){.graph-toolbar{grid-template-columns:1fr}.graph-main{height:480px}}
</style>
