<template>
  <div class="draw-page">
    <el-row :gutter="16">
      <!-- Left: AI Agent Panel -->
      <el-col :span="7">
        <el-card shadow="never" class="agent-card">
          <template #header>
            <div class="agent-header">
              <span>AI 智能绘图</span>
              <el-tag size="small" type="success" effect="dark">AI Agent</el-tag>
            </div>
          </template>

          <div class="agent-input-section">
            <el-input
              v-model="description"
              type="textarea"
              :rows="3"
              placeholder="用自然语言描述你想要的图表，如：画一个微服务架构图，包含用户服务、订单服务、支付服务和数据库"
              @keydown.enter.ctrl="generate"
            />
            <div class="agent-options">
              <el-select v-model="model" size="small" style="width: 100px">
                <el-option label="⚡ flash" value="flash" />
                <el-option label="🧠 pro" value="pro" />
              </el-select>
              <el-button type="primary" size="small" :loading="generating" @click="generate" style="margin-left: 8px">
                {{ generating ? '生成中...' : '生成图表' }}
              </el-button>
            </div>
          </div>

          <el-divider content-position="left">💬 对话修改</el-divider>
          <div ref="chatBox" class="chat-box">
            <div v-for="(msg, i) in chatHistory" :key="i" :class="['chat-msg', msg.role]">
              <div class="chat-bubble">{{ msg.content }}</div>
            </div>
            <div v-if="modifying" class="chat-msg ai">
              <div class="chat-bubble loading">AI 正在修改图表...</div>
            </div>
          </div>
          <div class="chat-input">
            <el-input
              v-model="modifyRequest"
              size="small"
              placeholder="描述修改要求，如：把数据库改成红色"
              @keydown.enter="modify"
              :disabled="!sessionId"
            />
            <el-button size="small" type="warning" :loading="modifying" @click="modify" :disabled="!sessionId" style="margin-left: 8px">
              修改
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- Right: Diagram Preview -->
      <el-col :span="17">
        <el-card shadow="never" class="preview-card">
          <template #header>
            <div class="preview-header">
              <span>📊 图表预览</span>
              <div class="preview-actions" v-if="sessionId">
                <el-button size="small" @click="exportXml">⬇ 导出 XML</el-button>
                <el-button size="small" @click="downloadSvg" :disabled="!hasDiagram">⬇ 导出 SVG</el-button>
                <el-button size="small" type="danger" plain @click="clearDiagram">清空</el-button>
              </div>
            </div>
          </template>

          <div v-if="generating" v-loading="true" element-loading-text="AI 正在生成图表..." style="height: 500px" />
          <div v-else-if="hasDiagram" class="diagram-container">
            <div class="diagram-canvas" v-html="renderedSvg"></div>
          </div>
          <el-empty v-else description="在左侧输入描述，AI 将为你生成流程图、架构图或思维导图" :image-size="100">
            <div class="empty-tips">
              <p>💡 支持的图表类型：</p>
              <el-tag size="small" type="info" style="margin: 2px">流程图</el-tag>
              <el-tag size="small" type="success" style="margin: 2px">架构图</el-tag>
              <el-tag size="small" type="warning" style="margin: 2px">思维导图</el-tag>
              <el-tag size="small" type="danger" style="margin: 2px">ER 图</el-tag>
              <el-tag size="small" style="margin: 2px">组织架构</el-tag>
            </div>
          </el-empty>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { drawGenerate, drawModify } from '../api'

const description = ref('')
const model = ref('flash')
const generating = ref(false)
const modifying = ref(false)
const sessionId = ref('')
const currentXml = ref('')
const chatHistory = ref([])
const modifyRequest = ref('')
const chatBox = ref(null)

const hasDiagram = computed(() => !!currentXml.value && !!sessionId.value)
const renderedSvg = computed(() => {
  if (!currentXml.value) return ''
  return xmlToSvg(currentXml.value)
})

const generate = async () => {
  if (!description.value.trim()) return
  generating.value = true
  chatHistory.value = [{ role: 'user', content: description.value }]
  try {
    const resp = await drawGenerate({ description: description.value, model: model.value })
    sessionId.value = resp.session_id
    currentXml.value = resp.xml
    chatHistory.value.push({ role: 'ai', content: '图表已生成！你可以在下方继续描述修改要求。' })
    ElMessage.success('图表已生成')
  } catch (e) {
    ElMessage.error(e.message)
    chatHistory.value.push({ role: 'ai', content: '生成失败：' + e.message })
  } finally {
    generating.value = false
    scrollChat()
  }
}

const modify = async () => {
  if (!modifyRequest.value.trim() || !sessionId.value) return
  modifying.value = true
  chatHistory.value.push({ role: 'user', content: modifyRequest.value })
  try {
    const resp = await drawModify({ session_id: sessionId.value, request: modifyRequest.value })
    currentXml.value = resp.xml
    chatHistory.value.push({ role: 'ai', content: '图表已更新！' })
    modifyRequest.value = ''
    ElMessage.success('图表已更新')
  } catch (e) {
    ElMessage.error(e.message)
    chatHistory.value.push({ role: 'ai', content: '修改失败：' + e.message })
  } finally {
    modifying.value = false
    scrollChat()
  }
}

const scrollChat = () => {
  nextTick(() => {
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  })
}

const exportXml = () => {
  const blob = new Blob([currentXml.value], { type: 'application/xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'diagram.drawio.xml'
  a.click()
  URL.revokeObjectURL(url)
}

const downloadSvg = () => {
  const blob = new Blob([renderedSvg.value], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'diagram.svg'
  a.click()
  URL.revokeObjectURL(url)
}

const clearDiagram = () => {
  sessionId.value = ''
  currentXml.value = ''
  chatHistory.value = []
  description.value = ''
}

// Parse draw.io mxGraph XML and render as SVG
function xmlToSvg(xml) {
  try {
    const parser = new DOMParser()
    const doc = parser.parseFromString(xml, 'text/xml')
    const model = doc.querySelector('mxGraphModel')
    if (!model) return '<div style="padding:20px;color:#999">XML 解析失败：未找到 mxGraphModel</div>'

    const cells = model.querySelectorAll('mxCell')
    const nodes = []
    const edges = []
    let maxX = 850, maxY = 600

    cells.forEach(cell => {
      const id = cell.getAttribute('id')
      if (id === '0' || id === '1') return

      const vertex = cell.getAttribute('vertex')
      const edge = cell.getAttribute('edge')
      const value = cell.getAttribute('value') || ''
      const style = cell.getAttribute('style') || ''
      const geom = cell.querySelector('mxGeometry')

      if (vertex === '1' && geom) {
        const x = parseFloat(geom.getAttribute('x') || 0)
        const y = parseFloat(geom.getAttribute('y') || 0)
        const w = parseFloat(geom.getAttribute('width') || 120)
        const h = parseFloat(geom.getAttribute('height') || 60)
        maxX = Math.max(maxX, x + w + 20)
        maxY = Math.max(maxY, y + h + 20)
        nodes.push({ id, value, style, x, y, w, h })
      } else if (edge === '1') {
        const source = cell.getAttribute('source')
        const target = cell.getAttribute('target')
        edges.push({ id, value, source, target, style })
      }
    })

    // Build SVG
    const svgParts = ['<svg xmlns="http://www.w3.org/2000/svg" width="' + Math.min(maxX, 1200) + '" height="' + Math.min(maxY, 800) + '" style="background:#fafafa;border-radius:8px">']

    // Draw edges first (behind nodes)
    const nodeMap = {}
    nodes.forEach(n => { nodeMap[n.id] = n })

    edges.forEach(e => {
      const s = nodeMap[e.source]
      const t = nodeMap[e.target]
      if (!s || !t) return
      const sx = s.x + s.w / 2
      const sy = s.y + s.h / 2
      const tx = t.x + t.w / 2
      const ty = t.y + t.h / 2
      // Draw line with arrow
      svgParts.push('<line x1="' + sx + '" y1="' + sy + '" x2="' + tx + '" y2="' + ty + '" stroke="#666" stroke-width="1.5" marker-end="url(#arrowhead)" />')
      // Edge label
      if (e.value) {
        const mx = (sx + tx) / 2
        const my = (sy + ty) / 2
        svgParts.push('<rect x="' + (mx - e.value.length * 5) + '" y="' + (my - 10) + '" width="' + (e.value.length * 10) + '" height="20" fill="#fff" stroke="#ddd" rx="4" />')
        svgParts.push('<text x="' + mx + '" y="' + (my + 4) + '" text-anchor="middle" font-size="11" fill="#444">' + escapeXml(e.value) + '</text>')
      }
    })

    // Draw nodes
    nodes.forEach(n => {
      const fill = getStyleColor(n.style, 'fillColor', '#dae8fc')
      const stroke = getStyleColor(n.style, 'strokeColor', '#6c8ebf')
      const isRounded = n.style.includes('rounded=1')
      const isRhombus = n.style.includes('rhombus')
      const isCylinder = n.style.includes('cylinder')
      const isEllipse = n.style.includes('ellipse')
      const isCloud = n.style.includes('cloud')
      const isActor = n.style.includes('actor')

      if (isRhombus) {
        // Diamond
        const cx = n.x + n.w / 2
        const cy = n.y + n.h / 2
        svgParts.push('<polygon points="' + cx + ',' + n.y + ' ' + (n.x + n.w) + ',' + cy + ' ' + cx + ',' + (n.y + n.h) + ' ' + n.x + ',' + cy + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
      } else if (isEllipse) {
        svgParts.push('<ellipse cx="' + (n.x + n.w / 2) + '" cy="' + (n.y + n.h / 2) + '" rx="' + (n.w / 2) + '" ry="' + (n.h / 2) + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
      } else if (isCylinder) {
        svgParts.push('<path d="M' + n.x + ',' + (n.y + 15) + ' a' + (n.w / 2) + ',15 0 0 0 ' + n.w + ',0 v' + (n.h - 30) + ' a' + (n.w / 2) + ',15 0 0 1 -' + n.w + ',0 z" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
        svgParts.push('<ellipse cx="' + (n.x + n.w / 2) + '" cy="' + (n.y + 15) + '" rx="' + (n.w / 2) + '" ry="15" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
      } else if (isActor) {
        // Actor shape (simplified)
        const cx = n.x + n.w / 2
        svgParts.push('<circle cx="' + cx + '" cy="' + (n.y + 15) + '" r="12" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
        svgParts.push('<line x1="' + cx + '" y1="' + (n.y + 27) + '" x2="' + cx + '" y2="' + (n.y + n.h - 10) + '" stroke="' + stroke + '" stroke-width="2" />')
        svgParts.push('<line x1="' + (n.x + 10) + '" y1="' + (n.y + 40) + '" x2="' + (n.x + n.w - 10) + '" y2="' + (n.y + 40) + '" stroke="' + stroke + '" stroke-width="2" />')
        svgParts.push('<line x1="' + cx + '" y1="' + (n.y + n.h - 10) + '" x2="' + (n.x + 15) + '" y2="' + (n.y + n.h) + '" stroke="' + stroke + '" stroke-width="2" />')
        svgParts.push('<line x1="' + cx + '" y1="' + (n.y + n.h - 10) + '" x2="' + (n.x + n.w - 15) + '" y2="' + (n.y + n.h) + '" stroke="' + stroke + '" stroke-width="2" />')
      } else if (isCloud) {
        svgParts.push('<ellipse cx="' + (n.x + n.w / 2) + '" cy="' + (n.y + n.h / 2) + '" rx="' + (n.w / 2) + '" ry="' + (n.h / 2) + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" opacity="0.8" />')
      } else {
        // Rectangle (rounded or sharp)
        const rx = isRounded ? '12' : '4'
        svgParts.push('<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="' + rx + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="2" />')
      }

      // Node text (multi-line support)
      const lines = n.value.split('\n')
      const textY = n.y + n.h / 2 - (lines.length - 1) * 8
      lines.forEach((line, i) => {
        svgParts.push('<text x="' + (n.x + n.w / 2) + '" y="' + (textY + i * 16) + '" text-anchor="middle" font-size="13" fill="#333" font-family="sans-serif">' + escapeXml(line) + '</text>')
      })
    })

    // Arrow marker definition
    svgParts.unshift('<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#666" /></marker></defs>')

    svgParts.push('</svg>')
    return svgParts.join('')
  } catch (e) {
    return '<div style="padding:20px;color:#c00">XML 渲染错误：' + e.message + '</div>'
  }
}

function getStyleColor(style, key, fallback) {
  const match = style.match(new RegExp(key + '=([^;]+)'))
  return match ? match[1] : fallback
}

function escapeXml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
</script>

<style scoped>
.draw-page { height: calc(100vh - 120px); }
.agent-card { height: 100%; display: flex; flex-direction: column; }
.agent-header { display: flex; align-items: center; gap: 8px; }
.agent-input-section { margin-bottom: 8px; }
.agent-options { display: flex; align-items: center; margin-top: 8px; }
.chat-box {
  flex: 1; overflow-y: auto; margin-bottom: 8px;
  background: var(--el-fill-color-lighter); border-radius: 8px; padding: 8px;
  min-height: 200px; max-height: 350px;
}
.chat-msg { margin-bottom: 10px; }
.chat-msg.user { text-align: right; }
.chat-bubble {
  display: inline-block; max-width: 90%; padding: 8px 12px;
  border-radius: 10px; font-size: 13px; line-height: 1.6;
  background: var(--el-color-primary-light-9); color: var(--el-text-color-primary);
}
.chat-msg.ai .chat-bubble { background: #fff; border: 1px solid var(--el-border-color-light); }
.chat-bubble.loading { color: var(--el-text-color-secondary); font-style: italic; }
.chat-input { display: flex; align-items: center; }
.preview-card { height: 100%; display: flex; flex-direction: column; }
.preview-header { display: flex; align-items: center; justify-content: space-between; }
.preview-actions { display: flex; gap: 6px; }
.diagram-container {
  flex: 1; overflow: auto; background: #fafafa; border-radius: 8px;
  padding: 20px; display: flex; justify-content: center; align-items: flex-start;
}
.diagram-canvas { display: flex; justify-content: center; }
.empty-tips { margin-top: 16px; text-align: center; }
.empty-tips p { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 8px; }
</style>
