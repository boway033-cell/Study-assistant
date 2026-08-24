import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const theme = readFileSync(resolve(here, '../src/theme/bailu.css'), 'utf8')

test('cards share gray-200 border and shadow-sm tokens', () => {
  assert.match(theme, /--study-card-border:\s*#e5e7eb/i)
  assert.match(theme, /--study-shadow-sm:/)
  assert.match(theme, /\.el-card,[\s\S]*\.task-item/)
  assert.match(theme, /border:\s*1px solid var\(--study-card-border\)/)
  assert.match(theme, /box-shadow:\s*var\(--study-shadow-sm\)/)
})

test('buttons provide hover, active and reduced-motion feedback', () => {
  assert.match(theme, /\.el-button:not\(\.is-disabled\):hover/)
  assert.match(theme, /translateY\(-1px\)/)
  assert.match(theme, /scale\(0\.97\)/)
  assert.match(theme, /prefers-reduced-motion:\s*reduce/)
})
