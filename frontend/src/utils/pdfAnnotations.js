export function annotationSegments(annotation) {
  if (annotation?.anchor_json) {
    try {
      const anchor = JSON.parse(annotation.anchor_json)
      if (Array.isArray(anchor?.segments)) return anchor.segments
    } catch {}
  }
  try {
    return [{ page: annotation.page, source: 'pdf-text', rects: JSON.parse(annotation.rect_json || '[]') }]
  } catch { return [] }
}

export function clipSelectionRects(rangeRects, pages) {
  const segments = []
  for (const page of pages) {
    const bounds = page.bounds
    const rects = []
    for (const rect of rangeRects) {
      if (rect.width <= 0.5 || rect.height <= 0.5) continue
      const left = Math.max(rect.left, bounds.left)
      const top = Math.max(rect.top, bounds.top)
      const right = Math.min(rect.right, bounds.right)
      const bottom = Math.min(rect.bottom, bounds.bottom)
      if (right - left <= 0.5 || bottom - top <= 0.5) continue
      const item = {
        x: +((left - bounds.left) / bounds.width).toFixed(6),
        y: +((top - bounds.top) / bounds.height).toFixed(6),
        w: +((right - left) / bounds.width).toFixed(6),
        h: +((bottom - top) / bounds.height).toFixed(6),
      }
      if (item.x >= 0 && item.y >= 0 && item.x + item.w <= 1.0001 && item.y + item.h <= 1.0001) rects.push(item)
    }
    if (rects.length) segments.push({ page: Number(page.page), source: page.source || 'pdf-text', rects })
  }
  return segments.sort((a, b) => a.page - b.page)
}
