import { marked } from 'marked'
import DOMPurify from 'dompurify'

// 配置 marked：gfm + 换行
marked.setOptions({ breaks: true, gfm: true })

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
