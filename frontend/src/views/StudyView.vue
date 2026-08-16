<template>
  <div>
    <el-tabs v-model="tab">
      <!-- ===== 综合阅读 ===== -->
      <el-tab-pane label="🧠 综合阅读" name="overview">
        <el-card shadow="never">
          <div class="ov-toolbar">
            <el-select v-model="ovBooks" multiple collapse-tags placeholder="选择文献（不选=全部）" style="width: 420px">
              <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <el-button type="primary" :loading="ovLoading" @click="genOverview">
              {{ ovLoading ? 'AI 综合阅读中…' : '生成综合阅读报告' }}
            </el-button>
            <span class="tip">AI 通读所选文献，输出：主题脉络 / 各文献定位 / 交叉知识点 / 思维训练题 / 学习路径</span>
          </div>
          <div v-if="ovProgress" class="ov-progress">
            <el-progress :percentage="ovProgress" :indeterminate="ovProgress === 0" />
            <span class="tip">{{ ovStage }}</span>
          </div>
          <el-divider content-position="left">📋 报告</el-divider>
          <div v-if="ovContent" class="report-box">
            <div class="report-toolbar">
              <el-button size="small" type="primary" plain @click="copyReport">📋 复制报告</el-button>
            </div>
            <div class="report-text">{{ ovContent }}</div>
          </div>
          <el-empty v-else description="选择文献后生成综合阅读报告" :image-size="80" />
          <template v-if="reports.length">
            <el-divider content-position="left">历史报告</el-divider>
            <div v-for="r in reports" :key="r.id" class="report-item" @click="ovContent = r.content">
              <el-tag size="small" type="info">{{ r.created_at?.slice(0, 16) }}</el-tag>
              <span class="report-preview">{{ r.content?.slice(0, 60) }}…</span>
            </div>
          </template>
        </el-card>
      </el-tab-pane>

      <!-- ===== 思维训练 ===== -->
      <el-tab-pane label="💬 思维训练" name="train">
        <el-card shadow="never">
          <template v-if="!sessionId">
            <el-form label-width="90px" style="max-width: 640px">
              <el-form-item label="训练文献">
                <el-select v-model="trBooks" multiple collapse-tags placeholder="选择文献（不选=全部）" style="width: 100%">
                  <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="训练模式">
                <el-radio-group v-model="trMode">
                  <el-radio value="quiz">📝 出题训练（概念→应用→批判→跨书）</el-radio>
                  <el-radio value="free">🗣 自由陪练（像老师一样对话）</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="训练主题">
                <el-input v-model="trTopic" placeholder="可选，如：公共管理的核心职能" style="width: 100%" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="starting" @click="startTrain">开始训练</el-button>
              </el-form-item>
            </el-form>
            <el-alert type="info" :closable="false" show-icon
              title="AI 会基于文献内容持续出题/追问，答完一轮点评并进入下一轮，6 轮后给出总结评价" />
          </template>

          <template v-else>
            <div class="train-header">
              <el-tag size="small" type="warning">{{ trMode === 'quiz' ? '出题训练' : '自由陪练' }}</el-tag>
              <span class="round-info">第 {{ trainRound }} / 6 轮</span>
              <el-button size="small" type="danger" plain @click="endTrain">结束训练</el-button>
            </div>
            <div ref="trainBox" class="train-box">
              <div v-for="(m, i) in trainMsgs" :key="i" :class="['train-msg', m.role]">
                <div class="train-label">{{ m.role === 'user' ? '我' : 'AI' }}</div>
                <div class="train-content" :class="{ loading: m.loading }">{{ m.content }}</div>
              </div>
              <div v-if="trainLoading" class="train-msg ai">
                <div class="train-label">AI</div>
                <div class="train-content loading">思考中…</div>
              </div>
            </div>
            <div class="train-input">
              <el-input v-model="trainAnswer" type="textarea" :rows="2"
                placeholder="输入你的回答，回车发送（Shift+Enter 换行）"
                @keydown.enter.exact.prevent="sendAnswer" />
              <el-button type="primary" :loading="trainLoading" @click="sendAnswer" style="margin-left: 8px">发送</el-button>
            </div>
          </template>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listBooks, getTask, studyOverview, studyTrainStart, studyTrainAsk } from '../api'

const tab = ref('overview')
const books = ref([])
const ovBooks = ref([])
const ovLoading = ref(false)
const ovProgress = ref(0)
const ovStage = ref('')
const ovContent = ref('')
const reports = ref([])

const trBooks = ref([])
const trMode = ref('quiz')
const trTopic = ref('')
const starting = ref(false)
const sessionId = ref('')
const trainMsgs = ref([])
const trainAnswer = ref('')
const trainLoading = ref(false)
const trainRound = ref(0)
const trainBox = ref(null)

const scrollTrain = () => {
  nextTick(() => { if (trainBox.value) trainBox.value.scrollTop = trainBox.value.scrollHeight })
}

const copyReport = () => {
  navigator.clipboard?.writeText(ovContent.value).then(() => ElMessage.success('已复制')).catch(() => {})
}

const genOverview = async () => {
  ovLoading.value = true
  ovProgress.value = 0
  ovStage.value = '提交任务…'
  try {
    const resp = await studyOverview({ book_ids: ovBooks.value.length ? ovBooks.value : null })
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      const t = await getTask(resp.task_id)
      ovProgress.value = Math.round((t.progress || 0) * 100)
      ovStage.value = t.message || t.stage || ''
      if (t.status === 'done') {
        ElMessage.success('综合阅读完成')
        await loadReports()
        if (reports.value.length) ovContent.value = reports.value[0].content
        break
      }
      if (t.status === 'failed') {
        ElMessage.error('生成失败：' + (t.error || ''))
        break
      }
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    ovLoading.value = false
  }
}

const loadReports = async () => {
  try { reports.value = await studyReports() } catch {}
}

const startTrain = async () => {
  starting.value = true
  try {
    const resp = await studyTrainStart({
      book_ids: trBooks.value.length ? trBooks.value : null,
      mode: trMode.value,
      topic: trTopic.value,
    })
    sessionId.value = resp.session_id
    trainMsgs.value = [{ role: 'assistant', content: resp.message }]
    trainRound.value = resp.round
    scrollTrain()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    starting.value = false
  }
}

const sendAnswer = async () => {
  const a = trainAnswer.value.trim()
  if (!a || trainLoading.value) return
  trainMsgs.value.push({ role: 'user', content: a })
  trainAnswer.value = ''
  trainLoading.value = true
  scrollTrain()
  try {
    const resp = await studyTrainAsk({ session_id: sessionId.value, answer: a })
    trainMsgs.value.push({ role: 'assistant', content: resp.message })
    trainRound.value = resp.round
    if (resp.done) {
      ElMessage.success('训练结束，AI 已给出总结评价')
    }
  } catch (e) {
    trainMsgs.value.push({ role: 'assistant', content: '⚠️ ' + e.message })
  } finally {
    trainLoading.value = false
    scrollTrain()
  }
}

const endTrain = () => {
  sessionId.value = ''
  trainMsgs.value = []
  trainRound.value = 0
}

onMounted(async () => {
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items.filter((b) => b.status === 'ready')
  } catch { /* ignore */ }
  loadReports()
})
</script>

<style scoped>
.ov-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tip { font-size: 12px; color: var(--el-text-color-secondary); }
.ov-progress { margin: 12px 0; }
.report-box { background: var(--el-fill-color-lighter); border-radius: 8px; padding: 16px; max-height: 560px; overflow-y: auto; }
.report-toolbar { text-align: right; margin-bottom: 8px; }
.report-text { font-size: 14px; line-height: 1.9; white-space: pre-wrap; }
.report-item { padding: 8px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; display: flex; gap: 8px; align-items: center; }
.report-item:hover { background: var(--el-fill-color-lighter); }
.report-preview { font-size: 12px; color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.train-header { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.round-info { font-size: 13px; color: var(--el-text-color-secondary); }
.train-box { height: 420px; overflow-y: auto; padding: 8px; background: var(--el-fill-color-lighter); border-radius: 8px; }
.train-msg { display: flex; gap: 8px; margin-bottom: 14px; }
.train-msg.user { flex-direction: row-reverse; }
.train-label { width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; color: #fff; background: var(--el-color-primary); }
.train-msg.user .train-label { background: var(--el-color-success); }
.train-content { max-width: 75%; padding: 10px 12px; border-radius: 8px; background: #fff; font-size: 14px; line-height: 1.7; white-space: pre-wrap; }
.train-msg.user .train-content { background: var(--el-color-primary-light-9); }
.train-content.loading { color: var(--el-text-color-placeholder); font-style: italic; }
.train-input { display: flex; align-items: flex-end; margin-top: 12px; }
</style>
