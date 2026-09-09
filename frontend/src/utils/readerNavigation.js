// A fresh wheel gesture at an edge turns the spread. Momentum only scrolls.
export function wheelNavigation(metrics, deltaY, now, state) {
  const direction = Math.sign(deltaY)
  const fresh = now - state.lastAt > 220 || direction !== state.direction
  state.lastAt = now
  state.direction = direction
  const max = Math.max(0, metrics.scrollHeight - metrics.clientHeight)
  const atEdge = direction > 0 ? metrics.scrollTop >= max - 2 : metrics.scrollTop <= 2
  if (!atEdge) { state.edge = 0; return 0 }
  if (!fresh || now - state.turnedAt < 600) return 0
  if (max > 2 && state.edge !== direction) { state.edge = direction; return 0 }
  state.edge = 0
  state.turnedAt = now
  return direction
}
