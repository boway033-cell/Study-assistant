import test from 'node:test'
import assert from 'node:assert/strict'
import { clarCandidates, clarifiedQuestion, citationAuditLabel } from '../src/utils/chatQa.js'

test('SSE done and history payload both expose nested clarification candidates', () => {
  const payload = { qa: { intent: 'clarify', clarification_options: ['《甲》 第一章', '《乙》'] } }
  assert.deepEqual(clarCandidates(payload), ['《甲》 第一章', '《乙》'])
  assert.deepEqual(clarCandidates(payload.qa), ['《甲》 第一章', '《乙》'])
  assert.deepEqual(clarCandidates({ qa: { clarification_options: null } }), [])
  assert.deepEqual(clarCandidates({ qa: { clarification_options: [null, 3, '', '《甲》'] } }), ['《甲》'])
})

test('choosing a candidate retains the original question', () => {
  assert.equal(clarifiedQuestion({ originalQuestion: '它的局限是什么' }, '《甲》'), '关于《甲》：它的局限是什么')
  assert.equal(clarifiedQuestion({ qa: { question: '两者哪个更重要' } }, '《乙》'), '关于《乙》：两者哪个更重要')
})

test('citation wording distinguishes numbering, lexical filtering and semantic support', () => {
  const label = citationAuditLabel({ verified: true, semantic_status: 'not_checked',
    support_method: 'lexical_overlap_proxy', support_rate: 1 })
  assert.match(label, /词面筛查 100%/)
  assert.match(label, /支撑判断仍需人工复核/)
  assert.match(citationAuditLabel({ verified: false }), /编号无效/)
  assert.equal(citationAuditLabel({ semantic_status: 'not_applicable' }), '本轮为澄清，未调用模型')
})
