import test from 'node:test'
import assert from 'node:assert/strict'
import { createSequencer } from '../src/utils/requestSequencer.js'
import {
  createAssistantScopeFlow,
  createDetailFlow,
  createHistoryFlow,
  createSourceFlow,
} from '../src/utils/chatFlows.js'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

// 让所有已排队的 Promise 回调跑完（覆盖多级 await）
const flush = async () => {
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
}

test('createSequencer：只有最新 token 有效，invalidate 后旧 token 全部失效', () => {
  const seq = createSequencer()
  assert.equal(seq.next(), 1)
  assert.equal(seq.isCurrent(1), true)
  const token2 = seq.next()
  assert.equal(seq.isCurrent(1), false)
  assert.equal(seq.isCurrent(token2), true)
  seq.invalidate()
  assert.equal(seq.isCurrent(token2), false)
  assert.equal(seq.isCurrent(0), false)
  const token3 = seq.next()
  assert.equal(seq.isCurrent(token3), true)
})

test('history 列表：A 慢 B 快，A 后完成也不能覆盖 B 的列表', async () => {
  const pending = new Map()
  const applied = []
  const loadings = []
  const flow = createHistoryFlow({
    fetchPage: (input) => {
      const entry = deferred()
      pending.set(input.params.book_id, entry)
      return entry.promise
    },
    apply: (result, input) => applied.push({ bookId: input.params.book_id, items: result.items }),
    onLoading: (value) => loadings.push(value),
  })

  const runA = flow.run({ page: 1, append: false, params: { book_id: 1, page: 1 } })
  const runB = flow.run({ page: 1, append: false, params: { book_id: 2, page: 1 } })
  assert.deepEqual(loadings, [true, true])

  pending.get(2).resolve({ items: [{ id: 'B' }], total: 1 })
  await flush()
  assert.deepEqual(applied, [{ bookId: 2, items: [{ id: 'B' }] }])
  assert.equal(loadings.at(-1), false)

  // A 的响应晚到：既不能覆盖列表，也不能把 loading 改回去
  pending.get(1).resolve({ items: [{ id: 'A' }], total: 1 })
  await flush()
  assert.deepEqual(applied, [{ bookId: 2, items: [{ id: 'B' }] }])
  assert.deepEqual(loadings, [true, true, false])
  await Promise.all([runA, runB])
})

test('history 详情：慢请求的详情不能覆盖后点的历史', async () => {
  const pending = new Map()
  const applied = []
  const flow = createDetailFlow({
    fetchDetail: (id) => {
      const entry = deferred()
      pending.set(id, entry)
      return entry.promise
    },
    apply: (detail, id) => applied.push({ id, detail }),
    onError: () => {},
  })

  const runA = flow.run(1)
  const runB = flow.run(2)
  pending.get(2).resolve({ answer: 'B' })
  await flush()
  pending.get(1).resolve({ answer: 'A' })
  await flush()
  assert.deepEqual(applied, [{ id: 2, detail: { answer: 'B' } }])
  await Promise.all([runA, runB])
})

test('source 面板：原文 → 章节标题两阶段之间被新引用插队，旧请求不得写面板', async () => {
  const originals = new Map()
  const chapters = new Map()
  const applied = []
  const loadings = []
  const flow = createSourceFlow({
    fetchOriginal: ({ bookId }) => {
      const entry = deferred()
      originals.set(bookId, entry)
      return entry.promise
    },
    fetchChapterTitle: ({ bookId }) => {
      const entry = deferred()
      chapters.set(bookId, entry)
      return entry.promise
    },
    apply: (payload) => applied.push(payload),
    onLoading: (value) => loadings.push(value),
  })

  const runA = flow.run({ bookId: 1, chunkId: 11 })
  originals.get(1).resolve({ chapter_id: 1, content: 'A 正文' })
  await flush() // A 卡在章节标题请求上；新引用 B 开始
  const runB = flow.run({ bookId: 2, chunkId: 22 })
  originals.get(2).resolve({ content: 'B 正文' })
  await flush()
  assert.deepEqual(applied.map((item) => item.bookId), [2])
  assert.equal(applied[0].data.content, 'B 正文')

  chapters.get(1).resolve('A 章节')
  await flush()
  assert.deepEqual(applied.map((item) => item.bookId), [2])
  assert.equal(loadings.at(-1), false)
  await Promise.all([runA, runB])
})

test('source 面板：A 的原文响应晚于 B 完成时，不改写 B 的面板', async () => {
  const originals = new Map()
  const applied = []
  const flow = createSourceFlow({
    fetchOriginal: ({ bookId }) => {
      const entry = deferred()
      originals.set(bookId, entry)
      return entry.promise
    },
    fetchChapterTitle: async () => '',
    apply: (payload) => applied.push(payload),
  })

  const runA = flow.run({ bookId: 3, chunkId: 33 })
  const runB = flow.run({ bookId: 4, chunkId: 44 })
  originals.get(4).resolve({ content: 'D' })
  await flush()
  originals.get(3).resolve({ content: 'C' })
  await flush()
  assert.deepEqual(applied.map((item) => item.bookId), [4])
  await Promise.all([runA, runB])
})

test('scope 切换：慢的旧范围请求不能覆盖新范围的助手状态', async () => {
  const statuses = new Map()
  const memories = new Map()
  const applied = []
  const flow = createAssistantScopeFlow({
    fetchStatus: ({ scopeId }) => {
      const entry = deferred()
      statuses.set(scopeId, entry)
      return entry.promise
    },
    fetchMemories: ({ scopeId }) => {
      const entry = deferred()
      memories.set(scopeId, entry)
      return entry.promise
    },
    fetchConnections: async () => [],
    buildKey: ({ scopeType, scopeId, status }) => JSON.stringify([scopeType, scopeId, status?.books || []]),
    apply: (payload) => applied.push(payload),
    onError: () => {},
  })

  const runA = flow.run({ scopeType: 'shelf', scopeId: 1, connectionsKey: '' })
  const runB = flow.run({ scopeType: 'shelf', scopeId: 2, connectionsKey: '' })
  statuses.get(2).resolve({ prepared: 0, books: [] })
  memories.get(2).resolve([{ id: 'm2' }])
  await flush()
  statuses.get(1).resolve({ prepared: 0, books: [] })
  memories.get(1).resolve([{ id: 'm1' }])
  await flush()

  assert.equal(applied.length, 1)
  assert.equal(applied[0].scopeId, 2)
  assert.deepEqual(applied[0].memories, [{ id: 'm2' }])
  await Promise.all([runA, runB])
})

test('scope 切换：旧范围卡在 connections 阶段时也不能在切换后写入', async () => {
  const statuses = new Map()
  const memories = new Map()
  const connections = new Map()
  const applied = []
  const flow = createAssistantScopeFlow({
    fetchStatus: ({ scopeId }) => {
      const entry = deferred()
      statuses.set(scopeId, entry)
      return entry.promise
    },
    fetchMemories: ({ scopeId }) => {
      const entry = deferred()
      memories.set(scopeId, entry)
      return entry.promise
    },
    fetchConnections: ({ scopeId }) => {
      const entry = deferred()
      connections.set(scopeId, entry)
      return entry.promise
    },
    buildKey: ({ scopeType, scopeId }) => JSON.stringify([scopeType, scopeId, Date.now()]),
    apply: (payload) => applied.push(payload),
    onError: () => {},
  })

  const runA = flow.run({ scopeType: 'shelf', scopeId: 3, connectionsKey: '' })
  statuses.get(3).resolve({ prepared: 1, books: [{ state: 'prepared', artifact_id: 'a3' }] })
  memories.get(3).resolve([])
  await flush() // A 正在等 connections

  const runB = flow.run({ scopeType: 'shelf', scopeId: 4, connectionsKey: '' })
  statuses.get(4).resolve({ prepared: 0, books: [] })
  memories.get(4).resolve([])
  await flush()
  assert.equal(applied.length, 1)
  assert.equal(applied[0].scopeId, 4)

  connections.get(3).resolve([{ id: 'c3' }])
  await flush()
  assert.equal(applied.length, 1)
  assert.equal(applied[0].scopeId, 4)
  await Promise.all([runA, runB])
})

test('invalidate：卸载/切范围后所有在途响应都不能再写状态', async () => {
  const pending = deferred()
  const applied = []
  const flow = createDetailFlow({
    fetchDetail: () => pending.promise,
    apply: (detail) => applied.push(detail),
  })
  const run = flow.run(9)
  flow.invalidate()
  pending.resolve({ answer: '迟到' })
  await flush()
  assert.deepEqual(applied, [])
  await run
})
