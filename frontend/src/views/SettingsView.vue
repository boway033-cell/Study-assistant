<template>
  <div>
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
      <template #header>
        <div class="card-header">
          <span>连接状态</span>
          <el-button size="small" @click="probe">重新检测</el-button>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="云端 DeepSeek">
          <el-tag :type="probeData.deepseek?.ok ? 'success' : 'danger'">
            {{ probeData.deepseek?.ok ? '已连接' : '未连接' }}
          </el-tag>
          <div class="form-tip">{{ probeData.deepseek?.reason || '' }}</div>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>使用说明</template>
      <ol class="help-list">
        <li><b>首次使用</b>：到 <a href="https://platform.deepseek.com" target="_blank">DeepSeek 开放平台</a> 注册并创建 API Key，填入上方保存即可。本应用 <b>全部 AI 分析均在本地完成</b>（解析/切块/检索不联网），仅将「提问 + 检索片段」发送到 DeepSeek 云端生成回答。</li>
        <li><b>模型选择</b>：日常问答选 flash（快、省 token）；分析难题、长文总结选 pro（深度推理）。可在 AI 问答页随时切换。</li>
        <li><b>数据位置</b>：所有资料与数据保存在 <code>backend/data/</code>，备份时复制该目录即可；API Key 保存在本地数据库，不会上传。</li>
        <li><b>词典</b>：可在 <code>backend/data/userdict.txt</code> 每行添加一个专业术语（如"拉格朗日中值定理"），提升搜索准确度。</li>
      </ol>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSettings, updateSettings, probeSettings } from '../api'

const form = ref({
  deepseek_api_key: '',
  deepseek_model: 'flash',
  rag_top_k: 5,
  vector_search: false,
})
const probeData = ref({})
const hasKey = ref(false)

const load = async () => {
  try {
    const s = await getSettings()
    // 后端返回脱敏 key（sk-***xxx）：非空表示已配置，输入框留空让用户重新填写
    hasKey.value = s.deepseek_api_key !== '' && s.deepseek_configured
    form.value = {
      deepseek_api_key: '',
      deepseek_model: s.deepseek_model,
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

onMounted(() => {
  load()
  probe()
})
</script>

<style scoped>
.form-tip { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.help-list { line-height: 2; padding-left: 20px; }
.help-list code { background: var(--el-fill-color-lighter); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
</style>