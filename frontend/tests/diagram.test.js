import test from 'node:test'
import assert from 'node:assert/strict'

import { escapeXml, safeSvgColor } from '../src/utils/diagram.js'

test('safeSvgColor accepts draw.io hex colors', () => {
  assert.equal(safeSvgColor('fillColor=#dae8fc;', 'fillColor', '#fff'), '#dae8fc')
})

test('safeSvgColor rejects SVG attribute injection', () => {
  const injected = '#fff" onload="globalThis.pwned=1'
  assert.equal(safeSvgColor(`fillColor=${injected};`, 'fillColor', '#dae8fc'), '#dae8fc')
})

test('escapeXml escapes text and attribute delimiters', () => {
  assert.equal(escapeXml('<x a="1">&\''), '&lt;x a=&quot;1&quot;&gt;&amp;&apos;')
})
