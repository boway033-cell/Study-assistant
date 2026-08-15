<template>
  <div>
    <el-card shadow="never">
      <template #header>AI 设置</template>
      <el-form label-width="140px" style="max-width: 560px">
        <el-form-item label="LLM 模式">
          <el-radio-group v-model="form.llm_mode">
            <el-radio value="local">本地（Ollama）</el-radio>
            <el-radio value="cloud">云端（DeepSeek）</el-radio>
          </el-radio-group>
          <div class="form-tip">
            本地模式免费离线但能力较弱；云端模式需要 API Key，问答质量更高。
          </div>
        </el-form-item>
        <el-form-item label="DeepSeek API Key">
          <el-input v-model="form.deepseek_api_key" type="password" show-password placeholder="sk-..." />
        </el-form-item>
        <el-form-item label="本地模型名">
          <el-input v-model="form.ollama_model" placeholder="qwen2.5:3b-instruct" />
          <div class="form-tip">需已通过 Ollama 拉取模型（如：ollama pull qwen2.5:3b）</div>
        </el-form-item>
        <el-form-item label="每日新卡数">
          <el-input-number v-model="form.daily_new_cards" :min="1" :max="200" />
        </el-form-item>
        <el-form-item label="检索片段数">
          <el-input-number v-model="form.rag_top_k" :min="1" :max="20" />
        </el-form-item>
        <el-form-item label="向量检索（P1）">
          <el-switch v-model="form.vector_search" />
          <div class="form-tip">默认关闭（零内存）；开启后需下载嵌入模型，仅供高级检索场景</div>
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
      <el-descriptions :column="2" border>
        <el-descriptions-item label="本地 Ollama">
          <el-tag :type="probeData.ollama?.ok ? 'success' : 'danger'">
            {{ probeData.ollama?.ok ? '已连接' : '未连接' }}
          </el-tag>
          <div class="form-tip">{{ probeData.ollama?.reason || '' }}</div>
        </el-descriptions-item>
        <el-descriptions-item label="云端 DeepSeek">
          <el-tag :type="probeData.deepseek?.ok ? 'success' : 'danger'">
            {{ probeData.deepseek?.ok ? '已配置' : '未配置' }}
          </el-tag>
          <div class="form-tip">{{ probeData.deepseek?.reason || '' }}</div>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>使用说明</template>
      <ol class="help-list">
        <li><b>本地 AI（推荐免费场景）</b>：安装 <a href="https://ollama.com" target="_blank">Ollama</a> 后运行 <code>ollama pull qwen2.5:3b</code>，再在此页切换为"本地"模式。</li>
        <li><b>云端 AI（推荐高质量场景）</b>：到 <a href="https://platform.deepseek.com" target="_blank">DeepSeek 开放平台</a> 注册并获取 API Key，填入上方并切换"云端"模式。</li>
        <li><b>数据位置</b>：所有资料与数据保存在 <code>backend/data/</code>，备份时复制该目录即可。</li>
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
  llm_mode: 'local',
  deepseek_api_key: '',
  ollama_model: 'qwen2.5:3b-instruct',
  daily_new_cards: 20,
  rag_top_k: 5,
  vector_search: false,
})
const probeData = ref({})

const load = async () => {
  try {
    const s = await getSettings()
    form.value = {
      llm_mode: s.llm_mode,
      deepseek_api_key: s.deepseek_api_key === '' ? '' : '', // 脱敏，重新填写才更新
      ollama_model: s.ollama_model,
      daily_new_cards: parseInt(s.daily_new_cards),
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
      llm_mode: form.value.llm_mode,
      deepseek_api_key: form.value.deepseek_api_key || undefined,
      ollama_model: form.value.ollama_model,
      daily_new_cards: form.value.daily_new_cards,
      rag_top_k: form.value.rag_top_k,
      vector_search: form.value.vector_search,
    })
    ElMessage.success('设置已保存')
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
.form-tip { color: #909399; font-size: 12px; margin-top: 4px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.help-list { line-height: 2; padding-left: 20px; }
.help-list code { background: #f5f7fa; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
</style>
