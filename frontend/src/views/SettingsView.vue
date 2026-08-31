<template>
  <div class="settings-page study-page">
    <header class="page-heading">
      <div><h1>模型设置</h1><p>添加 API 密钥，选择一个默认模型即可开始使用。</p></div>
      <el-button :loading="probingAll" @click="probeDefault">检测默认模型</el-button>
    </header>

    <el-card shadow="never" class="model-panel">
      <div class="section-heading"><div><h2>模型供应商</h2><p>密钥只加密保存在本机。</p></div></div>

      <div class="provider-list">
        <div v-for="provider in textProviders" :key="provider.id" class="provider-item">
          <span :class="['status-dot', providerState(provider)]" />
          <div class="provider-main">
            <div class="provider-name">
              {{ provider.name }}
              <span v-if="provider.id === routing.default_provider_id" class="default-badge">默认</span>
            </div>
            <div class="provider-meta">{{ provider.model }}<span>·</span>{{ providerStatusText(provider) }}</div>
            <div v-if="probeMessages[provider.id]" class="probe-message">{{ probeMessages[provider.id] }}</div>
          </div>
          <div class="provider-actions">
            <el-button text :loading="probingId === provider.id" @click="probeProvider(provider)">检测</el-button>
            <el-button @click="editProvider(provider)">编辑</el-button>
            <el-button v-if="!provider.builtin" text type="danger" @click="removeProvider(provider)">删除</el-button>
          </div>
        </div>
      </div>

      <div class="default-row">
        <div><b>默认模型</b><small>未单独指定的 AI 功能都使用此模型</small></div>
        <el-select v-model="routing.default_provider_id" style="width: min(320px, 100%)" @change="saveRouting">
          <el-option v-for="provider in textProviders" :key="provider.id" :label="providerLabel(provider)" :value="provider.id" />
        </el-select>
      </div>

      <div class="add-actions">
        <el-button class="add-button" @click="openProvider('kimi')">＋ 添加供应商</el-button>
        <el-button class="add-button" @click="openProvider('custom')">＋ 添加自定义供应商</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="advanced-panel">
      <el-collapse v-model="openSections">
        <el-collapse-item name="routes">
          <template #title><div class="collapse-title"><b>按功能选择模型</b><span>可选</span><small>研究、写作和 PPT 可以使用不同模型</small></div></template>
          <div class="route-grid">
            <label v-for="(label, key) in taskLabels" :key="key">
              <span>{{ label }}</span>
              <el-select v-model="routing.task_routes[key]" @change="saveRouting">
                <el-option label="跟随默认模型" value="" />
                <el-option v-for="provider in textProviders" :key="provider.id" :label="providerLabel(provider)" :value="provider.id" />
              </el-select>
            </label>
          </div>
          <div class="fallback-row"><div><b>失败降级顺序</b><small>仅在主模型尚未输出任何文字时依次尝试，防止不同模型内容被拼接。</small></div><el-select v-model="routing.fallback_provider_ids" multiple collapse-tags placeholder="不自动降级" @change="saveRouting"><el-option v-for="provider in textProviders" :key="provider.id" :disabled="provider.id===routing.default_provider_id" :label="providerLabel(provider)" :value="provider.id" /></el-select></div>
        </el-collapse-item>

        <el-collapse-item name="usage">
          <template #title><div class="collapse-title"><b>模型使用与故障</b><span>本机统计</span><small>调用、失败、降级与估算 token</small></div></template>
          <div class="usage-summary"><span>调用 <b>{{ usage.totals.calls || 0 }}</b></span><span>失败 <b>{{ usage.totals.failures || 0 }}</b></span><span>降级 <b>{{ usage.totals.fallback_activations || 0 }}</b></span><span>估算 Token <b>{{ formatNumber(usage.totals.estimated_tokens) }}</b></span></div>
          <p class="usage-boundary">{{ usage.boundary }}</p>
        </el-collapse-item>

        <el-collapse-item name="vision">
          <template #title><div class="collapse-title"><b>页面视觉分析</b><span>可选</span><small>用于 PDF 页面、图表和公式解读</small></div></template>
          <el-form label-position="top" class="compact-form">
            <div class="two-columns">
              <el-form-item label="API Key">
                <el-input v-model="form.vision_api_key" type="password" show-password :placeholder="hasVisionKey ? '已配置，留空保持不变' : '输入视觉模型 API Key'" />
              </el-form-item>
              <el-form-item label="模型">
                <el-select v-model="form.vision_model" filterable allow-create style="width: 100%">
                  <el-option value="qwen3-vl-plus" label="qwen3-vl-plus" />
                  <el-option value="qwen3-vl-flash" label="qwen3-vl-flash" />
                </el-select>
              </el-form-item>
            </div>
            <el-form-item label="Base URL"><el-input v-model="form.vision_base_url" /></el-form-item>
            <div class="form-actions"><el-button type="primary" @click="saveAdvanced">保存视觉设置</el-button><el-button :loading="probingVision" @click="probeVision">检测视觉模型</el-button></div>
            <p v-if="visionMessage" class="inline-message">{{ visionMessage }}</p>
          </el-form>
        </el-collapse-item>

        <el-collapse-item name="local">
          <template #title><div class="collapse-title"><b>本地检索与存储</b><span>高级</span><small>一般无需调整</small></div></template>
          <el-form label-position="top" class="compact-form">
            <div class="two-columns">
              <el-form-item label="检索片段数"><el-input-number v-model="form.rag_top_k" :min="1" :max="20" /></el-form-item>
              <el-form-item label="向量检索"><el-switch v-model="form.vector_search" /><span class="field-hint">开启后加载本地嵌入模型</span></el-form-item>
            </div>
            <div class="form-actions"><el-button type="primary" @click="saveAdvanced">保存检索设置</el-button></div>
          </el-form>
          <el-divider />
          <div :class="['capacity-card', capacity.tier || 'normal']">
            <div><span>知识库容量</span><b>{{ formatNumber(capacity.books) }} 本 · {{ formatNumber(capacity.chunks) }} 个片段 · {{ formatBytes(capacity.database_bytes) }}</b><small>{{ capacity.recommendation }}</small></div>
            <el-tag :type="capacity.tier==='migration_review'?'danger':capacity.tier==='attention'?'warning':'success'" effect="plain">{{ capacityTierText }}</el-tag>
          </div>
          <p class="usage-boundary">{{ capacity.boundary }}</p>
          <el-divider />
          <div class="storage-head"><div>受管数据约 <b>{{ formatBytes(storage.total_bytes) }}</b><small>原始文献、数据库、备份和 PPTX 成品不会清理。</small></div><el-button text :loading="storageLoading" @click="loadStorage">重新统计</el-button></div>
          <div class="storage-list">
            <label v-for="item in storage.items || []" :key="item.key" class="storage-row">
              <el-checkbox v-if="item.clearable" v-model="cleanupSelection" :value="item.key" />
              <span v-else class="protected-label">保护</span>
              <b>{{ item.label }}</b><span>{{ formatBytes(item.bytes) }}</span><small>{{ item.recoverability }}</small>
            </label>
          </div>
          <el-button :disabled="!cleanupSelection.length" :loading="storageLoading" @click="clearSelectedCaches">清理所选缓存</el-button>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-dialog v-model="deepseekDialog" title="编辑 DeepSeek" width="min(520px, 94vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item label="API Key"><el-input v-model="form.deepseek_api_key" type="password" show-password :placeholder="hasKey ? '已配置，留空保持不变' : '输入 DeepSeek API Key'" /></el-form-item>
        <el-form-item label="模型档位">
          <el-radio-group v-model="form.deepseek_model"><el-radio value="flash">Flash</el-radio><el-radio value="pro">Pro</el-radio></el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="deepseekDialog=false">取消</el-button><el-button type="primary" @click="saveDeepseek">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="providerDialog" :title="providerForm.id ? `编辑 ${providerForm.name}` : '添加模型供应商'" width="min(560px, 94vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item v-if="!providerForm.id" label="供应商">
          <el-select v-model="providerForm.vendor" style="width: 100%" @change="applyPreset"><el-option v-for="(preset, key) in providerPresets" :key="key" :value="key" :label="preset.label" /></el-select>
        </el-form-item>
        <el-form-item label="显示名称"><el-input v-model="providerForm.name" /></el-form-item>
        <el-form-item label="API Key"><el-input v-model="providerForm.api_key" type="password" show-password :placeholder="providerForm.id ? '留空保持原 Key' : '输入 API Key；本机无鉴权服务可留空'" /></el-form-item>
        <el-form-item label="模型">
          <div class="model-input"><el-select v-model="providerForm.model" filterable allow-create default-first-option><el-option v-for="model in discoveredModels" :key="model" :label="model" :value="model" /></el-select><el-button v-if="providerForm.id" :loading="modelsLoading" @click="syncModels">读取列表</el-button></div>
        </el-form-item>
        <el-collapse class="dialog-advanced">
          <el-collapse-item name="connection" title="自定义连接设置">
            <el-form-item label="协议"><el-select v-model="providerForm.protocol" style="width:100%"><el-option v-for="(label, key) in protocolNames" :key="key" :label="label" :value="key" /></el-select></el-form-item>
            <el-form-item label="Base URL"><el-input v-model="providerForm.base_url" /></el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer><el-button @click="providerDialog=false">取消</el-button><el-button type="primary" @click="submitProvider">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { cleanupStorage, deleteCompatibleProvider, getCapacityStatus, getProviderUsage, getSettings, getStorageUsage, listCompatibleProviders, listProviderModels, probeCompatibleProvider, probeSettings, saveCompatibleProvider, updateProviderRouting, updateSettings } from '../api'

const taskLabels = { chat: 'AI 问答', research: '研究与跨文献分析', writing: '写作台', presentation: 'PPT 生成', utility: '题目与通用生成' }
const protocolNames = { openai_chat: 'Chat Completions', anthropic_messages: 'Anthropic Messages', google_generate: 'Google GenerateContent' }
const providerPresets = {
  kimi: { label: 'Kimi / Moonshot', name: 'Kimi', protocol: 'openai_chat', base_url: 'https://api.moonshot.cn/v1', model: 'kimi-k2.6' },
  zhipu: { label: '智谱 GLM', name: '智谱 GLM', protocol: 'openai_chat', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-5.2' },
  qwen: { label: '通义千问 / 阿里百炼', name: '通义千问', protocol: 'openai_chat', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3-max' },
  openai: { label: 'OpenAI', name: 'OpenAI', protocol: 'openai_chat', base_url: 'https://api.openai.com/v1', model: 'gpt-5.4' },
  anthropic: { label: 'Anthropic Claude', name: 'Anthropic Claude', protocol: 'anthropic_messages', base_url: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-6' },
  gemini: { label: 'Google Gemini', name: 'Google Gemini', protocol: 'google_generate', base_url: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-3.7-flash' },
  custom: { label: '自定义 / 本机服务', name: '自定义模型', protocol: 'openai_chat', base_url: '', model: '' },
}

const providers = ref([])
const routing = ref({ default_provider_id: 'deepseek', task_routes: {}, fallback_provider_ids: [] })
const usage = ref({items:[],totals:{},boundary:''})
const capacity = ref({tier:'normal',books:0,chunks:0,database_bytes:0,recommendation:'',boundary:''})
const form = ref({ deepseek_api_key: '', deepseek_model: 'flash', vision_api_key: '', vision_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', vision_model: 'qwen3-vl-plus', rag_top_k: 5, vector_search: false })
const hasKey = ref(false)
const hasVisionKey = ref(false)
const providerDialog = ref(false)
const deepseekDialog = ref(false)
const providerForm = ref({ id: null, vendor: 'kimi', capability: 'text', ...providerPresets.kimi, api_key: '' })
const discoveredModels = ref([])
const modelsLoading = ref(false)
const openSections = ref([])
const probeStates = ref({})
const probeMessages = ref({})
const probingId = ref('')
const probingAll = ref(false)
const probingVision = ref(false)
const visionMessage = ref('')
const storage = ref({ total_bytes: 0, items: [] })
const storageLoading = ref(false)
const cleanupSelection = ref([])
const textProviders = computed(() => providers.value.filter(provider => provider.capability === 'text'))
const capacityTierText = computed(() => ({normal:'常规区间',attention:'需要关注',migration_review:'迁移评审'}[capacity.value.tier] || '待评估'))

const providerLabel = provider => `${provider.name} · ${provider.model}`
const providerState = provider => probeStates.value[provider.id] || (provider.configured ? 'configured' : 'missing')
const providerStatusText = provider => ({ ok: '连接正常', error: '连接失败', configured: '已保存，尚未检测', missing: '缺少 API Key' })[providerState(provider)]

const load = async () => {
  const settings = await getSettings()
  hasKey.value = Boolean(settings.deepseek_configured)
  hasVisionKey.value = Boolean(settings.vision_configured)
  form.value = { deepseek_api_key: '', deepseek_model: settings.deepseek_model || 'flash', vision_api_key: '', vision_base_url: settings.vision_base_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1', vision_model: settings.vision_model || 'qwen3-vl-plus', rag_top_k: Number.parseInt(settings.rag_top_k, 10) || 5, vector_search: Boolean(settings.vector_search) }
}

const loadProviders = async () => {
  const data = await listCompatibleProviders()
  providers.value = data.items || []
  routing.value = { default_provider_id: data.default_text_provider || 'deepseek', task_routes: { ...(data.task_routes || {}) }, fallback_provider_ids: data.fallback_provider_ids || [] }
}
const loadUsage=async()=>{usage.value=await getProviderUsage()}
const loadCapacity=async()=>{capacity.value=await getCapacityStatus()}
const formatNumber=value=>Number(value||0).toLocaleString('zh-CN')

const applyPreset = vendor => Object.assign(providerForm.value, providerPresets[vendor] || providerPresets.custom, { vendor })
const openProvider = vendor => { providerForm.value = { id: null, vendor, capability: 'text', ...(providerPresets[vendor] || providerPresets.custom), api_key: '' }; discoveredModels.value = []; providerDialog.value = true }
const editProvider = provider => {
  if (provider.builtin) { deepseekDialog.value = true; return }
  providerForm.value = { ...provider, api_key: '' }; discoveredModels.value = []; providerDialog.value = true
}

const saveDeepseek = async () => {
  await updateSettings({ deepseek_api_key: form.value.deepseek_api_key || undefined, deepseek_model: form.value.deepseek_model })
  deepseekDialog.value = false; await Promise.all([load(), loadProviders()]); ElMessage.success('DeepSeek 配置已保存')
}

const submitProvider = async () => {
  if (!providerForm.value.name.trim() || !providerForm.value.base_url.trim() || !providerForm.value.model.trim()) return ElMessage.warning('请填写名称、模型和 Base URL')
  await saveCompatibleProvider({ ...providerForm.value, api_key: providerForm.value.api_key || undefined })
  providerDialog.value = false; await loadProviders(); ElMessage.success('供应商已保存')
}

const saveRouting = async () => {
  try { await updateProviderRouting(routing.value); await loadProviders(); ElMessage.success('默认模型已更新') }
  catch (error) { ElMessage.error(error.message) }
}

const probeProvider = async provider => {
  probingId.value = provider.id
  try { const result = await probeCompatibleProvider(provider.id); probeStates.value[provider.id] = result.ok ? 'ok' : 'error'; probeMessages.value[provider.id] = result.reason || (result.ok ? '连接正常' : '连接失败') }
  catch (error) { probeStates.value[provider.id] = 'error'; probeMessages.value[provider.id] = error.message }
  finally { probingId.value = '' }
}

const probeDefault = async () => {
  probingAll.value = true
  try {
    const id = routing.value.default_provider_id || 'deepseek'
    const result = await probeCompatibleProvider(id)
    probeStates.value[id] = result.ok ? 'ok' : 'error'
    probeMessages.value[id] = result.reason || (result.ok ? '连接正常' : '未返回检测结果')
  } catch (error) { ElMessage.error(error.message) }
  finally { probingAll.value = false }
}

const probeVision = async () => {
  probingVision.value = true
  try { const result = await probeSettings(); visionMessage.value = result.vision?.reason || '未返回检测结果'; (result.vision?.ok ? ElMessage.success : ElMessage.warning)(visionMessage.value) }
  catch (error) { ElMessage.error(error.message) }
  finally { probingVision.value = false }
}

const syncModels = async () => {
  modelsLoading.value = true
  try { const result = await listProviderModels(providerForm.value.id); discoveredModels.value = result.items || []; ElMessage.success(`读取到 ${discoveredModels.value.length} 个模型`) }
  catch (error) { ElMessage.error(error.message) }
  finally { modelsLoading.value = false }
}

const removeProvider = async provider => {
  try { await ElMessageBox.confirm(`删除“${provider.name}”？`, '删除供应商'); await deleteCompatibleProvider(provider.id); await loadProviders() }
  catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message) }
}

const saveAdvanced = async () => {
  try { await updateSettings({ vision_api_key: form.value.vision_api_key || undefined, vision_base_url: form.value.vision_base_url, vision_model: form.value.vision_model, rag_top_k: form.value.rag_top_k, vector_search: form.value.vector_search }); await load(); ElMessage.success('设置已保存') }
  catch (error) { ElMessage.error(error.message) }
}

const formatBytes = (bytes = 0) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
const loadStorage = async () => { storageLoading.value = true; try { storage.value = await getStorageUsage() } catch (error) { ElMessage.error(error.message) } finally { storageLoading.value = false } }
const clearSelectedCaches = async () => {
  try { await ElMessageBox.confirm('只清理可重建缓存，原始文献和知识库不会删除。', '清理缓存', { type: 'warning' }); storageLoading.value = true; const result = await cleanupStorage(cleanupSelection.value); storage.value = result.storage; cleanupSelection.value = []; ElMessage.success(`已释放 ${formatBytes(result.released_bytes)}`) }
  catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message) }
  finally { storageLoading.value = false }
}

onMounted(async () => {
  try { await Promise.all([load(), loadProviders(), loadStorage(), loadUsage(), loadCapacity()]); await probeDefault() }
  catch (error) { ElMessage.error(error.message) }
})
</script>

<style scoped>
.settings-page{max-width:920px;margin:0 auto;padding-bottom:32px}.page-heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px}.page-heading h1{font-size:24px;line-height:1.3;color:#f7f2e9;font-weight:650;text-shadow:0 1px 8px rgba(0,0,0,.22)}.page-heading>div>p{margin-top:5px;color:rgba(247,242,233,.72);font-size:14px}.section-heading p{margin-top:5px;color:var(--el-text-color-secondary);font-size:14px}.model-panel,.advanced-panel{border:1px solid var(--study-card-border);border-radius:14px}.advanced-panel{margin-top:14px}.section-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.section-heading h2{font-size:17px}.provider-list{display:grid;gap:10px}.provider-item{display:grid;grid-template-columns:10px minmax(0,1fr) auto;align-items:center;gap:12px;min-height:68px;padding:11px 14px;border:1px solid var(--study-card-border);border-radius:12px;background:var(--el-bg-color)}.status-dot{width:9px;height:9px;border-radius:50%;background:#c8cdd4}.status-dot.ok{background:#2fbf71;box-shadow:0 0 0 3px rgba(47,191,113,.12)}.status-dot.error,.status-dot.missing{background:#e46b5d}.status-dot.configured{background:#d5a83e}.provider-main{min-width:0}.provider-name{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:650}.default-badge{padding:2px 7px;border-radius:999px;background:#eef4f1;color:#396c5c;font-size:11px;font-weight:500}.provider-meta,.probe-message{margin-top:4px;color:var(--el-text-color-secondary);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.provider-meta span{margin:0 6px}.probe-message{color:var(--study-text-strong)}.provider-actions{display:flex;align-items:center}.default-row{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-top:14px;padding:14px;border-radius:12px;background:var(--el-fill-color-lighter)}.default-row>div{display:flex;flex-direction:column;gap:3px}.default-row small{color:var(--el-text-color-secondary)}.add-actions{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.add-button{height:44px;border-style:dashed}.advanced-panel :deep(.el-card__body){padding:0 18px}.advanced-panel :deep(.el-collapse){border:0}.collapse-title{display:flex;align-items:center;min-width:0;gap:9px}.collapse-title span{padding:1px 6px;border-radius:5px;background:var(--el-fill-color-light);color:var(--el-text-color-secondary);font-size:11px}.collapse-title small{color:var(--el-text-color-secondary);font-weight:400}.route-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:4px 0 16px}.route-grid label{display:flex;flex-direction:column;gap:6px;color:var(--el-text-color-secondary);font-size:12px}.fallback-row{display:grid;grid-template-columns:minmax(240px,1fr) minmax(260px,1fr);align-items:center;gap:18px;padding:12px 0 16px;border-top:1px solid var(--study-card-border)}.fallback-row>div{display:flex;flex-direction:column;gap:4px}.fallback-row small,.usage-boundary{color:var(--study-text-secondary);font-size:11px}.usage-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:4px 0 10px}.usage-summary span{display:flex;flex-direction:column;padding:10px;border-radius:8px;background:var(--study-surface-muted);font-size:11px}.usage-summary b{margin-top:4px;font:600 18px var(--study-font-latin)}.compact-form{padding:4px 0 16px}.two-columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field-hint{margin-left:10px;color:var(--el-text-color-secondary);font-size:12px}.form-actions{display:flex;gap:8px}.inline-message{margin-top:8px;color:var(--el-text-color-secondary);font-size:12px}.storage-head{display:flex;justify-content:space-between;align-items:center}.storage-head>div{display:flex;flex-direction:column;gap:3px}.storage-head small{color:var(--el-text-color-secondary)}.storage-list{margin:12px 0}.storage-row{display:grid;grid-template-columns:56px minmax(120px,1fr) 90px minmax(160px,1.4fr);align-items:center;gap:8px;min-height:40px;border-top:1px solid var(--study-card-border);font-size:12px}.storage-row span,.storage-row small{color:var(--study-text-secondary)}.protected-label{font-size:11px}.model-input{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;width:100%}.dialog-advanced{margin-top:6px;border-top:1px solid var(--study-card-border)}
.capacity-card{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:13px;border:1px solid var(--study-card-border);border-radius:10px;background:var(--study-surface-muted)}.capacity-card>div{display:flex;flex-direction:column;gap:4px}.capacity-card span,.capacity-card small{color:var(--study-text-secondary);font-size:12px}.capacity-card b{font-size:14px}.capacity-card.attention{border-color:#d8b46b}.capacity-card.migration_review{border-color:#d98474}
@media(max-width:720px){.settings-page{padding:0 2px 24px}.page-heading{align-items:center}.route-grid,.two-columns,.add-actions,.fallback-row,.usage-summary{grid-template-columns:1fr}.provider-item{grid-template-columns:10px minmax(0,1fr)}.provider-actions{grid-column:2;justify-content:flex-start}.default-row{align-items:stretch;flex-direction:column}.collapse-title small{display:none}.storage-row{grid-template-columns:48px 1fr 80px}.storage-row small{display:none}}
</style>
