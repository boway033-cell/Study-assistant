<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>今日复习</span>
              <el-button type="success" :disabled="!queue.length || reviewing" @click="startReview">
                {{ queue.length ? `开始复习（${queue.length} 张）` : '暂无待复习卡片' }}
              </el-button>
            </div>
          </template>

          <div v-if="!reviewing" class="queue-info">
            <el-empty v-if="!queue.length" description="今天没有到期的卡片 🎉" />
            <div v-else class="queue-stats">
              <el-statistic title="到期卡片" :value="queue.length" />
            </div>
          </div>

          <div v-else class="card-deck">
            <div class="deck-count">{{ index + 1 }} / {{ deck.length }}</div>
            <div class="flashcard" :class="{ flipped: flipped }" @click="flipped = !flipped">
              <div class="flashcard-inner">
                <div class="flashcard-face front">
                  <div class="face-label">问题</div>
                  <div class="face-text">{{ current.front }}</div>
                  <div class="flip-hint">点击查看答案</div>
                </div>
                <div class="flashcard-face back">
                  <div class="face-label">答案</div>
                  <div class="face-text">{{ current.back }}</div>
                </div>
              </div>
            </div>
            <div v-if="flipped" class="rating-row">
              <el-button type="danger" @click="rate('again')">忘记</el-button>
              <el-button type="warning" @click="rate('hard')">困难</el-button>
              <el-button type="success" @click="rate('good')">良好</el-button>
              <el-button type="primary" @click="rate('easy')">简单</el-button>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never">
          <template #header>卡片管理</template>
          <el-form inline>
            <el-select v-model="filterBook" placeholder="全部书籍" clearable style="width: 140px" @change="loadCards">
              <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
            </el-select>
            <el-button type="primary" plain @click="showCreate = true">新建卡片</el-button>
          </el-form>
          <el-table :data="cards" size="small" max-height="420" empty-text="暂无卡片">
            <el-table-column prop="front" label="问题" min-width="160" show-overflow-tooltip />
            <el-table-column prop="state" label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="stateTag(row.state)">{{ row.state }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reps" label="复习" width="60" />
            <el-table-column label="操作" width="70">
              <template #default="{ row }">
                <el-popconfirm title="删除这张卡片？" @confirm="removeCard(row)">
                  <template #reference>
                    <el-button link type="danger" size="small">删</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="showCreate" title="新建卡片" width="500px">
      <el-form label-width="60px">
        <el-form-item label="书籍">
          <el-select v-model="createForm.book_id" style="width: 100%">
            <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="问题"><el-input v-model="createForm.front" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="答案"><el-input v-model="createForm.back" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="createForm.tags" placeholder="逗号分隔，如：高频,易错" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="saveCard">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { reviewQueue, reviewCard, listCards, deleteCard, createCard, listBooks } from '../api'

const queue = ref([])
const deck = ref([])
const index = ref(0)
const flipped = ref(false)
const reviewing = ref(false)
const cards = ref([])
const books = ref([])
const filterBook = ref(null)
const showCreate = ref(false)
const createForm = ref({ book_id: null, front: '', back: '', tags: '' })

const current = computed(() => deck.value[index.value] || {})

const loadQueue = async () => {
  try {
    const resp = await reviewQueue()
    queue.value = resp.items
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const startReview = () => {
  deck.value = [...queue.value]
  index.value = 0
  flipped.value = false
  reviewing.value = true
}

const rate = async (rating) => {
  const card = deck.value[index.value]
  try {
    await reviewCard(card.id, rating)
  } catch (e) {
    ElMessage.error(e.message)
  }
  index.value++
  flipped.value = false
  if (index.value >= deck.value.length) {
    reviewing.value = false
    ElMessage.success('今日复习完成！')
    loadQueue()
    loadCards()
  }
}

const loadCards = async () => {
  try {
    const resp = await listCards({ book_id: filterBook.value || undefined })
    cards.value = resp
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const removeCard = async (row) => {
  try {
    await deleteCard(row.id)
    ElMessage.success('已删除')
    loadCards()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const saveCard = async () => {
  if (!createForm.value.book_id || !createForm.value.front || !createForm.value.back) {
    ElMessage.warning('请填写书籍、问题和答案')
    return
  }
  try {
    await createCard(createForm.value)
    ElMessage.success('已创建')
    showCreate.value = false
    createForm.value = { book_id: null, front: '', back: '', tags: '' }
    loadCards()
    loadQueue()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const stateTag = (s) => ({ New: 'info', Learning: 'warning', Review: 'success', Relearning: 'danger' }[s] || 'info')

onMounted(async () => {
  loadQueue()
  loadCards()
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items
  } catch { /* ignore */ }
})
</script>

<style scoped>
.card-header { display: flex; justify-content: space-between; align-items: center; }
.queue-info { min-height: 200px; display: flex; align-items: center; justify-content: center; }
.queue-stats { text-align: center; }
.card-deck { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.deck-count { color: #909399; font-size: 13px; }
.flashcard { width: 100%; height: 300px; perspective: 1000px; cursor: pointer; }
.flashcard-inner { position: relative; width: 100%; height: 100%; transition: transform 0.5s; transform-style: preserve-3d; }
.flashcard.flipped .flashcard-inner { transform: rotateY(180deg); }
.flashcard-face {
  position: absolute; width: 100%; height: 100%; backface-visibility: hidden;
  border-radius: 12px; padding: 24px; display: flex; flex-direction: column;
  align-items: center; justify-content: center; border: 1px solid #e4e7ed;
}
.front { background: #ecf5ff; }
.back { background: #f0f9eb; transform: rotateY(180deg); }
.face-label { font-size: 12px; color: #909399; margin-bottom: 12px; }
.face-text { font-size: 18px; line-height: 1.8; text-align: center; }
.flip-hint { position: absolute; bottom: 16px; font-size: 12px; color: #c0c4cc; }
.rating-row { display: flex; gap: 12px; }
</style>
