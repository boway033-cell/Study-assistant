<template>
  <div class="settings-page study-page">
    <el-card shadow="never">
      <template #header>AI 设置（DeepSeek 云端）</template>
      <el-form label-width="150px" style="max-width: 620px">
        <el-form-item label="DeepSeek API Key">
          <el-input v-model="form.deepseek_api_key" type="password" show-password
            :placeholder="hasKey ? '已配置（留空保存则保留原 Key）' : 'sk-... 在 platform.deepseek.com 获取'" />
          <div class="form-tip" v-if="hasKey">✅ 已配置 API Key（出于安全考虑不显示完整内容）</div>
          <div class="form-tip" v-else>⚠️ 未配置 API Key，AI 问答 / AI 生成题目暂不可用，请在下方填入</div>
        </el-form-item>
        <el-form-item label="模型档位">
          <el-radio-group v-model="form.deepseek_model">
            <el-radio value="flash">⚡ flash（快速）</el-radio>
            <el-radio value="pro">🧠 pro（深度推理）</el-radio>
          </el-radio-group>
          <div class="form-tip">flash = deepseek-v4-flash（响应快，适合日常问答）；pro = deepseek-v4-pro（深度思考，适合难题/长文分析）</div>
        </el-form-item>
        <el-divider content-position="left">视觉分析（可选 · Qwen-VL，阿里百炼）</el-divider>
        <el-form-item label="视觉 API Key">
          <el-input v-model="form.vision_api_key" type="password" show-password
            :placeholder="hasVisionKey ? '已配置（留空保存则保留原 Key）' : 'sk-... 在阿里云百炼获取'" />
          <div class="form-tip" v-if="hasVisionKey">✅ 已配置视觉 Key（用于 PDF 阅读器「AI 解读本页/图表/公式」）</div>
          <div class="form-tip" v-else>可选：到 <a href="https://bailian.console.aliyun.com" target="_blank">阿里云百炼</a> 开通并创建 API Key；不配置则视觉解读不可用，其余功能不受影响</div>
        </el-form-item>
        <el-form-item label="视觉模型">
          <el-select v-model="form.vision_model" filterable allow-create default-first-option style="width: 300px">
            <el-option value="qwen3-vl-plus" label="qwen3-vl-plus（推荐，均衡）" />
            <el-option value="qwen3-vl-flash" label="qwen3-vl-flash（快，便宜）" />
          </el-select>
        </el-form-item>
        <el-form-item label="视觉接口地址"><el-input v-model="form.vision_base_url" placeholder="https://.../v1" /><div class="form-tip">默认保持阿里百炼；也可填写支持 OpenAI Chat Completions 图像输入格式的其他 HTTPS 接口。</div></el-form-item>
        <el-form-item label="检索片段数">
          <el-input-number v-model="form.rag_top_k" :min="1" :max="20" />
        </el-form-item>
        <el-form-item label="向量检索（P1）">
          <el-switch v-model="form.vector_search" />
          <div class="form-tip">默认关闭（零内存）；开启后需加载本地嵌入模型，仅供高级语义检索场景</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="save">保存设置</el-button>
          <el-button @click="load">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header><div class="card-header"><span>备用兼容接口</span><el-button size="small" type="primary" plain @click="openProvider()">添加接口</el-button></div></template>
      <el-alert type="info" :closable="false" title="默认文本分析仍固定使用 DeepSeek；这里保存的文本/多模态接口不会自动接管现有任务。" />
      <div v-if="providers.length" class="provider-cards">
        <div v-for="p in providers" :key="p.id" class="provider-row">
          <div><b>{{ p.name }}</b><span>{{ p.capability === 'vision' ? '多模态' : '文本' }} · {{ p.model }}</span><small>{{ p.base_url }}</small></div>
          <el-tag size="small" :type="p.configured ? 'success' : 'warning'">{{ p.configured ? '已配置' : '缺少 Key' }}</el-tag>
          <el-button size="small" @click="probeProvider(p)">检测</el-button><el-button size="small" @click="openProvider(p)">编辑</el-button><el-button size="small" type="danger" plain @click="removeProvider(p)">删除</el-button>
        </div>
      </div>
      <el-empty v-else description="尚未添加备用接口" :image-size="70" />
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-header">
          <span>连接状态</span>
          <el-button size="small" @click="probe">重新检测</el-button>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="云端 DeepSeek（问答）">
          <el-tag :type="probeData.deepseek?.ok ? 'success' : 'danger'">
            {{ probeData.deepseek?.ok ? '已连接' : '未连接' }}
          </el-tag>
          <div class="form-tip">{{ probeData.deepseek?.reason || '' }}</div>
        </el-descriptions-item>
        <el-descriptions-item label="视觉模型（Qwen-VL）">
          <el-tag :type="probeData.vision?.ok ? 'success' : 'danger'">
            {{ probeData.vision?.ok ? '已配置' : '未配置' }}
          </el-tag>
          <div class="form-tip">{{ probeData.vision?.reason || '' }}</div>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>使用说明</template>
      <ol class="help-list">
        <li><b>首次使用</b>：到 <a href="https://platform.deepseek.com" target="_blank">DeepSeek 开放平台</a> 注册并创建 API Key。资料解析、切块和本地检索不联网；问答、总结和深度分析会将「任务指令 + 相关来源片段」发送到当前配置的 DeepSeek 云端。</li>
        <li><b>模型选择</b>：日常问答选 flash（快、省 token）；分析难题、长文总结选 pro（深度推理）。可在 AI 问答页随时切换。</li>
        <li><b>数据位置</b>：所有资料与数据保存在 <code>backend/data/</code>，备份时复制该目录即可；API Key 保存在本地数据库，不会上传。</li>
        <li><b>词典</b>：可在 <code>backend/data/userdict.txt</code> 每行添加一个专业术语（如"拉格朗日中值定理"），提升搜索准确度。</li>
      </ol>
    </el-card>
    <el-dialog v-model="providerDialog" title="兼容接口" width="min(560px,94vw)">
      <el-form label-position="top">
        <el-form-item label="显示名称"><el-input v-model="providerForm.name" /></el-form-item>
        <el-form-item label="能力"><el-radio-group v-model="providerForm.capability"><el-radio value="text">文本</el-radio><el-radio value="vision">多模态</el-radio></el-radio-group></el-form-item>
        <el-form-item label="协议"><el-select v-model="providerForm.protocol" disabled><el-option label="OpenAI Chat Completions 兼容" value="openai_chat" /></el-select></el-form-item>
        <el-form-item label="Base URL"><el-input v-model="providerForm.base_url" placeholder="https://api.vendor.example/v1" /></el-form-item>
        <el-form-item label="模型名"><el-input v-model="providerForm.model" /></el-form-item>
        <el-form-item label="API Key"><el-input v-model="providerForm.api_key" type="password" show-password :placeholder="providerForm.id ? '留空保留原 Key' : '必填'" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="providerDialog=false">取消</el-button><el-button type="primary" @click="submitProvider">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSettings, updateSettings, probeSettings, listCompatibleProviders, saveCompatibleProvider, deleteCompatibleProvider, probeCompatibleProvider } from '../api'

const form = ref({
  deepseek_api_key: '',
  deepseek_model: 'flash',
  vision_api_key: '',
  vision_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  vision_model: 'qwen3-vl-plus',
  rag_top_k: 5,
  vector_search: false,
})
const probeData = ref({})
const hasKey = ref(false)
const hasVisionKey = ref(false)
const providers = ref([])
const providerDialog = ref(false)
const providerForm = ref({ id:null, name:'', capability:'text', protocol:'openai_chat', base_url:'', model:'', api_key:'' })

const load = async () => {
  try {
    const s = await getSettings()
    // 后端返回脱敏 key（sk-***xxx）：非空表示已配置，输入框留空让用户重新填写
    hasKey.value = s.deepseek_api_key !== '' && s.deepseek_configured
    hasVisionKey.value = s.vision_api_key !== '' && s.vision_configured
    form.value = {
      deepseek_api_key: '',
      deepseek_model: s.deepseek_model,
      vision_api_key: '',
      vision_base_url: s.vision_base_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      vision_model: s.vision_model || 'qwen3-vl-plus',
      rag_top_k: parseInt(s.rag_top_k),
      vector_search: s.vector_search,
    }
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const save = async () => {
  try {
    await updateSettings({
      // 用户留空 = 保留已存 Key；填写 = 更新
      deepseek_api_key: form.value.deepseek_api_key || undefined,
      deepseek_model: form.value.deepseek_model,
      vision_api_key: form.value.vision_api_key || undefined,
      vision_base_url: form.value.vision_base_url,
      vision_model: form.value.vision_model,
      rag_top_k: form.value.rag_top_k,
      vector_search: form.value.vector_search,
    })
    ElMessage.success('设置已保存')
    probe()
    load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const probe = async () => {
  try {
    probeData.value = await probeSettings()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

const loadProviders = async () => { try { providers.value = (await listCompatibleProviders()).items || [] } catch(e) { ElMessage.error(e.message) } }
const openProvider = (profile=null) => {
  providerForm.value = profile ? {...profile, api_key:''} : {id:null,name:'',capability:'text',protocol:'openai_chat',base_url:'',model:'',api_key:''}
  providerDialog.value = true
}
const submitProvider = async () => {
  if(!providerForm.value.name.trim() || !providerForm.value.base_url.trim() || !providerForm.value.model.trim()) return ElMessage.warning('请完整填写接口信息')
  try { await saveCompatibleProvider({...providerForm.value, api_key:providerForm.value.api_key || undefined}); providerDialog.value=false; await loadProviders(); ElMessage.success('兼容接口已保存，默认 DeepSeek 路由未改变') } catch(e) { ElMessage.error(e.message) }
}
const probeProvider = async (profile) => { try { const r=await probeCompatibleProvider(profile.id); (r.ok?ElMessage.success:ElMessage.warning)(r.reason) } catch(e) { ElMessage.error(e.message) } }
const removeProvider = async (profile) => { try { await ElMessageBox.confirm(`删除接口“${profile.name}”？`,'删除兼容接口'); await deleteCompatibleProvider(profile.id); await loadProviders() } catch(e) { if(e!=='cancel') ElMessage.error(e.message) } }

onMounted(() => {
  load()
  probe()
  loadProviders()
})
</script>

<style scoped>
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.help-list { line-height: 2; padding-left: 20px; }
.help-list code { background: var(--el-fill-color-lighter); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.provider-cards { display:grid; gap:8px; margin-top:12px; }.provider-row { display:grid; grid-template-columns:minmax(0,1fr) auto auto auto auto; align-items:center; gap:8px; padding:10px 12px; border:1px solid #e5e7eb; border-radius:9px; box-shadow:0 1px 2px rgba(15,23,42,.05); }.provider-row div { display:flex; min-width:0; flex-direction:column; }.provider-row span,.provider-row small { color:var(--el-text-color-secondary); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.settings-page{max-width:1120px}.settings-page>.el-card{margin-top:8px!important;border-radius:var(--study-radius-md)}.settings-page>.el-card:first-child{margin-top:0!important}.settings-page :deep(.el-card__header){padding:11px 14px}.settings-page :deep(.el-card__body){padding:14px}.provider-row{border-color:var(--study-card-border);border-radius:var(--study-radius-sm);box-shadow:none}@media(max-width:760px){.provider-row{grid-template-columns:1fr auto}.provider-row>.el-button{margin-left:0}.settings-page :deep(.el-form){max-width:none!important}.settings-page :deep(.el-form-item__label){width:100%!important;text-align:left}.settings-page :deep(.el-form-item__content){margin-left:0!important}}
</style>
