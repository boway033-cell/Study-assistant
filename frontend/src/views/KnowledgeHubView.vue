<template>
  <div class="knowledge-hub study-page">
    <KnowledgeScopeSelector />
    <nav class="function-switcher" aria-label="知识沉淀功能">
      <button v-for="item in options" :key="item.value" :class="{active:activeView===item.value}" @click="selectView(item.value)"><i>{{ item.index }}</i><span><b>{{ item.label }}</b><small>{{ item.description }}</small></span></button>
    </nav>
    <section class="function-stage"><component :is="activeComponent" embedded /></section>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import KnowledgeScopeSelector from '../components/KnowledgeScopeSelector.vue'
const route=useRoute();const router=useRouter()
const options=[{value:'notes',index:'01',label:'笔记与证据',description:'捕获、核验与整理'},{value:'tree',index:'02',label:'知识树',description:'层级与掌握'},{value:'graph',index:'03',label:'知识图谱',description:'关系与出处'},{value:'study',index:'04',label:'研究报告',description:'比较、引用与结论'}]
const components={notes:defineAsyncComponent(()=>import('./NotesView.vue')),tree:defineAsyncComponent(()=>import('./KnowledgeView.vue')),graph:defineAsyncComponent(()=>import('./GraphView.vue')),study:defineAsyncComponent(()=>import('./StudyView.vue'))}
const normalized=value=>options.some(item=>item.value===value)?value:'notes'
const activeView=ref(normalized(route.query.view))
const activeComponent=computed(()=>components[activeView.value])
const selectView=view=>{if(view===activeView.value)return;activeView.value=view;router.replace({query:{...route.query,view}})}
watch(()=>route.query.view,value=>{activeView.value=normalized(value)})
</script>

<style scoped>
.knowledge-hub{max-width:1500px}.function-switcher{display:flex;gap:2px;margin-bottom:8px;padding:3px;overflow-x:auto;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-surface-muted);box-shadow:var(--study-shadow-sm)}.function-switcher button{display:flex;min-height:38px;align-items:center;justify-content:center;gap:7px;flex:1;padding:7px 12px;border:0;background:transparent;color:var(--study-text-secondary);cursor:pointer;white-space:nowrap}.function-switcher button:hover{background:rgba(255,253,248,.72);color:var(--el-color-primary)}.function-switcher button.active{background:var(--study-surface-raised);color:var(--el-color-primary);box-shadow:var(--study-shadow-sm)}.function-switcher i{display:none}.function-switcher span{display:block}.function-switcher small{display:none}.function-stage{min-width:0}.function-stage :deep(.study-page){padding:0}.function-stage :deep(.scope-required){min-height:112px;padding:20px;border-color:var(--el-border-color);border-radius:var(--study-radius-md);background:var(--study-surface-paper)}@media(max-width:620px){.function-switcher button{flex:none}.function-switcher{justify-content:flex-start}}
</style>
