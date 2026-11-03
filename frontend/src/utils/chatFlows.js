// 异步请求流：序号守卫 + AbortController 的组合封装。
// 约定：apply / onLoading / onError 只会在“该请求仍是最新一次”时被调用，
// 因此组件里所有状态写入都可以安全地放到 apply 里，旧响应不会覆盖新状态。
import { createSequencer } from './requestSequencer.js'
import { buildConnectionsKey } from './chatScope.js'

export function createFlow({ fetcher, apply, onLoading = () => {}, onError = () => {} }) {
  const sequencer = createSequencer()
  let controller = null
  const invalidate = () => {
    sequencer.invalidate()
    controller?.abort()
    controller = null
    onLoading(false)
  }
  const run = async (input) => {
    const token = sequencer.next()
    controller?.abort()
    const local = new AbortController()
    controller = local
    onLoading(true)
    try {
      const result = await fetcher(input, { token, signal: local.signal })
      if (!sequencer.isCurrent(token) || local.signal.aborted) return null
      apply(result, input)
      return result
    } catch (error) {
      if (sequencer.isCurrent(token) && !local.signal.aborted) onError(error, input)
      return null
    } finally {
      if (sequencer.isCurrent(token)) onLoading(false)
    }
  }
  return { run, invalidate, isCurrent: (token) => sequencer.isCurrent(token) }
}

export function createHistoryFlow({ fetchPage, apply, onLoading = () => {}, onError = () => {} }) {
  return createFlow({ fetcher: fetchPage, apply, onLoading, onError })
}

export function createDetailFlow({ fetchDetail, apply, onError = () => {} }) {
  return createFlow({ fetcher: (id, context) => fetchDetail(id, context), apply, onError })
}

// 原文面板是两阶段请求：先拿分块原文，再按 chapter_id 补章节标题。
// 两个阶段之间都可能被新请求插队，所以每一步之后都要重新校验 token。
export function createSourceFlow({
  fetchOriginal,
  fetchChapterTitle,
  apply,
  onLoading = () => {},
  onError = () => {},
}) {
  const sequencer = createSequencer()
  let controller = null
  const invalidate = () => {
    sequencer.invalidate()
    controller?.abort()
    controller = null
    onLoading(false)
  }
  const run = async ({ bookId, chunkId, sourceMeta = null }) => {
    const token = sequencer.next()
    controller?.abort()
    const local = new AbortController()
    controller = local
    onLoading(true)
    try {
      const data = await fetchOriginal({ bookId, chunkId, sourceMeta, signal: local.signal })
      if (!sequencer.isCurrent(token) || local.signal.aborted) return null
      let chapterTitle = ''
      if (data?.chapter_id) {
        try {
          chapterTitle = await fetchChapterTitle({ bookId, chapterId: data.chapter_id, signal: local.signal })
        } catch {
          chapterTitle = ''
        }
      }
      if (!sequencer.isCurrent(token) || local.signal.aborted) return null
      const payload = { bookId, chunkId, sourceMeta, data, chapterTitle: chapterTitle || '' }
      apply(payload)
      return payload
    } catch (error) {
      if (sequencer.isCurrent(token) && !local.signal.aborted) onError(error)
      return null
    } finally {
      if (sequencer.isCurrent(token)) onLoading(false)
    }
  }
  return { run, invalidate, isCurrent: (token) => sequencer.isCurrent(token) }
}

// 范围助手：status + memories 并发，必要时再串行取 connections。
// 只有最新一次范围请求可以写 assistantStatus / memories / connections / connectionsKey。
export function createAssistantScopeFlow({
  fetchStatus,
  fetchMemories,
  fetchConnections,
  apply,
  buildKey = buildConnectionsKey,
  onError = () => {},
}) {
  const sequencer = createSequencer()
  let controller = null
  const invalidate = () => {
    sequencer.invalidate()
    controller?.abort()
    controller = null
  }
  const run = async ({ scopeType, scopeId, connectionsKey = '', now = Date.now() }) => {
    const token = sequencer.next()
    controller?.abort()
    const local = new AbortController()
    controller = local
    try {
      const [status, memories] = await Promise.all([
        fetchStatus({ scopeType, scopeId, signal: local.signal }),
        fetchMemories({ scopeType, scopeId, signal: local.signal }),
      ])
      if (!sequencer.isCurrent(token) || local.signal.aborted) return null
      const key = buildKey({ scopeType, scopeId, status, memories, now })
      let connections = []
      let connectionsFetched = false
      if (key !== connectionsKey) {
        connections = status?.prepared
          ? await fetchConnections({ scopeType, scopeId, signal: local.signal })
          : []
        if (!sequencer.isCurrent(token) || local.signal.aborted) return null
        connectionsFetched = true
      }
      apply({
        scopeType, scopeId, status, memories, connections,
        connectionsKey: key, connectionsFetched,
      })
      return { status, memories, connections, connectionsKey: key, connectionsFetched }
    } catch (error) {
      if (sequencer.isCurrent(token) && !local.signal.aborted) onError(error)
      return null
    }
  }
  return { run, invalidate, isCurrent: (token) => sequencer.isCurrent(token) }
}
