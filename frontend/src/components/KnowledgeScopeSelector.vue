<template>
  <section class="knowledge-scope-shell">
    <div class="scope-identity"><span class="study-eyebrow">CAPTURE · CONNECT · SYNTHESIZE</span><h1>知识沉淀</h1><p>先限定书目，再沿同一范围整理笔记、结构与跨文献结论。</p></div>
    <div class="scope-control"><label>当前书目范围</label><el-select :model-value="knowledgeBookIds" multiple collapse-tags collapse-tags-tooltip filterable :loading="knowledgeScopeLoading" placeholder="请选择一本或多本资料" @change="changeScope"><el-option v-for="book in knowledgeBooks" :key="book.id" :label="book.title" :value="book.id" /></el-select><small>{{ knowledgeScopeLabel }} · 下方功能共用此范围</small></div>
  </section>
</template>

<script setup>
import { onMounted } from 'vue'
import { knowledgeBooks, knowledgeBookIds, knowledgeScopeLabel, knowledgeScopeLoading, setKnowledgeScope, loadKnowledgeBooks } from '../stores/knowledgeScope'
const emit = defineEmits(['change'])
const changeScope = (ids) => { setKnowledgeScope(ids); emit('change', ids) }
onMounted(loadKnowledgeBooks)
</script>

<style scoped>
.knowledge-scope-shell{display:grid;grid-template-columns:minmax(300px,1fr) minmax(360px,.75fr);align-items:center;gap:32px;margin-bottom:12px;padding:16px 20px;border:1px solid rgba(245,240,232,.16);border-radius:var(--study-radius-lg);background:linear-gradient(120deg,#173638,#536354);color:#f7f1e8;box-shadow:0 8px 24px rgba(19,43,43,.16)}.scope-identity h1{margin:3px 0;font:700 24px var(--study-font-reading);letter-spacing:1.5px}.scope-identity p{color:rgba(247,241,232,.72);font-size:var(--study-font-size-sm)}.scope-control{display:flex;min-width:0;flex-direction:column}.scope-control label{margin-bottom:5px;font-size:var(--study-font-size-xs);font-weight:700}.scope-control small{margin-top:5px;overflow:hidden;color:rgba(247,241,232,.66);font-size:var(--study-font-size-xs);text-overflow:ellipsis;white-space:nowrap}.scope-control :deep(.el-select__wrapper){background:#fffaf2}@media(max-width:900px){.knowledge-scope-shell{grid-template-columns:1fr;gap:14px}}@media(max-width:720px){.knowledge-scope-shell{padding:15px}}
</style>
