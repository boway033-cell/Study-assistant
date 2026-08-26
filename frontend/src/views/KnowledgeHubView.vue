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
.knowledge-hub{max-width:1500px}.function-switcher{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;margin-bottom:12px;overflow:hidden;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-card-border);box-shadow:var(--study-shadow-sm)}.function-switcher button{display:flex;align-items:center;gap:10px;padding:12px 16px;border:0;background:#f7f2e9;color:#62594d;cursor:pointer;text-align:left;transition:background .16s ease,color .16s ease,transform .16s ease}.function-switcher button:hover{background:#fffaf2;color:#74481f}.function-switcher button.active{background:#fffdf8;color:#70431c;box-shadow:inset 0 -3px 0 #a66b32}.function-switcher i{color:#a27b55;font:700 12px Georgia,serif}.function-switcher span{display:flex;min-width:0;flex-direction:column}.function-switcher small{margin-top:2px;color:var(--el-text-color-secondary);font-size:var(--study-font-size-xs)}.function-stage{min-width:0}.function-stage :deep(.study-page){padding:0}@media(max-width:760px){.function-switcher{grid-template-columns:repeat(2,minmax(0,1fr))}.function-switcher button{padding:10px 12px}}
</style>
