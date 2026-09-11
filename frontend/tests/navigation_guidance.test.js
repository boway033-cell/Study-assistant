import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const navigation = readFileSync(resolve(here, '../src/components/AppNavigation.vue'), 'utf8')
const app = readFileSync(resolve(here, '../src/App.vue'), 'utf8')

test('sidebar presents one guided knowledge workflow before secondary capabilities', () => {
  for (const label of ['01</i> 资料入库', '02</i> 研读与沉淀', '03</i> 写作', '04</i> 文献汇报']) {
    assert.match(navigation, new RegExp(label))
  }
  assert.match(navigation, /当前路径/)
  assert.match(navigation, /先导入并确认解析就绪/)
  assert.match(navigation, /index="assist"/)
  assert.match(navigation, /index="optional"/)
  assert.match(navigation, /unique-opened/)
})

test('sidebar identity states the product purpose instead of seasonal status', () => {
  assert.match(app, /知识库助手/)
  assert.match(app, /资料 · 研读 · 输出/)
  assert.match(app, /'252px'/)
})
