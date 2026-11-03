// Sensemaking 证据 → ChatView 档案路由 query 的纯函数映射。
//
// 后端 sensemaking 服务把证据的内容指纹保存在 evidence.source_hash
// （sha256(utf-8 正文) hex，与 backend/app/services/source_identity.py:content_hash 定义一致）。
// 这份指纹就是 ChatView 打开档案时要传给 get_chunk_original 的 content_hash，
// 用来判断引用时的正文是否仍可核验。
//
// 当前 artifact 不保存 SourceVersion.version_uid（只能拿到整篇 digest 与 stale_chunk_ids），
// 所以通常没有版本号可传；只有证据里真实存在且格式合法时才传，绝不伪造。
const SHA256_HEX = /^[a-f0-9]{64}$/i
const VERSION_UID_HEX = /^[a-f0-9]{32}$/i

export function normalizeContentHash(value) {
  if (typeof value !== 'string') return ''
  const trimmed = value.trim().toLowerCase()
  return SHA256_HEX.test(trimmed) ? trimmed : ''
}

export function normalizeVersionUid(value) {
  if (typeof value !== 'string') return ''
  const trimmed = value.trim().toLowerCase()
  return VERSION_UID_HEX.test(trimmed) ? trimmed : ''
}

// 返回可直接交给 router.push({ path: '/chat', query }) 的 query 对象。
// 证据缺少可核验定位（book_id/chunk_id 非法）时返回 null：宁可不跳转，也不带假指纹。
export function buildArchiveJumpQuery({ evidence, statement } = {}) {
  if (!evidence || typeof evidence !== 'object') return null
  const bookId = Number(evidence.book_id)
  const chunkId = Number(evidence.chunk_id)
  if (!Number.isInteger(bookId) || bookId <= 0) return null
  if (!Number.isInteger(chunkId) || chunkId <= 0) return null
  const query = {
    archiveBookId: bookId,
    archiveChunkId: chunkId,
    archiveQuote: typeof evidence.quote === 'string' ? evidence.quote : '',
    archiveStatement: typeof statement === 'string' ? statement : '',
  }
  const hash = normalizeContentHash(evidence.content_hash ?? evidence.source_hash)
  if (hash) query.archiveContentHash = hash
  const version = normalizeVersionUid(evidence.source_version_uid ?? evidence.version_uid)
  if (version) query.archiveVersionUid = version
  return query
}
