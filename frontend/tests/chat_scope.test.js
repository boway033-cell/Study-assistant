import test from 'node:test'
import assert from 'node:assert/strict'
import {
  BOOK_STATUS_LABELS,
  buildChatRequest,
  buildConnectionsKey,
  buildHistoryQuery,
  evaluateRouteBook,
  mergeHistoryPage,
  scopeState,
} from '../src/utils/chatScope.js'

test('buildChatRequest：书架范围只带 scope_type/scope_id，book_id 为 null', () => {
  assert.deepEqual(
    buildChatRequest({ scopeType: 'shelf', scopeId: 3, bookId: 7 }),
    { book_id: null, scope_type: 'shelf', scope_id: 3 },
  )
})

test('buildChatRequest：项目范围只带 scope_type/scope_id，book_id 为 null', () => {
  assert.deepEqual(
    buildChatRequest({ scopeType: 'project', scopeId: '5' }),
    { book_id: null, scope_type: 'project', scope_id: 5 },
  )
})

test('buildChatRequest：单本资料范围 book_id=该书，scope_type/scope_id 都为 null', () => {
  assert.deepEqual(
    buildChatRequest({ scopeType: '', scopeId: null, bookId: '12' }),
    { book_id: 12, scope_type: null, scope_id: null },
  )
})

test('buildChatRequest：全部资料三者都是 null', () => {
  assert.deepEqual(buildChatRequest({}), { book_id: null, scope_type: null, scope_id: null })
  assert.deepEqual(buildChatRequest({ scopeType: '', scopeId: null, bookId: null }),
    { book_id: null, scope_type: null, scope_id: null })
  // shelf/project 的 id 非法时不能悄悄退化成“全部资料以外的范围”
  assert.deepEqual(buildChatRequest({ scopeType: 'shelf', scopeId: 'bad', bookId: 3 }),
    { book_id: null, scope_type: null, scope_id: null })
})

test('scopeState：选择器取值到请求契约只有一处转换', () => {
  assert.deepEqual(scopeState('all'), { scopeType: '', scopeId: null, bookId: null })
  assert.deepEqual(scopeState('shelf:3'), { scopeType: 'shelf', scopeId: 3, bookId: null })
  assert.deepEqual(scopeState('project:5'), { scopeType: 'project', scopeId: 5, bookId: null })
  assert.deepEqual(scopeState('book:7'), { scopeType: '', scopeId: null, bookId: 7 })
  assert.deepEqual(scopeState('book:abc'), { scopeType: '', scopeId: null, bookId: null })
})

test('buildHistoryQuery：历史列表与提问使用同一套范围契约', () => {
  assert.deepEqual(
    buildHistoryQuery({ scopeType: '', scopeId: null, bookId: 9, page: 2, pageSize: 20 }),
    { page: 2, page_size: 20, book_id: 9 },
  )
  assert.deepEqual(
    buildHistoryQuery({ scopeType: 'shelf', scopeId: 3, bookId: null, page: 1, pageSize: 20 }),
    { page: 1, page_size: 20, scope_type: 'shelf', scope_id: 3 },
  )
  assert.deepEqual(
    buildHistoryQuery({}),
    { page: 1, page_size: 20 },
  )
})

test('evaluateRouteBook：路由 bookId 单独校验，不依赖前 100 条 ready 列表', () => {
  // 这本书不在列表中（也没有传列表），仍能通过校验；请求体仍按书范围构造。
  const verdict = evaluateRouteBook({ bookId: 150, book: { id: 150, title: '第 150 本书', status: 'ready' } })
  assert.equal(verdict.ok, true)
  assert.equal(verdict.book.id, 150)
  assert.deepEqual(buildChatRequest({ bookId: 150 }), { book_id: 150, scope_type: null, scope_id: null })
})

test('evaluateRouteBook：404/已删除给出明确原因，调用方据此禁用发送', () => {
  const verdict = evaluateRouteBook({ bookId: 42, error: Object.assign(new Error('内容不存在'), { status: 404 }) })
  assert.equal(verdict.ok, false)
  assert.match(verdict.reason, /42/)
  assert.match(verdict.reason, /不存在或已被删除/)
})

test('evaluateRouteBook：网络失败给出明确原因，包含后端错误信息', () => {
  const verdict = evaluateRouteBook({ bookId: 42, error: new Error('Network Error') })
  assert.equal(verdict.ok, false)
  assert.match(verdict.reason, /无法加载/)
  assert.match(verdict.reason, /Network Error/)
})

test('evaluateRouteBook：status 非 ready 时说明当前状态并禁止发送', () => {
  const parsing = evaluateRouteBook({ bookId: 8, book: { id: 8, title: '慢书', status: 'parsing' } })
  assert.equal(parsing.ok, false)
  assert.match(parsing.reason, /《慢书》/)
  assert.match(parsing.reason, /解析中/)
  assert.equal(BOOK_STATUS_LABELS.parsing, '解析中')

  const failed = evaluateRouteBook({ bookId: 8, book: { id: 8, title: '坏书', status: 'failed', error_msg: 'PDF 损坏' } })
  assert.match(failed.reason, /解析失败/)
  assert.match(failed.reason, /PDF 损坏/)
})

test('evaluateRouteBook：非法或缺失 bookId 不能静默当作全部资料', () => {
  const verdict = evaluateRouteBook({ bookId: 'abc' })
  assert.equal(verdict.ok, false)
  assert.match(verdict.reason, /编号无效/)
  assert.equal(evaluateRouteBook({ bookId: 0 }).ok, false)
})

test('mergeHistoryPage：切范围替换列表、加载更早追加列表，total 跟随最新响应', () => {
  const first = mergeHistoryPage({
    current: [],
    page: 1,
    append: false,
    result: { items: [{ id: 'a' }, { id: 'b' }], total: 5 },
  })
  assert.deepEqual(first, { items: [{ id: 'a' }, { id: 'b' }], page: 1, total: 5 })
  const more = mergeHistoryPage({ current: first.items, page: 2, append: true, result: { items: [{ id: 'c' }], total: 5 } })
  assert.deepEqual(more.items, [{ id: 'a' }, { id: 'b' }, { id: 'c' }])
  assert.equal(more.page, 2)
  // 切到别的范围时不能追加旧范围的记录
  const switched = mergeHistoryPage({ current: more.items, page: 1, append: false, result: { items: [{ id: 'x' }], total: 1 } })
  assert.deepEqual(switched, { items: [{ id: 'x' }], page: 1, total: 1 })
  // 空响应也要把 total/page 落到实处，不能保留旧范围的 loading/total
  assert.deepEqual(mergeHistoryPage({ current: more.items, page: 1, append: false, result: null }),
    { items: [], page: 1, total: 0 })
})

test('buildConnectionsKey：研读产物/记忆变化才改变缓存键', () => {
  const base = {
    scopeType: 'shelf', scopeId: 3,
    status: { books: [{ state: 'prepared', artifact_id: 'a1' }] },
    memories: [{ id: 1, updated_at: 't1' }],
    now: 1000,
  }
  const first = buildConnectionsKey(base)
  assert.equal(buildConnectionsKey({ ...base }), first)
  assert.notEqual(buildConnectionsKey({ ...base, memories: [{ id: 1, updated_at: 't2' }] }), first)
  assert.notEqual(buildConnectionsKey({ ...base, status: { books: [{ state: 'prepared', artifact_id: 'a2' }] } }), first)
})
