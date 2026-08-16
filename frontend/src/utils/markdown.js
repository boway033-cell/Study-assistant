import { marked } from 'marked'

// 配置 marked：gfm + 换行
marked.setOptions({ breaks: true, gfm: true })

export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked.parse(text)
  } catch {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  }
}