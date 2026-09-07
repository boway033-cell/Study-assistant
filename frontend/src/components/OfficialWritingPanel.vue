<template>
  <section class="official-workbench" aria-label="公文写作" v-loading="loading">
    <div class="official-toolbar">
      <div class="stage-list"><span :class="{ active: !current }">材料</span><span :class="{ active: stage === 'outline' }">提纲</span><span :class="{ active: stage === 'draft' }">正文</span><span v-if="current">版本 #{{ current.id }}</span></div>
      <el-button :disabled="!!busy" @click="startNew">新建</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="work-error" />
    <div class="official-layout">
      <aside class="brief-pane">
        <el-form label-position="top" :disabled="busy || !!current">
          <el-form-item label="主题 / 标题"><el-input v-model="brief.title" maxlength="255" aria-label="公文主题" /></el-form-item>
          <div class="brief-pair">
            <el-form-item label="文种"><el-select v-model="brief.genre" aria-label="公文文种"><el-option v-for="genre in genres" :key="genre" :label="genre" :value="genre" /></el-select></el-form-item>
            <el-form-item label="行文关系"><el-select v-model="brief.relationship" aria-label="行文关系"><el-option v-for="name in ['上行', '下行', '平行', '内部']" :key="name" :label="name" :value="name" /></el-select></el-form-item>
          </div>
          <el-form-item label="发文单位"><el-input v-model="brief.issuer" maxlength="200" aria-label="发文单位" /></el-form-item>
          <el-form-item label="主送对象"><el-input v-model="brief.recipient" maxlength="300" aria-label="主送对象" /></el-form-item>
          <el-form-item label="事实材料 / 政策依据"><el-input v-model="brief.facts" type="textarea" :rows="10" maxlength="24000" show-word-limit aria-label="事实材料" /></el-form-item>
          <el-form-item label="目标字数"><el-input-number v-model="brief.length" :min="100" :max="10000" :step="100" aria-label="目标字数" /></el-form-item>
        </el-form>
        <el-checkbox v-model="consent" :disabled="!!busy" class="consent">允许将本稿材料发送至已配置的写作模型</el-checkbox>
        <el-button v-if="!current" type="primary" :loading="busy === 'outline'" :disabled="!!busy || !consent || !brief.title.trim() || !brief.facts.trim()" @click="createOutline">生成提纲</el-button>
        <el-collapse class="skill-notices"><el-collapse-item title="技能与许可" name="skills"><div v-for="skill in skills" :key="skill.name" class="skill-line"><strong>{{ skill.name }}</strong><span>{{ skill.role }}</span><small>{{ skill.license }}</small></div><small>融合功能含非商业许可代码。事实与签发权限仍须人工核对。</small></el-collapse-item></el-collapse>
      </aside>
      <div class="editor-pane">
        <template v-if="current">
          <div class="editor-heading"><h2>{{ stage === 'outline' ? '提纲' : '正文' }}</h2><el-tag v-if="stage === 'outline'" :type="confirmed ? 'success' : 'info'">{{ confirmed ? '已确认' : '待确认' }}</el-tag><span v-if="dirty" class="unsaved">未保存</span></div>
          <el-input v-model="title" aria-label="稿件标题" maxlength="255" :disabled="!!busy" class="draft-title" />
          <el-input v-model="text" type="textarea" :rows="19" aria-label="公文编辑器" maxlength="60000" show-word-limit :disabled="!!busy" />
          <div class="editor-actions">
            <el-button :disabled="!!busy || !validText || !dirty" :loading="busy === 'save'" @click="action('save', { title, text })">保存版本</el-button>
            <template v-if="stage === 'outline'">
              <el-button :disabled="!!busy || !validText || confirmed" @click="action('confirm', { title, text })">确认提纲</el-button>
              <el-button type="primary" :loading="busy === 'draft'" :disabled="!!busy || !confirmed || !consent" @click="action('draft', { allow_model: true })">展开正文</el-button>
            </template>
            <template v-else>
              <el-button :loading="busy === 'review'" :disabled="!!busy || dirty || !consent" @click="action('review', { allow_model: true })">审稿</el-button>
              <el-button :loading="busy === 'export'" :disabled="!!busy || dirty || !ack" type="primary" @click="exportDraft">导出 Word</el-button>
            </template>
          </div>
          <template v-if="stage === 'draft'">
            <el-checkbox v-model="ack" :disabled="!!busy" class="consent">已人工核对待补项、事实和签发权限；导出后仍需检查分页与字体</el-checkbox>
            <section class="audit-panel" aria-label="审稿结果">
              <h3>检查</h3>
              <ul v-if="checks?.warnings?.length"><li v-for="warning in checks.warnings" :key="warning">{{ warning }}</li></ul>
              <span v-else>未命中自动检查项；不代表事实已核验。</span>
              <el-collapse>
                <el-collapse-item :title="checks?.lieflat?.applicable ? `结构诊断 · 样本 ${checks.lieflat.sample_size}` : '结构统计 · 当前文种无适用基线'" name="metrics">
                  <template v-if="checks?.lieflat?.applicable">
                    <small>统计偏离不等于错误，不应为贴合区间删除事实。</small>
                    <ul v-if="checks.lieflat.hints.length"><li v-for="(hint, index) in checks.lieflat.hints" :key="index">{{ hint.message }}<small>{{ hint.basis }}</small></li></ul>
                    <div class="metrics-wrap"><table><thead><tr><th>指标</th><th>实测</th><th>参考区间</th></tr></thead><tbody><tr v-for="metric in checks.lieflat.metrics" :key="metric.key"><td>{{ metricLabels[metric.key] || metric.key }}</td><td>{{ metric.value }}</td><td>{{ metric.low }}–{{ metric.high }}</td></tr></tbody></table></div>
                  </template>
                  <span v-else>保留文种、事实与人工审稿检查，不套用其他文种参数。</span>
                </el-collapse-item>
              </el-collapse>
              <pre v-if="current.audit.review" class="review-text">{{ current.audit.review }}</pre>
              <el-input v-model="instructions" type="textarea" :rows="3" maxlength="6000" aria-label="改稿意见" placeholder="改稿意见" :disabled="!!busy" />
              <el-button :loading="busy === 'revise'" :disabled="!!busy || dirty || !consent || !instructions.trim()" @click="action('revise', { instructions, allow_model: true })">按意见生成新版本</el-button>
            </section>
            <OfficialFormatPanel v-model="format" :record="formatRecord" :busy="!!busy" @save="saveFormat" />
            <div v-if="current.audit.export" class="export-result">
              <a :href="`/api/writing/outputs/${current.id}/download`" target="_blank" rel="noopener">下载已导出的 Word</a>
              <span>结构校验通过 · 未进行页面视觉核验</span>
              <ul v-if="current.audit.export.warnings.length"><li v-for="warning in current.audit.export.warnings" :key="warning">{{ warning }}</li></ul>
            </div>
          </template>
        </template>
        <div v-else class="empty-editor"><h2>公文写作</h2><span>材料 → 提纲确认 → 起草 → 审稿 → Word</span></div>
        <section class="version-history">
          <h3>历史版本</h3>
          <div v-if="!history.length" class="history-empty">暂无版本</div>
          <button v-for="item in history" :key="item.id" class="history-row" :disabled="!!busy" :aria-current="current?.id === item.id ? 'true' : undefined" @click="openVersion(item.id)"><span class="history-title">{{ item.title }}</span><span>#{{ item.id }} · {{ item.stage === 'outline' ? '提纲' : '正文' }}</span></button>
          <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" small :disabled="!!busy" @current-change="loadHistory" />
        </section>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'
import { officialAction, officialDetail, officialHistory, officialOptions, officialOutline, officialSaveProfile } from '../api/officialWriting'
import { formatDiff } from '../utils/officialFormat'
import OfficialFormatPanel from './OfficialFormatPanel.vue'

const freshBrief = () => ({ title: '', genre: '通知', issuer: '', recipient: '', relationship: '内部', facts: '', length: 1500 })
const brief = ref(freshBrief()), current = ref(null), title = ref(''), text = ref(''), instructions = ref('')
const genres = ref([]), skills = ref([]), history = ref([]), page = ref(1), total = ref(0)
const format = ref(null), formatRecord = ref(null)
const busy = ref(''), loading = ref(false), error = ref(''), consent = ref(false), ack = ref(false)
const clone = value => JSON.parse(JSON.stringify(value))
const stage = computed(() => current.value?.audit.stage)
const dirty = computed(() => !!current.value && (title.value !== current.value.title || text.value !== current.value.text))
const validText = computed(() => !!title.value.trim() && !!text.value.trim())
const confirmed = computed(() => !dirty.value && !!current.value?.audit.confirmed)
const checks = computed(() => current.value?.audit.checks)
const metricLabels = { chars: '字数', sent: '平均句长', h1: '一级标题', h2: '二级标题', h3: '三级标题', yishi: '一是二是', dun: '顿号密度‰', qz: '强制词', yq: '要求词', jy: '建议词', pct: '百分比', quote: '引号概念', quote_long: '长引语‰', paras: '段落数', para_len: '段落长度' }
const loadHistory = async () => { const result = await officialHistory(page.value); history.value = result.items; total.value = result.total }
const useVersion = row => {
  current.value = row; title.value = row.title; text.value = row.text; brief.value = clone(row.audit.brief)
  instructions.value = ''; ack.value = false
}
const mayLeave = async () => {
  if (busy.value) { ElMessage.warning('请等待当前操作完成'); return false }
  if (!dirty.value && (current.value || (!brief.value.facts && !brief.value.title))) return true
  try { await ElMessageBox.confirm('未保存的内容将被放弃，是否继续？', '确认切换', { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' }); return true } catch { return false }
}
onBeforeRouteLeave(mayLeave)
async function run(name, fn) {
  if (busy.value) return
  busy.value = name; error.value = ''
  try { await fn() } catch (e) { error.value = e.message || '操作失败' } finally { busy.value = '' }
}
async function refreshHistory() {
  try { page.value = 1; await loadHistory() } catch { ElMessage.warning('版本已保存，历史列表刷新失败') }
}
async function createOutline() {
  await run('outline', async () => { useVersion(await officialOutline(clone(brief.value))); await refreshHistory() })
}
async function action(name, payload) {
  await run(name, async () => {
    useVersion(await officialAction(current.value.id, name, { etag: current.value.etag, ...payload }))
    await refreshHistory()
  })
}
async function exportDraft() {
  await action('export', { overrides: formatDiff(format.value, formatRecord.value.preset), review_acknowledged: true })
}
async function startNew() {
  if (!await mayLeave()) return
  current.value = null; brief.value = freshBrief(); title.value = ''; text.value = ''; error.value = ''; consent.value = false; ack.value = false
  if (formatRecord.value) format.value = clone(formatRecord.value.profile)
}
async function openVersion(id) {
  if (!await mayLeave()) return
  await run('load', async () => {
    const row = await officialDetail(id)
    format.value = clone(row.audit.export?.profile || formatRecord.value.profile)
    useVersion(row)
  })
}
async function saveFormat() {
  await run('format', async () => {
    formatRecord.value = await officialSaveProfile({ overrides: formatDiff(format.value, formatRecord.value.preset), etag: formatRecord.value.etag, confirmed: true })
    ElMessage.success('默认排版已保存')
  })
}
onMounted(async () => {
  loading.value = true
  try {
    const options = await officialOptions(); genres.value = options.genres; skills.value = options.skills
    formatRecord.value = options.format; format.value = clone(options.format.profile)
    await loadHistory()
  } catch (e) { error.value = e.message } finally { loading.value = false }
})
</script>

<style scoped>
.official-workbench{min-width:0;background:var(--study-card-bg,#f5f1e8);color:var(--study-text-primary,#343832);padding:20px;border-radius:8px}.official-toolbar,.stage-list,.editor-heading,.editor-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px}.official-toolbar{justify-content:space-between;margin-bottom:18px}.stage-list{font-size:13px;color:var(--study-text-secondary)}.stage-list .active{color:var(--study-accent,#895c39);font-weight:700}.official-layout{display:grid;grid-template-columns:minmax(230px,300px) minmax(0,1fr);gap:24px}.brief-pane,.editor-pane{min-width:0}.brief-pane{border-right:1px solid var(--study-card-border,#ded7cb);padding-right:24px}.brief-pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}.brief-pane :deep(.el-form-item){margin-bottom:14px}.consent{height:auto;white-space:normal;align-items:flex-start;margin:12px 0;width:100%}.consent :deep(.el-checkbox__label){white-space:normal;line-height:1.6;overflow-wrap:anywhere}.consent :deep(.el-checkbox__input){margin-top:4px}.editor-heading h2{font-size:18px;margin:0}.draft-title{margin:12px 0}.editor-pane :deep(.el-textarea__inner){line-height:1.85}.editor-actions{margin:12px 0}.editor-actions .el-button{margin:0}.unsaved{color:#94603c;font-size:12px}.audit-panel{margin-top:18px;padding-top:12px;border-top:1px solid var(--study-card-border)}h3{font-size:14px;margin:10px 0}.audit-panel ul,.export-result ul{padding-left:20px;line-height:1.8;font-size:13px}.audit-panel li small{display:block;color:var(--study-text-secondary)}.audit-panel>.el-button{margin-top:10px}.review-text{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;background:rgba(128,109,80,.06);padding:12px}.metrics-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:5px 8px;text-align:left;border-bottom:1px solid var(--study-card-border)}.export-result{display:grid;gap:8px;margin-top:16px;font-size:12px;overflow-wrap:anywhere}.export-result a{color:var(--study-accent,#895c39);font-size:14px}.skill-notices{margin-top:18px}.skill-line{display:grid;gap:3px;margin-bottom:12px;font-size:12px;overflow-wrap:anywhere}.empty-editor{display:grid;place-content:center;text-align:center;min-height:300px;color:var(--study-text-secondary);gap:8px}.empty-editor h2{font-size:22px;color:var(--study-text-primary)}.empty-editor span{font-size:13px}.version-history{margin-top:24px;padding-top:12px;border-top:1px solid var(--study-card-border)}.history-row{display:flex;align-items:center;justify-content:space-between;gap:12px;background:none;border:0;border-bottom:1px solid var(--study-card-border);color:inherit;width:100%;padding:12px 0;text-align:left;cursor:pointer;font:inherit;font-size:12px}.history-row:hover,.history-row[aria-current=true]{color:var(--study-accent,#895c39)}.history-title{min-width:0;overflow-wrap:anywhere}.history-row>span:last-child{flex-shrink:0}.history-empty{font-size:13px;color:var(--study-text-secondary)}.work-error{margin-bottom:16px}
@media(max-width:900px){.official-layout{grid-template-columns:minmax(0,1fr)}.brief-pane{border-right:0;border-bottom:1px solid var(--study-card-border);padding:0 0 18px}.official-workbench{padding:14px}.empty-editor{min-height:140px}}
</style>
