import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const reader = readFileSync(resolve(here, '../src/components/PdfReader.vue'), 'utf8')
const vite = readFileSync(resolve(here, '../vite.config.js'), 'utf8')
const notes = readFileSync(resolve(here, '../src/views/NotesView.vue'), 'utf8')

test('pdf reader uses root-scoped CMap, font and WASM assets', () => {
  assert.match(reader, /cMapUrl:\s*'\/cmaps\/'/)
  assert.match(reader, /standardFontDataUrl:\s*'\/standard_fonts\/'/)
  assert.match(reader, /wasmUrl:\s*'\/wasm\/'/)
  assert.match(vite, /pdfjs-dist\/wasm/)
  assert.match(vite, /configureServer/)
})

test('knowledge notes provide a dedicated sanitized Markdown reader', () => {
  assert.match(notes, /getKnowledgeNote/)
  assert.match(notes, /openNote/)
  assert.match(notes, /renderMarkdown\(reader\.note\.content\)/)
  assert.match(notes, /打开阅读/)
})
