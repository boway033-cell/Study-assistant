<template>
  <StudyCommandBar compact class="knowledge-scope-shell" title="书目范围" :description="`${knowledgeScopeLabel} · 所有知识功能共用此范围`">
    <div class="scope-control"><el-select :model-value="knowledgeBookIds" multiple collapse-tags collapse-tags-tooltip filterable :loading="knowledgeScopeLoading" placeholder="选择一本或多本资料" @change="changeScope"><el-option v-for="book in knowledgeBooks" :key="book.id" :label="book.title" :value="book.id" /></el-select></div>
    <template #actions><el-button v-if="!knowledgeBookIds.length && knowledgeBooks.length" plain @click="useRecent">使用最近资料</el-button><el-button v-if="knowledgeBookIds.length" link @click="changeScope([])">清除</el-button></template>
  </StudyCommandBar>
</template>

<script setup>
import { onMounted } from 'vue'
import { knowledgeBooks, knowledgeBookIds, knowledgeScopeLabel, knowledgeScopeLoading, setKnowledgeScope, loadKnowledgeBooks } from '../stores/knowledgeScope'
import StudyCommandBar from './StudyCommandBar.vue'
const emit = defineEmits(['change'])
const changeScope = (ids) => { setKnowledgeScope(ids); emit('change', ids) }
const useRecent = () => knowledgeBooks.value[0] && changeScope([knowledgeBooks.value[0].id])
onMounted(loadKnowledgeBooks)
</script>

<style scoped>
.knowledge-scope-shell{margin-bottom:8px}.scope-control{width:min(520px,42vw);min-width:280px}.scope-control :deep(.el-select){width:100%}@media(max-width:760px){.scope-control{width:100%;min-width:0}}
</style>
