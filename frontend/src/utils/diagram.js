const SAFE_COLOR = /^(?:#[0-9a-f]{3,8}|none|transparent)$/i

export function safeSvgColor(style, key, fallback) {
  const match = style.match(new RegExp(`${key}=([^;]+)`))
  const value = match?.[1]?.trim() || ''
  return SAFE_COLOR.test(value) ? value : fallback
}

export function escapeXml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}
