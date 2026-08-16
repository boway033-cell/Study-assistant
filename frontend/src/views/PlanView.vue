<template>
  <div class="plan-page">
    <!-- 未设定计划：表单 -->
    <el-card v-if="!plan" shadow="never" class="setup-card">
      <template #header>🎯 设定学习目标</template>
      <el-form label-width="90px" style="max-width: 500px">
        <el-form-item label="计划名称">
          <el-input v-model="name" placeholder="如：保研复习计划" />
        </el-form-item>
        <el-form-item label="考试日期">
          <el-date-picker v-model="examDate" type="date" value-format="YYYY-MM-DD"
            placeholder="选择考试日期" style="width: 100%" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">开始倒推计划</el-button>
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" show-icon
        title="设定考试日期后，系统按「掌握度从弱到强」把知识树节点平均分配到每天，形成复习节奏。" />
    </el-card>

    <!-- 已设定计划：倒计时 + 每日任务 -->
    <template v-else>
      <div class="plan-summary">
        <el-card shadow="never"><div class="sum-num">{{ totalDays }}</div><div class="sum-label">距考试天数</div></el-card>
        <el-card shadow="never"><div class="sum-num">{{ nodeTotal }}</div><div class="sum-label">知识点总数</div></el-card>
        <el-card shadow="never"><div class="sum-num">{{ checkedCount }}</div><div class="sum-label">已打卡天数</div></el-card>
        <el-card shadow="never" class="plan-info">
          <div class="plan-name">{{ plan.name }}</div>
          <div class="exam-date">考试：{{ plan.exam_date }}</div>
          <el-button size="small" @click="resetPlan">修改计划</el-button>
        </el-card>
      </div>

      <el-card shadow="never" class="task-card">
        <template #header>
          <div class="task-header">
            <span>📅 每日任务（掌握度弱 → 强）</span>
            <span class="legend">
              <el-tag size="small" type="danger" effect="plain">薄弱</el-tag>
              <el-tag size="small" type="warning" effect="plain">模糊</el-tag>
              <el-tag size="small" type="success" effect="plain">已掌握</el-tag>
            </span>
          </div>
        </template>
        <div class="day-list">
          <div v-for="d in days" :key="d.date" :class="['day-item', { done: d.checked }]">
            <div class="day-left">
              <div class="day-date">{{ d.date }}</div>
              <el-tag v-if="d.checked" type="success" size="small">已打卡</el-tag>
            </div>
            <div class="day-nodes">
              <el-tag v-for="n in d.nodes" :key="n.id" :type="masteryTag(n.mastery)" size="small" effect="plain">
                {{ n.title }}
              </el-tag>
              <span v-if="!d.nodes.length" class="day-empty">—</span>
            </div>
            <el-button size="small" :type="d.checked ? 'info' : 'primary'"
              :disabled="d.date > todayStr" @click="toggle(d)">
              {{ d.checked ? '取消' : '打卡' }}
            </el-button>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getPlan, savePlan, planCheckin } from '../api'

const plan = ref(null)
const name = ref('学习计划')
const examDate = ref('')
const saving = ref(false)
const days = ref([])
const totalDays = ref(0)
const nodeTotal = ref(0)

const todayStr = (() => {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return d.getFullYear() + '-' + mm + '-' + dd
})()
const checkedCount = computed(() => days.value.filter(d => d.checked).length)

const masteryTag = (m) => ({ unknown: 'danger', fuzzy: 'warning', known: 'success' }[m] || 'info')

const load = async () => {
  try {
    const data = await getPlan()
    plan.value = data.plan
    days.value = data.days || []
    totalDays.value = data.total_days || 0
    nodeTotal.value = data.node_total || 0
  } catch (e) { /* ignore */ }
}

const save = async () => {
  if (!examDate.value) { ElMessage.warning('请选择考试日期'); return }
  saving.value = true
  try {
    await savePlan({ name: name.value || '学习计划', exam_date: examDate.value })
    ElMessage.success('计划已创建')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

const resetPlan = () => {
  plan.value = null
  examDate.value = ''
}

const toggle = async (d) => {
  try {
    await planCheckin({ date: d.date, done: d.checked ? 0 : 1 })
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.plan-page { display: flex; flex-direction: column; gap: 16px; }
.setup-card { max-width: 620px; }
.plan-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.sum-num { font-size: 28px; font-weight: 700; color: var(--el-color-primary); }
.sum-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.plan-info { display: flex; flex-direction: column; gap: 6px; }
.plan-name { font-weight: 600; }
.exam-date { font-size: 13px; color: var(--el-text-color-secondary); }
.task-header { display: flex; justify-content: space-between; align-items: center; }
.legend { display: flex; gap: 6px; }
.day-list { max-height: calc(100vh - 300px); overflow-y: auto; }
.day-item { display: flex; align-items: center; gap: 14px; padding: 10px 6px; border-bottom: 1px solid var(--el-border-color-extra-light); }
.day-item.done { background: var(--el-fill-color-lighter); }
.day-left { width: 110px; display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
.day-date { font-size: 13px; color: var(--el-text-color-primary); }
.day-nodes { flex: 1; display: flex; flex-wrap: wrap; gap: 6px; }
.day-empty { color: var(--el-text-color-placeholder); }
</style>
