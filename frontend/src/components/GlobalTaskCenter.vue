<template>
  <el-drawer v-model="visible" title="全局任务中心" size="min(430px, 92vw)" append-to-body>
    <div class="task-summary">
      <div><strong>{{ activeTasks.length }}</strong><span>进行中</span></div>
      <div><strong>{{ finishedTasks.length }}</strong><span>最近完成</span></div>
      <el-button circle :loading="taskCenter.loading" @click="refreshTasks"><el-icon><Refresh /></el-icon></el-button>
    </div>
    <el-alert :closable="false" type="info" title="任务在后台串行执行，以控制 OCR、解析和 AI 生成的内存峰值。" />
    <div v-if="!taskCenter.items.length && !taskCenter.loading" class="task-empty">暂无任务记录</div>
    <div v-for="task in taskCenter.items" :key="task.task_id" class="task-item" @click="openTask(task)">
      <div class="task-head">
        <span class="task-kind">{{ taskLabel(task.name) }}</span>
        <el-tag size="small" :type="statusType(task.status)">{{ statusLabel(task.status) }}</el-tag>
      </div>
      <strong class="task-title">{{ task.book_title || '知识库任务' }}</strong>
      <el-progress v-if="isActive(task)" :percentage="percentage(task.progress)" :stroke-width="6" :show-text="false" />
      <p :class="{ error: task.status === 'failed' }">{{ task.error || task.message || stageLabel(task.stage) }}</p>
      <time>{{ formatTime(task.updated_at || task.created_at) }}</time>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { taskCenter, refreshTasks } from '../stores/taskCenter'

const visible = defineModel({ type: Boolean, default: false })
const router = useRouter()
const isActive = (task) => ['pending', 'running'].includes(task.status)
const activeTasks = computed(() => taskCenter.items.filter(isActive))
const finishedTasks = computed(() => taskCenter.items.filter((item) => !isActive(item)))
const taskLabel = (name) => ({ import: '文献导入 / OCR', reimport: '重新解析', deep: '结构精读', deck: 'PPTX 汇报', deck_outline: 'PPTX 提纲', deck_render: 'PPTX 渲染' }[name] || '知识处理')
const statusLabel = (status) => ({ pending: '排队中', running: '处理中', done: '已完成', failed: '失败' }[status] || status)
const statusType = (status) => ({ done: 'success', failed: 'danger', running: 'warning', pending: 'info' }[status] || 'info')
const stageLabel = (stage) => ({ parsing: '正在解析原文', ocr: '正在识别扫描页', deep: '正在结构化精读', generate: '正在生成汇报', deck_outline: '正在生成可编辑提纲', deck_render: '正在渲染并审计 PPTX' }[stage] || stage || '等待处理')
const percentage = (value) => Math.max(0, Math.min(100, Math.round((value || 0) * 100)))
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''
const openTask = (task) => {
  if (!task.book_id) return
  visible.value = false
  if (['deck', 'deck_outline', 'deck_render'].includes(task.name)) router.push({ path: '/literature-workbench', query: { bookId: task.book_id } })
  else router.push(`/reader/${task.book_id}`)
}
</script>

<style scoped>
.task-summary{display:grid;grid-template-columns:1fr 1fr auto;align-items:center;gap:10px;margin-bottom:14px}.task-summary>div{display:flex;flex-direction:column;padding:12px;border-radius:12px;background:#f3eee4}.task-summary strong{font:700 24px Georgia,serif;color:#6f4721}.task-summary span{font-size:11px;color:#887762}.task-item{margin-top:12px;padding:14px;border:1px solid #ded5c7;border-radius:12px;background:#faf7f0;cursor:pointer;transition:.18s}.task-item:hover{border-color:#b99570;transform:translateY(-1px)}.task-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}.task-kind{font-size:11px;letter-spacing:1px;color:#8b5a2b}.task-title{display:block;margin-bottom:8px;color:#33362f}.task-item p{min-height:18px;margin:7px 0 2px;font-size:12px;color:#746b5e}.task-item p.error{color:#b94b45}.task-item time{font-size:10px;color:#a09382}.task-empty{padding:80px 0;text-align:center;color:#9a8d7a}
</style>
