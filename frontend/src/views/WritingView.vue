<template>
  <main class="writing-page study-page">
    <header class="writing-page-head"><div><span>RESEARCH TO DRAFT</span><h1>写作工作台</h1><p>从知识对象取材，以 Writing DNA 校准表达；草稿、审阅和 Word 输出在同一工作区完成。</p></div><el-button plain @click="router.push('/knowledge-hub?view=notes')">管理取材对象</el-button></header>
    <WritingLabDrawer embedded />
  </main>
</template>

<script setup>
import { useRouter } from 'vue-router'
import WritingLabDrawer from '../components/WritingLabDrawer.vue'
const router=useRouter()
</script>

<style scoped>
.writing-page-head{box-sizing:border-box;width:100%;padding:18px 20px!important;border:1px solid rgba(250,246,237,.2)!important;border-radius:12px;background:rgba(22,43,44,.88);box-shadow:var(--study-shadow-sm);color:#f7f1e6}.writing-page-head p{color:rgba(247,241,230,.72)!important}
.writing-page-head>div{min-width:0;max-width:100%}.writing-page-head p{overflow-wrap:anywhere}
@media(max-width:700px){.writing-page{box-sizing:border-box;width:calc(100vw - 16px);max-width:calc(100vw - 16px)}}
.writing-page{max-width:1500px}.writing-page-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:16px;padding:8px 2px 18px;border-bottom:1px solid var(--study-card-border)}.writing-page-head span{color:#8e7357;font-size:10px;letter-spacing:1.6px}.writing-page-head h1{margin:6px 0;font-family:var(--study-font-display);font-size:30px;font-weight:600}.writing-page-head p{max-width:760px;color:var(--study-text-secondary);font-size:14px;line-height:1.65}@media(max-width:700px){.writing-page-head{align-items:flex-start;flex-direction:column}.writing-page-head h1{font-size:25px}}
</style>
