// 引用身份（P1-1）的前端契约：后端 getChunkOriginal 返回
// identity_status / identity_verified / content(可为 null) / message / content_hash / source_version_uid。
// 旧后端没有这些字段时按 legacy_unverified 处理：正文照常显示，但必须明确标未验证。
export const IDENTITY_STATUSES = Object.freeze([
  'ok', 'legacy_unverified', 'stale', 'needs_review', 'invalid', 'missing',
])
const KNOWN_STATUSES = new Set(IDENTITY_STATUSES)

export function normalizeIdentityStatus(data) {
  if (!data) return 'missing'
  const raw = String(data.identity_status || data.status || '')
  if (KNOWN_STATUSES.has(raw)) return raw
  if (data.identity_verified === true) return 'ok'
  if (data.identity_verified === false) return 'needs_review'
  // 兼容尚未返回身份字段的旧后端：可展示，但标记为无法验证。
  return 'legacy_unverified'
}

// 把引用里保存的指纹传给 getChunkOriginal，后端据此判断是否仍是同一段原文。
// 注意：响应字段叫 source_version_uid，但 books.py get_chunk_original 的 query 参数名是
// version_uid（backend/tests/test_e2e_isolated.py 亦以 version_uid 调用），两者不能混用。
export function buildChunkOriginalQuery(source = {}) {
  const query = {}
  const hash = typeof source?.content_hash === 'string' ? source.content_hash.trim() : ''
  const version = typeof source?.source_version_uid === 'string'
    ? source.source_version_uid.trim()
    : (typeof source?.version_uid === 'string' ? source.version_uid.trim() : '')
  if (hash) query.content_hash = hash
  if (version) query.version_uid = version
  return query
}

// ChatView 从 SensemakingView 等入口收到的档案路由 query（archiveContentHash/archiveVersionUid）
// → getChunkOriginal 的 query 参数。vue-router 可能给出数组，这里只取第一个值。
const firstQueryValue = value => (Array.isArray(value) ? value[0] : value)
export function archiveRouteChunkOriginalQuery(routeQuery = {}) {
  return buildChunkOriginalQuery({
    content_hash: firstQueryValue(routeQuery?.archiveContentHash),
    source_version_uid: firstQueryValue(routeQuery?.archiveVersionUid),
  })
}

const withDetail = (base, detail) => (detail ? `${base}（${detail}）` : base)

const MESSAGES = {
  ok: '来源已校验',
  stale: '该书已重新解析；以下为引用时保存的原文',
  legacy_unverified: '旧引用无法验证身份，可能对应更新后的正文，请核对原文',
  staleMissingContent: '该书已重新解析，但未能取回引用时保存的原文；以下仅显示引用片段线索。',
  legacyMissingContent: '旧引用无法验证身份，也未能取回正文；以下仅显示引用片段线索。',
  invalid: '该引用已失效',
  needsReview: '该引用需复核',
}

const DETAILS = {
  invalid: '无法根据引用记录定位到引用时的原文',
  needs_review: '旧引用没有内容指纹，且该书已重新解析，无法确认原文',
  missing: '引用时所在的分块已不存在，也没有归档原文',
}

// 纯函数：把后端响应 + 引用里的 snippet 转成面板可直接渲染的展示模型。
// showContent=false 时调用方绝不能显示 data.content（它可能是更新后的正文）。
export function resolveCitationOriginal({ data = null, snippet = '' } = {}) {
  const status = normalizeIdentityStatus(data)
  const content = typeof data?.content === 'string' ? data.content : ''
  const backendMessage = typeof data?.message === 'string' ? data.message.trim() : ''
  const citationSnippet = String(snippet || '').trim()

  if (status === 'ok') {
    if (!content) {
      // 契约上 ok 应带正文；万一后端只给了消息，也不能留下空白面板。
      return {
        status, verified: true, tone: 'warning', label: MESSAGES.ok,
        message: backendMessage
          ? `${MESSAGES.ok}，但未能取回正文（${backendMessage}）；以下仅显示引用片段线索。`
          : `${MESSAGES.ok}，但未能取回正文；以下仅显示引用片段线索。`,
        showContent: false, content: '', snippet: citationSnippet,
      }
    }
    return {
      status, verified: true, tone: 'success', label: MESSAGES.ok,
      message: withDetail(MESSAGES.ok, backendMessage),
      showContent: true, content, snippet: '',
    }
  }
  if (status === 'stale') {
    return {
      status, verified: true, tone: 'warning', label: '已重新解析',
      message: content ? withDetail(MESSAGES.stale, backendMessage) : MESSAGES.staleMissingContent,
      showContent: Boolean(content), content: content || '', snippet: content ? '' : citationSnippet,
    }
  }
  if (status === 'legacy_unverified') {
    return {
      status, verified: false, tone: 'warning', label: '引用未验证',
      message: content ? withDetail(MESSAGES.legacy_unverified, backendMessage) : MESSAGES.legacyMissingContent,
      showContent: Boolean(content), content: content || '', snippet: content ? '' : citationSnippet,
    }
  }
  const base = status === 'needs_review' ? MESSAGES.needsReview : MESSAGES[status]
  return {
    status,
    verified: false,
    tone: 'error',
    label: status === 'needs_review' ? '需复核' : '已失效',
    message: `${base}：${backendMessage || DETAILS[status] || DETAILS.invalid}`,
    showContent: false,
    content: '',
    snippet: citationSnippet,
  }
}

// 档案/证据卡需要一段“引用原文”。身份不可信时只保留引用记录里的片段，绝不拿当前正文顶替。
export function citationQuoteCandidate({ data = null, preferred = '' } = {}) {
  const preferredText = String(preferred || '').trim()
  const resolved = resolveCitationOriginal({ data, snippet: preferredText })
  if (!resolved.showContent) return preferredText || resolved.snippet
  if (preferredText && resolved.content.includes(preferredText)) return preferredText
  return resolved.content.slice(0, 180).trim()
}
