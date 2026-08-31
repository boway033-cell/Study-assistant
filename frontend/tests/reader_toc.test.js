import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/views/ReaderView.vue'), 'utf8')

test('TOC editor supports discoverable drag-and-drop hierarchy correction', () => {
  for (const contract of [
    ':draggable="!tocReviewOnly"',
    '@dragstart.stop="startTocDrag(data, $event)"',
    '@drop.prevent.stop="dropTocDrag(data, $event)"',
    "position === 'inside'",
    'const group = tocDraft.value.splice',
  ]) assert.ok(reader.includes(contract), `missing TOC drag contract: ${contract}`)
})

test('TOC saves and repairs refresh in place without reloading the reader', () => {
  const saveBlock = reader.slice(reader.indexOf('const saveTocEditor'), reader.indexOf('const applySafeTocRepair'))
  const repairBlock = reader.slice(reader.indexOf('const applySafeTocRepair'), reader.indexOf('const restoreRevision'))
  assert.match(saveBlock, /applyTocResultInPlace\(result\)/)
  assert.match(repairBlock, /applyTocResultInPlace\(applied\)/)
  assert.doesNotMatch(saveBlock, /loadBook|loadTocEditor/)
  assert.doesNotMatch(repairBlock, /loadBook|loadTocEditor/)
})

test('manual level correction moves only the selected subtree', () => {
  const levelBlock = reader.slice(reader.indexOf('const normalizeTocLevels'), reader.indexOf('const subtreeEnd'))
  assert.match(levelBlock, /tocDraft\.value\[end\]\.level > baseLevel/)
  assert.match(levelBlock, /i === index/)
  assert.doesNotMatch(levelBlock, /normalizeAllTocLevels\(\)/)
})
