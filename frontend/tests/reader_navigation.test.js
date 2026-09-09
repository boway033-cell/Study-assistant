import test from 'node:test'
import assert from 'node:assert/strict'
import { wheelNavigation } from '../src/utils/readerNavigation.js'

const state = () => ({ lastAt: -Infinity, direction: 0, edge: 0, turnedAt: -Infinity })
test('tall spreads scroll first; momentum cannot turn the page at the bottom', () => {
  const s = state()
  assert.equal(wheelNavigation({ scrollTop: 100, scrollHeight: 1600, clientHeight: 600 }, 90, 1000, s), 0)
  assert.equal(wheelNavigation({ scrollTop: 1000, scrollHeight: 1600, clientHeight: 600 }, 90, 1030, s), 0)
  assert.equal(wheelNavigation({ scrollTop: 1000, scrollHeight: 1600, clientHeight: 600 }, 90, 1500, s), 0)
  assert.equal(wheelNavigation({ scrollTop: 1000, scrollHeight: 1600, clientHeight: 600 }, 90, 1900, s), 1)
  assert.equal(wheelNavigation({ scrollTop: 0, scrollHeight: 1600, clientHeight: 600 }, 90, 1930, s), 0)
})
test('a fully visible spread can turn, but repeated wheel pulses are debounced', () => {
  const s = state(), m = { scrollTop: 0, scrollHeight: 600, clientHeight: 600 }
  assert.equal(wheelNavigation(m, 70, 1000, s), 1)
  assert.equal(wheelNavigation(m, 70, 1040, s), 0)
  assert.equal(wheelNavigation(m, -70, 1800, s), -1)
})
