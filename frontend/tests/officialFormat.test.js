import test from 'node:test'
import assert from 'node:assert/strict'
import { formatChanges, formatDiff } from '../src/utils/officialFormat.js'

test('format overrides contain only changed leaves, not fixed fields', () => {
  const preset = { schema_version: 1, page: { size: 'A4', margins_cm: { top: 3.7 } }, global: { bold: true } }
  const changed = { ...preset, global: { bold: false } }
  assert.deepEqual(formatDiff(changed, preset), { global: { bold: false } })
  assert.deepEqual(formatChanges(changed, preset), [{ field: 'global.bold', before: true, after: false }])
  assert.equal(preset.global.bold, true)
})

test('restoring preset produces explicit differences from saved defaults', () => {
  const preset = { page: { print_mode: 'duplex' } }
  assert.deepEqual(formatDiff(preset, preset), {})
  assert.deepEqual(formatChanges(preset, { page: { print_mode: 'single' } }), [{ field: 'page.print_mode', before: 'single', after: 'duplex' }])
})
