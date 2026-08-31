import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const stats = readFileSync(resolve(here, '../src/views/StatsView.vue'), 'utf8')
const navigation = readFileSync(resolve(here, '../src/components/AppNavigation.vue'), 'utf8')
const router = readFileSync(resolve(here, '../src/router/index.js'), 'utf8')
const api = readFileSync(resolve(here, '../src/api/index.js'), 'utf8')

test('learning plan module is absent from the product surface', () => {
  assert.equal(existsSync(resolve(here, '../src/views/PlanView.vue')), false)
  assert.doesNotMatch(navigation, /学习计划|index="\/plan"/)
  assert.doesNotMatch(router, /path: '\/plan'/)
  assert.doesNotMatch(api, /getPlan|savePlan|planCheckin/)
})

test('statistics are redesigned as knowledge-base insights', () => {
  for (const label of ['知识库洞察', '资料到知识的覆盖', '优先治理', '知识活动', '知识对象构成', '研究与输出']) {
    assert.match(stats, new RegExp(label))
  }
  assert.match(stats, /getKnowledgeBaseInsights/)
  assert.match(stats, /来源可追溯率/)
  assert.match(stats, /不以在线时长或连续打卡评价质量/)
  assert.match(navigation, /知识库洞察/)
  assert.match(router, /title: '知识库洞察'/)
})
