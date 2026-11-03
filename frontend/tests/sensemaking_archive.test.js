import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildArchiveJumpQuery,
  normalizeContentHash,
  normalizeVersionUid,
} from '../src/utils/sensemakingArchive.js'

const HASH = 'a1'.repeat(32)
const VERSION = 'b2'.repeat(16)
const evidence = (extra = {}) => ({
  ref: 'B7:P3:C11', quote: '作者在原文中的短引', book_id: 7, chunk_id: 11, page: 3,
  source_hash: HASH, ...extra,
})

test('buildArchiveJumpQuery：把 artifact 证据的 source_hash 映射为 archiveContentHash', () => {
  const query = buildArchiveJumpQuery({ evidence: evidence(), statement: '作者的核心论点' })
  assert.deepEqual(query, {
    archiveBookId: 7,
    archiveChunkId: 11,
    archiveQuote: '作者在原文中的短引',
    archiveStatement: '作者的核心论点',
    archiveContentHash: HASH,
  })
  // 指纹必须恰好是后端 content_hash 的定义：sha256(utf-8) 的 64 位小写 hex
  assert.equal(normalizeContentHash(HASH), HASH)
  assert.equal(normalizeContentHash(HASH.toUpperCase()), HASH)
})

test('buildArchiveJumpQuery：没有任何可核验指纹时不伪造参数', () => {
  for (const hash of [undefined, null, '', '   ', 'abc', 'sha256:' + HASH, HASH.slice(1), HASH + 'x', 42]) {
    const query = buildArchiveJumpQuery({ evidence: evidence({ source_hash: hash }), statement: 's' })
    assert.ok(query, '位置合法就应该仍然允许跳转')
    assert.equal('archiveContentHash' in query, false, '不可核验的指纹不能传给后端：' + String(hash))
  }
})

test('buildArchiveJumpQuery：证据里的 content_hash 优先于旧字段 source_hash', () => {
  const other = 'c3'.repeat(32)
  const query = buildArchiveJumpQuery({ evidence: evidence({ content_hash: other }) })
  assert.equal(query.archiveContentHash, other)
})

test('buildArchiveJumpQuery：只有真实存在且格式合法的版本号才映射为 archiveVersionUid', () => {
  assert.equal('archiveVersionUid' in buildArchiveJumpQuery({ evidence: evidence() }), false)
  for (const uid of [undefined, null, '', 'v-1', 'not-a-uid', 'd4'.repeat(15), 'd4'.repeat(33)]) {
    const query = buildArchiveJumpQuery({ evidence: evidence({ source_version_uid: uid }) })
    assert.equal('archiveVersionUid' in query, false, '非法版本号不能传：' + String(uid))
  }
  assert.equal(buildArchiveJumpQuery({ evidence: evidence({ source_version_uid: VERSION }) }).archiveVersionUid, VERSION)
  // 兼容 source_version_uid 之外的字段名
  assert.equal(buildArchiveJumpQuery({ evidence: evidence({ version_uid: VERSION }) }).archiveVersionUid, VERSION)
  assert.equal(normalizeVersionUid(VERSION.toUpperCase()), VERSION)
})

test('buildArchiveJumpQuery：定位缺失或非法时返回 null，不带着假引用跳转', () => {
  for (const bad of [null, undefined, {}, 'x', [1]]) {
    assert.equal(buildArchiveJumpQuery({ evidence: bad }), null)
  }
  for (const pair of [[0, 1], [1, 0], [-1, 2], ['x', 2], [1, 'y'], [1.5, 2], [NaN, 2]]) {
    assert.equal(buildArchiveJumpQuery({ evidence: evidence({ book_id: pair[0], chunk_id: pair[1] }) }), null,
      '非法定位：' + JSON.stringify(pair))
  }
})

test('buildArchiveJumpQuery：id 归一化为数字，quote/statement 缺省为空串', () => {
  const query = buildArchiveJumpQuery({ evidence: { book_id: '12', chunk_id: '34', source_hash: HASH } })
  assert.deepEqual(query, {
    archiveBookId: 12, archiveChunkId: 34, archiveQuote: '', archiveStatement: '', archiveContentHash: HASH,
  })
  assert.deepEqual(Object.keys(query).sort(),
    ['archiveBookId', 'archiveChunkId', 'archiveContentHash', 'archiveQuote', 'archiveStatement'])
})
