<template>
  <el-drawer v-model="visible" title="全局任务中心" size="min(430px, 92vw)" append-to-body>
    <div class="task-summary">
      <div><strong>{{ activeTasks.length }}</strong><span>进行中</span></div>
      <div><strong>{{ finishedTasks.length }}</strong><span>最近完成</span></div>
      <el-button circle :loading="taskCenter.loading" @click="refreshTasks"><el-icon><Refresh /></el-icon></el-button>
    </div>
    <el-alert :closable="false" type="info" title="OCR 与解析会串行控内存；研读和写作使用独立队列，不必等待整本文献 OCR。" />
    <div v-if="!taskCenter.items.length && !taskCenter.loading" class="task-empty">暂无任务记录</div>
    <div v-for="task in taskCenter.items" :key="task.task_id" class="task-item" @click="openTask(task)">
      <div class="task-head">
        <span class="task-kind">{{ taskLabel(task.name) }}</span>
        <el-tag size="small" :type="statusType(task.status)">{{ statusLabel(task.status) }}</el-tag>
      </div>
      <strong class="task-title">{{ task.book_title || task.result?.focus || '知识库任务' }}</strong>
      <el-progress v-if="isActive(task)" :percentage="percentage(task.progress)" :stroke-width="6" :show-text="false" />
      <p :class="{ error: task.status === 'failed', stalled: isStalled(task) }">{{ isStalled(task) ? '长时间没有新进度，可取消后重试；已完成页面缓存不会删除。' : task.error || task.message || stageLabel(task.stage) }}</p>
      <footer><time>{{ formatTime(task.updated_at || task.created_at) }}</time><div class="task-actions"><el-button v-if="canRetry(task)" link type="primary" :loading="retryingId===task.task_id" @click.stop="requestRetry(task)">重新解析</el-button><el-button v-if="isActive(task)" link type="danger" :loading="cancellingId===task.task_id" @click.stop="requestCancel(task)">取消任务</el-button></div></footer>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { taskCenter, refreshTasks } from '../stores/taskCenter'
import { cancelTask, retryTask } from '../api'

const visible = defineModel({ type: Boolean, default: false })
const router = useRouter()
const cancellingId = ref('')
const retryingId = ref('')
const isActive = (task) => ['pending', 'running', 'cancelling'].includes(task.status)
const canRetry = task => ['failed', 'cancelled'].includes(task.status) && ['import', 'reimport'].includes(task.name)
const activeTasks = computed(() => taskCenter.items.filter(isActive))
const finishedTasks = computed(() => taskCenter.items.filter((item) => !isActive(item)))
const taskLabel = (name) => ({ import: '文献导入 / OCR', reimport: '重新解析', deep: '结构精读', study: '综合研读', 'study-overview': '综合研读', 'literature-review': '文献综述', writing_dna: '写作 DNA', deck: 'PPTX 汇报', deck_outline: 'PPTX 提纲', deck_render: 'PPTX 渲染' }[name] || '知识处理')
const statusLabel = (status) => ({ pending: '排队中', running: '处理中', cancelling: '取消中', cancelled: '已取消', done: '已完成', failed: '失败' }[status] || status)
const statusType = (status) => ({ done: 'success', failed: 'danger', cancelled: 'info', cancelling: 'warning', running: 'warning', pending: 'info' }[status] || 'info')
const stageLabel = (stage) => ({ parsing: '正在解析原文', ocr: '正在识别扫描页', deep: '正在结构化精读', overview: '正在汇总研读材料', 'research-plan': '正在规划研究路径', evidence: '正在检索和整理证据', synthesis: '正在跨文献综合写作', generate: '正在生成汇报', deck_outline: '正在生成可编辑提纲', deck_render: '正在渲染并审计 PPTX' }[stage] || stage || '等待处理')
const percentage = (value) => Math.max(0, Math.min(100, Math.round((value || 0) * 100)))
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''
const isStalled = (task) => task.status === 'running' && ['ocr', 'parsing'].includes(task.stage) && Date.now() - new Date(task.updated_at || task.created_at).getTime() > 120000
const requestCancel = async (task) => {
  try {
    await ElMessageBox.confirm('停止后会保留已经完成的页面缓存。下次重新解析时可复用缓存。', '取消任务', { type: 'warning', confirmButtonText: '停止任务' })
    cancellingId.value = task.task_id
    await cancelTask(task.task_id)
    await refreshTasks()
    ElMessage.success('已提交停止请求')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '任务未能停止')
  } finally { cancellingId.value = '' }
}
const requestRetry = async task => {
  retryingId.value = task.task_id
  try {
    await retryTask(task.task_id)
    await refreshTasks()
    ElMessage.success('已重新排队；完成的 OCR 页面会从缓存继续')
  } catch (error) { ElMessage.error(error.message || '任务无法重试') }
  finally { retryingId.value = '' }
}
const openTask = (task) => {
  visible.value = false
  if (['study', 'study-overview'].includes(task.name)) {
    const reportId = Number(task.result?.report_id)
    router.push({ path: '/study', query: reportId ? { reportId } : { taskId: task.task_id } })
  } else if (['deck', 'deck_outline', 'deck_render'].includes(task.name)) {
    router.push({ path: '/literature-workbench', query: { bookId: task.book_id } })
  } else if (task.book_id) router.push(`/reader/${task.book_id}`)
}
</script>

<style scoped>
.task-summary{display:grid;grid-template-columns:1fr 1fr auto;align-items:center;gap:8px;margin-bottom:12px}.task-summary>div{display:flex;flex-direction:column;padding:10px;border-radius:var(--study-radius-md);background:var(--study-surface-muted)}.task-summary strong{font:700 20px Georgia,serif;color:var(--el-color-primary)}.task-summary span{font-size:var(--study-font-size-xs);color:var(--study-text-secondary)}.task-item{margin-top:10px;padding:12px;border:1px solid var(--study-card-border);border-radius:var(--study-radius-md);background:var(--study-surface-paper);cursor:pointer}.task-item:hover{border-color:var(--el-border-color)}.task-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}.task-kind{font-size:var(--study-font-size-xs);letter-spacing:.5px;color:var(--el-color-primary)}.task-title{display:block;margin-bottom:8px;color:var(--study-text-primary)}.task-item p{min-height:18px;margin:7px 0 2px;font-size:var(--study-font-size-xs);color:var(--study-text-secondary)}.task-item p.error{color:var(--el-color-danger)}.task-item p.stalled{color:var(--el-color-warning-dark-2)}.task-item footer{display:flex;align-items:center;justify-content:space-between}.task-actions{display:flex;align-items:center}.task-item time{font-size:10px;color:var(--study-text-muted)}.task-empty{padding:36px 0;text-align:center;color:var(--study-text-muted)}
</style>
