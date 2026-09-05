import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const health = readFileSync(resolve(here, '../src/views/KnowledgeHealthView.vue'), 'utf8')
const notes = readFileSync(resolve(here, '../src/views/NotesView.vue'), 'utf8')
const study = readFileSync(resolve(here, '../src/views/StudyView.vue'), 'utf8')
const navigation = readFileSync(resolve(here, '../src/components/AppNavigation.vue'), 'utf8')
const router = readFileSync(resolve(here, '../src/router/index.js'), 'utf8')
const app = readFileSync(resolve(here, '../src/App.vue'), 'utf8')

test('knowledge health covers the six explainable audit categories', () => {
  for (const label of ['未解析', '低质量 OCR', '无目录', '无元数据', '重复资料', '失效锚点']) {
    assert.match(health, new RegExp(label))
  }
  assert.match(health, /getKnowledgeBaseHealth/)
  assert.match(health, /repairKnowledgeBaseHealth/)
  assert.match(health, /处理所选/)
  assert.match(health, /失效对象/)
  assert.match(navigation, /知识库健康/)
  assert.match(router, /path: '\/knowledge-health'/)
})

test('long-form notes and research reports expose immersive reading', () => {
  assert.match(notes, /size="100%"/)
  assert.match(notes, /76ch/)
  assert.match(study, /沉浸阅读/)
  assert.match(study, /v-model="immersive"/)
  assert.match(study, /76ch/)
})

test('reader keeps the full product navigation unless the user collapses it', () => {
  assert.match(app, /:width="sidebarCollapsed \? '76px' : '252px'"/)
  assert.match(app, /<AppNavigation :collapsed="sidebarCollapsed"/)
  assert.doesNotMatch(app, /AppNavigation :collapsed="\$route\.name === 'reader'/)
})
