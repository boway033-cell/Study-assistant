<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="8">
        <el-card shadow="never">
          <template #header>总览</template>
          <div class="stat-grid">
            <el-statistic title="书籍" :value="overview.book_count" />
            <el-statistic title="卡片" :value="overview.card_count" />
            <el-statistic title="今日到期" :value="overview.due_today" />
            <el-statistic title="累计复习" :value="overview.reviews_done" />
            <el-statistic title="题目" :value="overview.quiz_count" />
            <el-statistic title="平均掌握度" :value="overview.avg_mastery" :precision="2" />
          </div>
        </el-card>
        <el-card shadow="never" style="margin-top: 16px">
          <template #header>薄弱章节 TOP10</template>
          <el-table :data="weakness" size="small" empty-text="暂无数据">
            <el-table-column prop="book_title" label="书" min-width="100" show-overflow-tooltip />
            <el-table-column prop="chapter_title" label="章节" min-width="120" show-overflow-tooltip />
            <el-table-column label="掌握度" width="80">
              <template #default="{ row }">
                <el-progress :percentage="Math.round(row.mastery * 100)" :status="row.mastery < 0.5 ? 'exception' : 'success'" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card shadow="never">
          <template #header>近 30 天复习趋势</template>
          <div ref="trendChart" style="height: 300px" />
        </el-card>
        <el-card shadow="never" style="margin-top: 16px">
          <template #header>
            <div class="card-header">
              <span>章节掌握度</span>
              <el-select v-model="masteryBook" placeholder="选择书籍" clearable style="width: 200px" @change="loadMastery">
                <el-option v-for="b in books" :key="b.id" :label="b.title" :value="b.id" />
              </el-select>
            </div>
          </template>
          <div v-if="masteryBook" ref="masteryChart" style="height: 300px" />
          <el-empty v-else description="请选择书籍查看章节掌握度" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { getOverview, getReviewHistory, getWeakness, getMastery, listBooks } from '../api'

const overview = ref({})
const weakness = ref([])
const reviewHistory = ref([])
const books = ref([])
const masteryBook = ref(null)
const masteryData = ref([])
const trendChart = ref(null)
const masteryChart = ref(null)
let trendInstance = null
let masteryInstance = null

const loadAll = async () => {
  try {
    overview.value = await getOverview()
  } catch { /* ignore */ }
  try {
    weakness.value = (await getWeakness()).items
  } catch { /* ignore */ }
  try {
    reviewHistory.value = (await getReviewHistory(30)).daily
    renderTrend()
  } catch { /* ignore */ }
}

const renderTrend = () => {
  if (!trendChart.value) return
  trendInstance = trendInstance || echarts.init(trendChart.value)
  trendInstance.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: reviewHistory.value.map((d) => d.date.slice(5)) },
    yAxis: { type: 'value' },
    series: [
      {
        name: '复习数', type: 'line', smooth: true, areaStyle: {},
        data: reviewHistory.value.map((d) => d.reviews),
      },
    ],
  })
}

const loadMastery = async () => {
  if (!masteryBook.value) return
  try {
    masteryData.value = (await getMastery(masteryBook.value)).chapters
    renderMastery()
  } catch { /* ignore */ }
}

const renderMastery = () => {
  if (!masteryChart.value) return
  masteryInstance = masteryInstance || echarts.init(masteryChart.value)
  masteryInstance.setOption({
    tooltip: {},
    grid: { left: 40, right: 20, top: 30, bottom: 80 },
    xAxis: {
      type: 'category',
      data: masteryData.value.map((c) => c.title),
      axisLabel: { rotate: 30, fontSize: 11 },
    },
    yAxis: { type: 'value', max: 1 },
    series: [
      {
        name: '掌握度', type: 'bar', barWidth: '50%',
        itemStyle: {
          color: (p) => (p.value >= 0.7 ? '#5f9b8f' : p.value >= 0.4 ? '#c99a5b' : '#c45656'),
        },
        data: masteryData.value.map((c) => c.mastery),
      },
    ],
  })
}

const resize = () => {
  trendInstance?.resize()
  masteryInstance?.resize()
}

onMounted(async () => {
  loadAll()
  try {
    const resp = await listBooks({ page_size: 100 })
    books.value = resp.items
  } catch { /* ignore */ }
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  trendInstance?.dispose()
  masteryInstance?.dispose()
})
</script>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
