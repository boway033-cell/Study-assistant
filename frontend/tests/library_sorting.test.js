import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const library = readFileSync(resolve(here, '../src/views/LibraryView.vue'), 'utf8')
const api = readFileSync(resolve(here, '../src/api/index.js'), 'utf8')

test('library sorting is server-backed and custom order is persisted by drag', () => {
  assert.match(library, /v-model="sortBy"/)
  assert.match(library, /sort_by:\s*sortBy\.value/)
  assert.match(library, /:draggable="canDragSort"/)
  assert.match(library, /await reorderBooks\(orderedIds, shelfId\)/)
  assert.match(api, /http\.put\('\/books\/order'/)
})

test('drag sorting is limited to an unfiltered library or explicit shelf', () => {
  assert.match(library, /activeFilterCount\.value === 0/)
  assert.match(library, /selectedShelf\.value === 'all' \|\| typeof selectedShelf\.value === 'number'/)
  assert.match(library, /清除筛选后即可拖拽排序/)
})

test('smart shelf counts come from the global backend summary', () => {
  assert.match(library, /libraryStats\.total/)
  assert.match(library, /if \(resp\.stats\) libraryStats\.value = resp\.stats/)
  assert.match(library, /books\.length < filteredTotal/)
  assert.doesNotMatch(library, /const readingCount = computed/)
})
