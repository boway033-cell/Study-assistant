import test from 'node:test'
import assert from 'node:assert/strict'
import {
  IDENTITY_STATUSES,
  archiveRouteChunkOriginalQuery,
  buildChunkOriginalQuery,
  citationQuoteCandidate,
  normalizeIdentityStatus,
  resolveCitationOriginal,
} from '../src/utils/citationIdentity.js'

test('normalizeIdentityStatus：优先使用后端 identity_status，其次兼容旧字段', () => {
  for (const status of IDENTITY_STATUSES) {
    assert.equal(normalizeIdentityStatus({ identity_status: status, content: 'x' }), status)
  }
  // 旧响应完全不带身份字段：可显示但必须标“未验证”，不能默认 ok
  assert.equal(normalizeIdentityStatus({ content: 'x' }), 'legacy_unverified')
  // 只有 identity_verified 字段时的降级
  assert.equal(normalizeIdentityStatus({ identity_verified: true, content: 'x' }), 'ok')
  assert.equal(normalizeIdentityStatus({ identity_verified: false, content: 'x' }), 'needs_review')
  assert.equal(normalizeIdentityStatus(null), 'missing')
})

test('resolveCitationOriginal：ok 正常显示正文，并标“来源已校验”', () => {
  const identity = resolveCitationOriginal({ data: { identity_status: 'ok', identity_verified: true, content: '当前原文' }, snippet: '引用片段' })
  assert.equal(identity.tone, 'success')
  assert.equal(identity.showContent, true)
  assert.equal(identity.content, '当前原文')
  assert.match(identity.message, /来源已校验/)
})

test('resolveCitationOriginal：legacy_unverified 黄色警告，但仍显示正文供核对', () => {
  const identity = resolveCitationOriginal({ data: { identity_status: 'legacy_unverified', content: '当前正文' } })
  assert.equal(identity.tone, 'warning')
  assert.equal(identity.showContent, true)
  assert.equal(identity.content, '当前正文')
  assert.match(identity.message, /旧引用无法验证身份/)
  assert.match(identity.message, /请核对原文/)
})

test('resolveCitationOriginal：stale 黄色警告并显示后端返回的归档原文', () => {
  const identity = resolveCitationOriginal({ data: { identity_status: 'stale', content: '引用时保存的原文' } })
  assert.equal(identity.tone, 'warning')
  assert.equal(identity.showContent, true)
  assert.equal(identity.content, '引用时保存的原文')
  assert.match(identity.message, /该书已重新解析/)
  assert.match(identity.message, /引用时保存的原文/)
})

test('resolveCitationOriginal：stale/legacy 拿不到正文时只显示引用片段线索', () => {
  for (const status of ['stale', 'legacy_unverified']) {
    const identity = resolveCitationOriginal({ data: { identity_status: status, content: null }, snippet: '引用记录里的片段' })
    assert.equal(identity.showContent, false)
    assert.equal(identity.content, '')
    assert.match(identity.message, /仅显示引用片段线索/)
    assert.deepEqual([identity.snippet], ['引用记录里的片段'])
  }
})

test('resolveCitationOriginal：invalid/needs_review/missing 红色提示，绝不显示当前正文', () => {
  for (const status of ['invalid', 'needs_review', 'missing']) {
    const identity = resolveCitationOriginal({
      data: { identity_status: status, content: '更新后的当前正文', message: '后端说明' },
      snippet: 'snippet 线索',
    })
    assert.equal(identity.tone, 'error')
    assert.equal(identity.showContent, false)
    assert.equal(identity.content, '')
    assert.doesNotMatch(identity.message, /更新后的当前正文/)
    assert.deepEqual([identity.snippet], ['snippet 线索'])
  }
  const invalid = resolveCitationOriginal({ data: { identity_status: 'invalid', message: 'chunk 不存在' } })
  assert.match(invalid.message, /该引用已失效/)
  assert.match(invalid.message, /chunk 不存在/)
  const review = resolveCitationOriginal({ data: { identity_status: 'needs_review' } })
  assert.match(review.message, /该引用需复核/)
})

test('resolveCitationOriginal：content 为 null 时 ok/legacy 也不能显示空白面板', () => {
  const ok = resolveCitationOriginal({ data: { identity_status: 'ok', content: null }, snippet: '线索' })
  assert.equal(ok.showContent, false)
  assert.equal(ok.snippet, '线索')
  const legacy = resolveCitationOriginal({ data: { identity_status: 'legacy_unverified', content: null }, snippet: '线索' })
  assert.equal(legacy.showContent, false)
  assert.equal(legacy.snippet, '线索')
})

test('buildChunkOriginalQuery：把引用里的指纹作为 query 参数传给后端', () => {
  // 响应字段 source_version_uid 必须映射成后端 query 参数 version_uid（见 books.py get_chunk_original），
  // 否则版本号会被 FastAPI 静默忽略，退化成"任意归档"匹配。
  assert.deepEqual(
    buildChunkOriginalQuery({ content_hash: 'sha256:abc', source_version_uid: 'v-1' }),
    { content_hash: 'sha256:abc', version_uid: 'v-1' },
  )
  // 兼容旧字段名 version_uid，且空值不发送
  assert.deepEqual(buildChunkOriginalQuery({ version_uid: 'v-2' }), { version_uid: 'v-2' })
  assert.deepEqual(buildChunkOriginalQuery({}), {})
  assert.deepEqual(buildChunkOriginalQuery(null), {})
  assert.deepEqual(buildChunkOriginalQuery({ content_hash: '  ', source_version_uid: '' }), {})
})

test('archiveRouteChunkOriginalQuery：路由档案参数映射为 getChunkOriginal 的 query', () => {
  assert.deepEqual(
    archiveRouteChunkOriginalQuery({ archiveContentHash: 'abc123'.padEnd(64, '0'), archiveVersionUid: 'v-9' }),
    { content_hash: 'abc123'.padEnd(64, '0'), version_uid: 'v-9' },
  )
  // vue-router 可能把重复 query 给成数组：只取第一个，不能把数组当参数发出去
  assert.deepEqual(
    archiveRouteChunkOriginalQuery({ archiveContentHash: ['h-1', 'h-2'], archiveVersionUid: ['v-1'] }),
    { content_hash: 'h-1', version_uid: 'v-1' },
  )
  // 只有引用定位、没有指纹时（旧 Sensemaking 路由）：不发送伪造参数
  assert.deepEqual(archiveRouteChunkOriginalQuery({ archiveBookId: '1', archiveChunkId: '2' }), {})
  assert.deepEqual(archiveRouteChunkOriginalQuery({}), {})
  assert.deepEqual(archiveRouteChunkOriginalQuery(null), {})
})

test('citationQuoteCandidate：身份不可信时只保留引用片段，不拿当前正文顶替', () => {
  const staleArchive = { identity_status: 'stale', content: '引用时保存的原文内容……' }
  assert.equal(citationQuoteCandidate({ data: staleArchive, preferred: '' }), '引用时保存的原文内容……')

  const untrusted = { identity_status: 'invalid', content: '更新后的当前正文' }
  assert.equal(citationQuoteCandidate({ data: untrusted, preferred: '引用里的片段' }), '引用里的片段')
  assert.equal(citationQuoteCandidate({ data: untrusted, preferred: '' }), '')

  // ok 时优先用引用里保存的片段（若确实包含在正文中）
  const ok = { identity_status: 'ok', content: '整段原文，其中包含引用片段。' }
  assert.equal(citationQuoteCandidate({ data: ok, preferred: '其中包含引用片段' }), '其中包含引用片段')
  // 引用片段与正文不一致时退回归档正文开头，且不做 180 字以上截断展示
  assert.equal(citationQuoteCandidate({ data: { identity_status: 'ok', content: '正文'.repeat(200) }, preferred: '' }).length, 180)
})
