// 问答范围 → 后端请求契约。
// 三种范围互斥：书架/项目走 scope_type + scope_id；单本资料走 book_id；
// “全部资料”三者都为 null。ChatView 只从这里取请求体，避免各处手写字段导致串范围。
const SCOPE_KINDS = new Set(['shelf', 'project'])

const toPositiveInt = (value) => {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : null
}

export const BOOK_STATUS_LABELS = {
  pending: '排队中',
  parsing: '解析中',
  needs_ocr: '等待 OCR',
  failed: '解析失败',
  ready: '已就绪',
}

export function buildChatRequest(state = {}) {
  const scopeType = String(state.scopeType || '')
  if (SCOPE_KINDS.has(scopeType)) {
    const scopeId = toPositiveInt(state.scopeId)
    return scopeId
      ? { book_id: null, scope_type: scopeType, scope_id: scopeId }
      : { book_id: null, scope_type: null, scope_id: null }
  }
  const bookId = toPositiveInt(state.bookId)
  if (bookId) return { book_id: bookId, scope_type: null, scope_id: null }
  return { book_id: null, scope_type: null, scope_id: null }
}

export function buildHistoryQuery(state = {}) {
  const request = buildChatRequest(state)
  const params = {
    page: toPositiveInt(state.page) || 1,
    page_size: toPositiveInt(state.pageSize) || 20,
  }
  if (request.scope_type) {
    params.scope_type = request.scope_type
    params.scope_id = request.scope_id
  } else if (request.book_id) {
    params.book_id = request.book_id
  }
  return params
}

// scopeValue 形如 all / shelf:3 / project:5 / book:7。
// 选择器的 v-model 与请求契约之间的唯一转换点。
export function scopeState(scopeValue) {
  const raw = String(scopeValue || '')
  const separator = raw.indexOf(':')
  if (separator > 0) {
    const kind = raw.slice(0, separator)
    const id = toPositiveInt(raw.slice(separator + 1))
    if (id && SCOPE_KINDS.has(kind)) return { scopeType: kind, scopeId: id, bookId: null }
    if (id && kind === 'book') return { scopeType: '', scopeId: null, bookId: id }
  }
  return { scopeType: '', scopeId: null, bookId: null }
}

// 路由 bookId 的单独校验：不依赖“前 100 条 ready 列表”。
// 返回 ok=false 时调用方必须显示原因并禁止发送，不能回退到 all/localStorage。
export function evaluateRouteBook({ bookId, book = null, error = null } = {}) {
  const id = toPositiveInt(bookId)
  if (!id) {
    return { ok: false, reason: '链接中的文献编号无效，无法确定提问范围。请回到资料库重新打开文献，或在左侧选择提问范围。' }
  }
  if (error) {
    if (error.status === 404 || error.status === 410) {
      return { ok: false, reason: `链接指向的文献（编号 ${id}）不存在或已被删除，无法围绕它提问。请在左侧选择其他提问范围。` }
    }
    const detail = error.message ? `：${error.message}` : ''
    return { ok: false, reason: `无法加载链接指向的文献（编号 ${id}）${detail}。请检查后端服务与网络后重试，或在左侧选择其他提问范围。` }
  }
  if (!book) {
    return { ok: false, reason: `无法加载链接指向的文献（编号 ${id}），请重试或在左侧选择其他提问范围。` }
  }
  if (book.status !== 'ready') {
    const label = BOOK_STATUS_LABELS[book.status] || book.status || '未知状态'
    const name = book.title ? `《${book.title}》` : `文献（编号 ${id}）`
    const failedDetail = book.status === 'failed' && book.error_msg ? `（${book.error_msg}）` : ''
    return { ok: false, reason: `${name}当前状态为「${label}」${failedDetail}，还不能围绕它提问。请等待解析完成后重试，或在左侧选择其他提问范围。` }
  }
  return { ok: true, reason: '', book }
}

// 历史列表分页合并：只有最新一页响应能走到这里（序号守卫在 flow 里），
// 纯函数便于断言“加载更早是追加、切范围是替换、total 随之更新”。
export function mergeHistoryPage({ current = [], page = 1, append = false, result = null } = {}) {
  const items = result?.items || []
  return {
    items: append ? [...current, ...items] : items,
    page,
    total: result?.total || 0,
  }
}

// 范围助手（全文研读/记忆/关联）的连接列表缓存键。
export function buildConnectionsKey({ scopeType, scopeId, status = null, memories = [], now = Date.now() }) {
  const prepared = (status?.books || [])
    .filter((book) => book.state === 'prepared')
    .map((book) => book.artifact_id)
  const memoryFingerprint = (memories || []).map((memory) => [memory.id, memory.updated_at])
  return JSON.stringify([
    scopeType,
    scopeId,
    prepared,
    memoryFingerprint,
    scopeType === 'project' ? Math.floor(now / 60000) : 0,
  ])
}
