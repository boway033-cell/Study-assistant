<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>刷题自测（AI 分析教材生成）</span>
          <div>
            <el-button type="warning" plain @click="openGen">🤖 AI 生成题目</el-button>
            <el-button type="danger" plain :disabled="!filterBook" @click="clearBook">🗑 清除本书题目</el-button>
            <el-select v-model="filterBook" placeholder="全部书籍" clearable style="width: 160px; margin: 0 8px" @change="loadQuizzes">
              <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <el-radio-group v-model="filterType" size="small" @change="loadQuizzes">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="choice">选择</el-radio-button>
              <el-radio-button value="blank">填空</el-radio-button>
              <el-radio-button value="short">简答</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <div v-if="!currentQuiz" class="quiz-list">
        <el-alert v-if="!quizzes.length" type="info" :closable="false" show-icon
          title="暂无题目：点击右上角「AI 生成题目」，选择书籍后让 DeepSeek 分析教材内容自动生成"
          style="margin-bottom: 12px" />
        <el-table :data="quizzes" v-loading="loading" empty-text="暂无题目（点右上角 AI 生成）">
          <el-table-column prop="question" label="题目" min-width="200" show-overflow-tooltip />
          <el-table-column prop="q_type" label="题型" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="typeTag(row.q_type)">{{ typeName(row.q_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="book_title" label="来源" width="150" show-overflow-tooltip />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="startQuiz(row)">作答</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-else class="quiz-playing">
        <div class="quiz-meta">
          <el-tag size="small">{{ typeName(currentQuiz.q_type) }}</el-tag>
          <span class="quiz-source">《{{ currentQuiz.book_title }}》{{ currentQuiz.chapter_title || '' }}</span>
          <el-button link type="info" size="small" @click="currentQuiz = null">退出</el-button>
        </div>
        <div class="quiz-question">{{ currentQuiz.question }}</div>

        <div v-if="currentQuiz.q_type === 'choice'" class="choice-list">
          <div
            v-for="(opt, i) in currentQuiz.options"
            :key="i"
            class="choice-item"
            :class="{ selected: userAnswer === opt[0], correct: answered && opt[0] === result?.answer, wrong: answered && userAnswer === opt[0] && userAnswer !== result?.answer }"
            @click="selectChoice(opt[0])"
          >{{ opt }}</div>
        </div>

        <div v-else>
          <el-input
            v-model="userAnswer"
            type="textarea"
            :rows="3"
            :placeholder="currentQuiz.q_type === 'blank' ? '输入答案' : '输入你的回答，提交后对照参考答案自评'"
          />
        </div>

        <div class="quiz-actions">
          <el-button
            v-if="!answered"
            type="primary"
            :disabled="!userAnswer.trim()"
            @click="submitAnswer"
          >提交答案</el-button>
          <template v-else>
            <div class="result-box" :class="result?.is_correct ? 'ok' : 'no'">
              <div v-if="currentQuiz.q_type !== 'short'">{{ result?.is_correct ? '✅ 回答正确' : '❌ 回答错误' }}</div>
              <div class="result-answer">参考答案：{{ result?.answer }}</div>
              <div v-if="result?.explanation" class="result-explanation">解析：{{ result?.explanation }}</div>
            </div>
            <el-button v-if="currentQuiz.q_type === 'short'" type="primary" @click="submitSelfGrade(true)">我答对了</el-button>
            <el-button v-if="currentQuiz.q_type === 'short'" type="danger" @click="submitSelfGrade(false)">我答错了</el-button>
            <el-button @click="nextQuiz">下一题</el-button>
          </template>
        </div>
      </div>
    </el-card>

    <!-- AI 生成对话框 -->
    <el-dialog v-model="showGen" title="🤖 AI 生成题目（DeepSeek 分析教材内容）" width="500px">
      <el-form label-width="90px">
        <el-form-item label="选择书籍">
          <el-select v-model="genBook" placeholder="选择已解析完成的书籍" style="width: 100%" @change="onGenBook">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="生成章节">
          <el-select v-model="genChapter" placeholder="全部章节（默认）" clearable style="width: 100%">
            <el-option v-for="c in genChapters" :key="c.id" :label="c.title" :value="c.id" />
          </el-select>
          <div class="form-tip">不选 = 全书所有章节；每题由 AI 依据对应章节原文生成，选择/简答各 5 道</div>
        </el-form-item>
        <div v-if="genRunning" class="gen-progress">
          <el-progress :percentage="genProgress" :indeterminate="genProgress === 0" />
          <div class="form-tip">🤖 {{ genStage }}（DeepSeek 正在分析教材原文）</div>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="showGen = false" :disabled="genRunning">关闭</el-button>
        <el-button type="warning" :loading="genRunning" @click="doGenerate">开始生成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listQuizzes, attemptQuiz, selfGrade, listBooks, getBook, generateQuizzes, getTask, clearBookQuizzes } from '../api'

const quizzes = ref([])
const loading = ref(false)
const books = ref([])
const filterBook = ref(null)
const filterType = ref('')
const currentQuiz = ref(null)
const userAnswer = ref('')
const answered = ref(false)
const result = ref(null)
const showGen = ref(false)
const genBook = ref(null)
const genChapter = ref(null)
const genChapters = ref([])
const genRunning = ref(false)
const genProgress = ref(0)
const genStage = ref('')

const typeName = (t) => ({ choice: '选择', blank: '填空', short: '简答' }[t] || t)
const typeTag = (t) => ({ choice: 'primary', blank: 'warning', short: 'success' }[t] || 'info')

const loadQuizzes = async () => {
  loading.value = true
  try {
    const resp = await listQuizzes({
      book_id: filterBook.value || undefined,
      q_type: filterType.value || undefined,
      page_size: 50,
    })
    quizzes.value = resp.items
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

const clearBook = async () => {
  if (!filterBook.value) { ElMessage.warning('请先选择要清除的书籍'); return }
  try {
    await ElMessageBox.confirm(`确定删除《${books.value.find(b => b.id === filterBook.value)?.title || ''}》的全部题目？`, '清除确认', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    await clearBookQuizzes(filterBook.value)
    ElMessage.success('已清除')
    loadQuizzes()
  } catch { /* 取消 */ }
}

const openGen = () => {
  if (!books.value.length) {
    ElMessage.warning('还没有可用的书籍，请先到资料库上传教材')
    return
  }
  showGen.value = true
  genChapter.value = null
  genChapters.value = []
  genProgress.value = 0
}

const onGenBook = async () => {
  genChapter.value = null
  genChapters.value = []
  if (!genBook.value) return
  try {
    const detail = await getBook(genBook.value)
    const flat = []
    const walk = (nodes, depth) => {
      for (const n of nodes) {
        flat.push({ id: n.id, title: '　'.repeat(depth) + n.title })
        if (n.children?.length) walk(n.children, depth + 1)
      }
    }
    walk(detail.chapters || [], 0)
    genChapters.value = flat
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const doGenerate = async () => {
  if (!genBook.value) {
    ElMessage.warning('请选择书籍')
    return
  }
  genRunning.value = true
  genProgress.value = 0
  genStage.value = '提交任务…'
  try {
    const resp = await generateQuizzes(genBook.value, {
      chapter_ids: genChapter.value ? [genChapter.value] : [],
    })
    genStage.value = 'AI 分析教材中…'
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const t = await getTask(resp.task_id)
      genProgress.value = Math.round((t.progress || 0) * 100)
      genStage.value = t.stage === '生成题目' ? (t.message || '生成题目中…') : (t.message || t.stage || '')
      if (t.status === 'done') {
        ElMessage.success(`生成完成，共 ${t.result?.generated || 0} 道题`)
        genRunning.value = false
        showGen.value = false
        loadQuizzes()
        return
      }
      if (t.status === 'failed') {
        ElMessage.error('生成失败：' + (t.message || t.error || ''))
        genRunning.value = false
        return
      }
    }
    ElMessage.warning('生成超时，请稍后刷新查看')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    genRunning.value = false
  }
}

const startQuiz = (q) => {
  currentQuiz.value = q
  userAnswer.value = ''
  answered.value = false
  result.value = null
}

const selectChoice = (letter) => {
  if (answered.value) return
  userAnswer.value = letter
}

const submitAnswer = async () => {
  try {
    result.value = await attemptQuiz(currentQuiz.value.id, userAnswer.value)
    answered.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const submitSelfGrade = async (correct) => {
  try {
    result.value = await selfGrade(currentQuiz.value.id, correct)
    answered.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const nextQuiz = () => {
  const idx = quizzes.value.findIndex((q) => q.id === currentQuiz.value.id)
  if (idx >= 0 && idx < quizzes.value.length - 1) {
    startQuiz(quizzes.value[idx + 1])
  } else {
    currentQuiz.value = null
    ElMessage.success('本组题目已完成')
    loadQuizzes()
  }
}

onMounted(async () => {
  loadQuizzes()
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items.filter((b) => b.status === 'ready')
  } catch { /* ignore */ }
})
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.quiz-playing { padding: 8px 0; }
.quiz-meta { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; }
.quiz-source { color: var(--el-text-color-secondary); font-size: 13px; }
.quiz-question { font-size: 17px; font-weight: 600; margin-bottom: 20px; line-height: 1.7; }
.choice-item {
  padding: 12px 16px; border: 1px solid var(--el-border-color-light); border-radius: 8px;
  margin-bottom: 8px; cursor: pointer; transition: all 0.2s;
}
.choice-item:hover { border-color: var(--el-color-primary); }
.choice-item.selected { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.choice-item.correct { border-color: var(--el-color-success); background: var(--el-color-success-light-9); }
.choice-item.wrong { border-color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.quiz-actions { margin-top: 20px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.result-box { padding: 12px 16px; border-radius: 8px; margin-right: 8px; }
.result-box.ok { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.result-box.no { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.result-answer { font-size: 14px; margin-top: 4px; }
.result-explanation { font-size: 13px; color: var(--el-text-color-regular); margin-top: 4px; }
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.gen-progress { margin-top: 8px; }
</style>