import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const source = fs.readFileSync(new URL('../src/views/SettingsView.vue', import.meta.url), 'utf8')

test('model settings use one provider list instead of a duplicate connection table', () => {
  assert.match(source, /模型供应商/)
  assert.doesNotMatch(source, /<template #header>连接状态/)
  assert.match(source, /按功能选择模型/)
})

test('default text model is probed independently and automatically', () => {
  assert.match(source, /probeCompatibleProvider\(id\)/)
  assert.match(source, /await probeDefault\(\)/)
})

test('model routing exposes safe fallback, local usage, and capacity guardrails', () => {
  assert.match(source, /失败降级顺序/)
  assert.match(source, /fallback_provider_ids/)
  assert.match(source, /模型使用与故障/)
  assert.match(source, /知识库容量/)
  assert.match(source, /getCapacityStatus/)
})

test('custom providers discover models before save and allow model switching', () => {
  assert.match(source, /discoverProviderModels/)
  assert.match(source, /识别模型/)
  assert.match(source, /autoSyncModels/)
  assert.match(source, /已识别 \{\{ discoveredModels\.length \}\} 个模型/)
  assert.doesNotMatch(source, /v-if="providerForm\.id"[^>]*>读取列表/)
})
