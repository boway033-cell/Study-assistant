<template>
  <div class="graph-page">
    <div class="graph-main">
      <el-card shadow="never">
        <template #header>
          <div class="graph-header">
            <span>📊 知识图谱（{{ nodes.length }} 个概念 · {{ edges.length }} 条关联）</span>
            <el-button size="small" @click="loadGraph">刷新</el-button>
          </div>
        </template>
        <div ref="chart" class="chart-box" v-loading="loading"></div>
      </el-card>
    </div>
    <div class="graph-side">
      <el-card shadow="never">
        <template #header>
          <span v-if="selectedConcept">🔍 「{{ selectedConcept }}」的出处</span>
          <span v-else>点击左侧概念查看出处</span>
        </template>
        <div v-loading="loadingSources" class="source-list">
          <el-empty v-if="!selectedConcept" description="点击图谱中的概念节点" :image-size="80" />
          <el-empty v-else-if="!sources.length && !loadingSources" description="无匹配出处" :image-size="80" />
          <div v-for="s in sources" :key="s.chunk_id" class="source-item">
            <div class="source-meta">
              <el-tag size="small">{{ s.book_title }}</el-tag>
              <el-tag size="small" type="warning" v-if="s.chapter_title">{{ s.chapter_title }}</el-tag>
              <el-tag size="small" type="success" v-if="s.page">第 {{ s.page }} 页</el-tag>
            </div>
            <div class="source-snippet" v-html="sanitizeHtml(s.snippet)"></div>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { getGraph, getConceptSources } from '../api'
import { sanitizeHtml } from '../utils/markdown'

const loading = ref(false)
const loadingSources = ref(false)
const nodes = ref([])
const edges = ref([])
const selectedConcept = ref('')
const sources = ref([])
const chart = ref(null)
let chartInstance = null

const loadGraph = async () => {
  loading.value = true
  try {
    const data = await getGraph()
    nodes.value = data.nodes
    edges.value = data.edges
    await nextTick()
    renderChart()
  } catch (e) { /* ignore */ } finally {
    loading.value = false
  }
}

const renderChart = () => {
  if (!chart.value) return
  chartInstance = chartInstance || echarts.init(chart.value)
  const gNodes = nodes.value.map(n => ({
    name: n.name,
    symbolSize: Math.max(12, Math.min(42, 12 + n.count * 4)),
  }))
  const gEdges = edges.value.map(e => ({
    source: e.source, target: e.target,
    lineStyle: { width: Math.min(3, 0.5 + e.weight * 0.5) },
  }))
  chartInstance.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p) => (p.dataType === 'node' ? p.name : ''),
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      data: gNodes,
      edges: gEdges,
      force: { repulsion: 120, edgeLength: 70, gravity: 0.1 },
      label: { show: true, fontSize: 11, color: '#8B5A2B', position: 'right' },
      itemStyle: { color: '#2E4042', borderColor: '#F5F0E8', borderWidth: 1 },
      lineStyle: { color: '#C2A285', opacity: 0.35, curveness: 0.1 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3, opacity: 0.8 } },
    }],
  })
  chartInstance.off('click')
  chartInstance.on('click', (p) => { if (p.dataType === 'node') selectConcept(p.name) })
}

const selectConcept = async (name) => {
  selectedConcept.value = name
  sources.value = []
  loadingSources.value = true
  try {
    const data = await getConceptSources(name)
    sources.value = data.items || []
  } catch (e) {
    sources.value = []
  } finally {
    loadingSources.value = false
  }
}

const resize = () => { if (chartInstance) chartInstance.resize() }

onMounted(() => { loadGraph(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); if (chartInstance) chartInstance.dispose() })
</script>

<style scoped>
.graph-page { display: flex; gap: 16px; height: calc(100vh - 150px); }
.graph-main { flex: 1; min-width: 0; }
.graph-side { width: 400px; }
.chart-box { height: calc(100vh - 240px); }
.source-list { max-height: calc(100vh - 300px); overflow-y: auto; }
.source-item { padding: 10px; border-bottom: 1px solid var(--el-border-color-extra-light); }
.source-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.source-snippet { font-size: 13px; line-height: 1.7; color: var(--el-text-color-primary); }
.graph-header { display: flex; justify-content: space-between; align-items: center; }
</style>
