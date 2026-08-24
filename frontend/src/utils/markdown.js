import { marked } from 'marked'
import DOMPurify from 'dompurify'

// 原文已在后端恢复为真实段落；不要把 PDF/OCR 的每个物理换行强制渲染成 <br>。
marked.setOptions({ breaks: false, gfm: true })

// 消毒任意 HTML（用于已转义/拼接的片段、流式增量等）
export function sanitizeHtml(html) {
  if (!html) return ''
  try {
    return DOMPurify.sanitize(html)
  } catch {
    // 兜底：纯转义
    return html.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  }
}

// 仅允许 SVG 图形配置，额外禁用可嵌入 HTML/脚本的元素与内联样式。
export function sanitizeSvg(svg) {
  if (!svg) return ''
  try {
    return DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      FORBID_TAGS: ['script', 'foreignObject'],
      FORBID_ATTR: ['style'],
    })
  } catch {
    return ''
  }
}

// 渲染 Markdown 并消毒（XSS 防护）
export function renderMarkdown(text) {
  if (!text) return ''
  let html
  try {
    html = marked.parse(text)
  } catch {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  }
  return sanitizeHtml(html)
}
