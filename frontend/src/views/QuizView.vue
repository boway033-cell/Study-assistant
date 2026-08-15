<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>刷题自测</span>
          <div>
            <el-select v-model="filterBook" placeholder="全部书籍" clearable style="width: 160px; margin-right: 8px" @change="loadQuizzes">
              <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <el-radio-group v-model="filterType" size="small" @change="loadQuizzes">
              <el-radio-button label="">全部</el-radio-button>
              <el-radio-button label="choice">选择</el-radio-button>
              <el-radio-button label="blank">填空</el-radio-button>
              <el-radio-button label="short">简答</el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <div v-if="!currentQuiz" class="quiz-list">
        <el-table :data="quizzes" v-loading="loading" empty-text="暂无题目（可在设置中配置 AI 后生成）">
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

        <!-- 选择题 -->
        <div v-if="currentQuiz.q_type === 'choice'" class="choice-list">
          <div
            v-for="(opt, i) in currentQuiz.options"
            :key="i"
            class="choice-item"
            :class="{ selected: userAnswer === opt[0], correct: answered && opt[0] === result?.answer, wrong: answered && userAnswer === opt[0] && userAnswer !== result?.answer }"
            @click="selectChoice(opt[0])"
          >{{ opt }}</div>
        </div>

        <!-- 填空/简答 -->
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
              <div v-if="currentQuiz.q_type !== 'short'">
                {{ result?.is_correct ? '✅ 回答正确' : '❌ 回答错误' }}
              </div>
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listQuizzes, attemptQuiz, selfGrade, listBooks } from '../api'

const quizzes = ref([])
const loading = ref(false)
const books = ref([])
const filterBook = ref(null)
const filterType = ref('')
const currentQuiz = ref(null)
const userAnswer = ref('')
const answered = ref(false)
const result = ref(null)

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
    books.value = resp.items
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
</style>
