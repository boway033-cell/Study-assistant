<template>
  <div class="knowledge-hub study-page">
    <KnowledgeScopeSelector v-if="['notes','study','tree','graph'].includes(activeView)" />
    <nav class="function-switcher" aria-label="研读功能">
      <button v-for="item in options" :key="item.value" :class="{active:activeView===item.value}" @click="selectView(item.value)"><i>{{ item.index }}</i><span><b>{{ item.label }}</b><small>{{ item.description }}</small></span></button>
      <el-dropdown trigger="click" @command="selectView" class="structure-menu"><button type="button" class="structure-trigger" :class="{active:['tree','graph'].includes(activeView)}">{{ activeView==='tree'?'知识树':activeView==='graph'?'知识图谱':'知识结构' }} ▾</button><template #dropdown><el-dropdown-menu><el-dropdown-item command="tree">知识树</el-dropdown-item><el-dropdown-item command="graph">知识图谱</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
    </nav>
    <section class="function-stage"><component :is="activeComponent" embedded /></section>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
const route=useRoute();const router=useRouter()
const options=[{value:'understand',index:'01',label:'理解与发现',description:'重建论证、比较概念'},{value:'chat',index:'02',label:'问资料',description:'带来源的追问'},{value:'notes',index:'03',label:'笔记与证据',description:'捕获、核验与整理'},{value:'study',index:'04',label:'研究报告',description:'跨文献综合与审计'}]
const components={understand:defineAsyncComponent(()=>import('./SensemakingView.vue')),chat:defineAsyncComponent(()=>import('./ChatView.vue')),notes:defineAsyncComponent(()=>import('./NotesView.vue')),study:defineAsyncComponent(()=>import('./StudyView.vue')),tree:defineAsyncComponent(()=>import('./KnowledgeView.vue')),graph:defineAsyncComponent(()=>import('./GraphView.vue'))}
const normalized=value=>components[value]?value:'understand'
const activeView=ref(normalized(route.query.view))
const activeComponent=computed(()=>components[activeView.value])
const selectView=view=>{if(view===activeView.value)return;activeView.value=view;router.replace({query:{...route.query,view}})}
watch(()=>route.query.view,value=>{activeView.value=normalized(value)})
</script>

<style scoped>
.knowledge-hub{max-width:1500px}.function-switcher{display:flex;gap:2px;margin-bottom:12px;padding:3px;overflow-x:auto;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-surface-muted);box-shadow:var(--study-shadow-sm)}.function-switcher button{display:flex;min-height:38px;align-items:center;justify-content:center;gap:7px;flex:1;padding:7px 12px;border:0;background:transparent;color:var(--study-text-secondary);cursor:pointer;white-space:nowrap}.function-switcher button:hover{background:rgba(255,253,248,.72);color:var(--el-color-primary)}.function-switcher button.active{background:var(--study-surface-raised);color:var(--el-color-primary);box-shadow:var(--study-shadow-sm)}.function-switcher i{display:none}.function-switcher span{display:block}.function-switcher small{display:none}.structure-menu{flex:0 0 110px}.function-switcher .structure-trigger{width:100%;font-size:12px}.function-stage{min-width:0}.function-stage :deep(.study-page){padding:0}.function-stage :deep(.scope-required){min-height:112px;padding:20px;border-color:var(--el-border-color);border-radius:var(--study-radius-md);background:var(--study-surface-paper)}.function-stage :deep(.chat-page){height:calc(100dvh - 155px);min-height:620px}@media(max-width:620px){.function-switcher button{flex:none}.function-switcher{justify-content:flex-start}.function-stage :deep(.chat-page){height:auto;min-height:0}}
</style>
