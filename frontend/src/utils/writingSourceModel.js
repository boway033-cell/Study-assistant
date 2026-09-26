// 独立仿写的取材来源模型：纯函数，便于用 node:test 做行为测试。
// 五类来源各自独立选择、计数与增删；Writing DNA 切换不得影响这里的选择结果。

export const SOURCE_TYPES = ['note', 'evidence', 'report', 'local_literature', 'web']

export const SOURCE_LABELS = {
  note: '知识笔记',
  evidence: '证据卡',
  report: '批判性审查报告',
  local_literature: '本地文献',
  web: '联网补充',
}

export const EVIDENCE_LEVEL_LABELS = {
  abstract: '摘要级依据',
  metadata: '元数据线索',
}

export const MIN_WEB_ABSTRACT_CHARS = 40

export const emptySelection = () =>
  SOURCE_TYPES.reduce((acc, type) => ({ ...acc, [type]: [] }), {})

// 联网来源没有库内主键，用 provider:记录号 作为稳定标识。
export const sourceKey = (type, item) => {
  if (!item) return ''
  if (type === 'web') return `${item.provider}:${item.provider_id}`
  const id = item.id ?? item.book_id
  return `${type}:${id}`
}

export const isSelected = (selection, type, item) =>
  (selection[type] || []).some(entry => sourceKey(type, entry) === sourceKey(type, item))

export const toggleSource = (selection, type, item) => {
  const list = selection[type] || []
  const key = sourceKey(type, item)
  const kept = list.filter(entry => sourceKey(type, entry) !== key)
  return { ...selection, [type]: kept.length === list.length ? [...list, item] : kept }
}

export const addSource = (selection, type, item) => {
  if (isSelected(selection, type, item)) return selection
  return { ...selection, [type]: [...(selection[type] || []), item] }
}

export const removeSource = (selection, type, key) => ({
  ...selection,
  [type]: (selection[type] || []).filter(entry => sourceKey(type, entry) !== key),
})

export const removeAt = (selection, type, index) => ({
  ...selection,
  [type]: (selection[type] || []).filter((_, position) => position !== index),
})

// 已选来源按类型分组展示；空分组直接省略，避免无意义嵌套。
export const selectedGroups = selection =>
  SOURCE_TYPES.filter(type => (selection[type] || []).length)
    .map(type => ({ type, label: SOURCE_LABELS[type], items: selection[type] }))

export const selectionCounts = selection =>
  SOURCE_TYPES.reduce((acc, type) => ({ ...acc, [type]: (selection[type] || []).length }), {})

// 只有带摘要的联网命中才能作为事实依据；仅题名/元数据只能作为检索线索。
export const webSourceSelectable = item =>
  item?.evidence_level === 'abstract' && String(item.abstract || '').trim().length >= MIN_WEB_ABSTRACT_CHARS

export const usableSourceCount = selection => {
  const data = SOURCE_TYPES.filter(type => type !== 'web')
    .reduce((sum, type) => sum + (selection[type] || []).length, 0)
  return data + (selection.web || []).filter(webSourceSelectable).length
}

export const canGenerateImitate = (selection, topic) =>
  String(topic || '').trim().length >= 2 && usableSourceCount(selection) > 0

// 已删除来源必须被明确移除，而不是等到生成时才报错。
export const dropMissing = (selection, type, missingIds) => {
  const drop = new Set((missingIds || []).map(Number))
  const list = selection[type] || []
  if (!drop.size || !list.length) return { selection, removed: 0 }
  const kept = list.filter(item => !drop.has(Number(item.id ?? item.book_id)))
  return { selection: { ...selection, [type]: kept }, removed: list.length - kept.length }
}

export const webSnapshot = item => ({
  provider: item.provider,
  provider_id: item.provider_id,
  title: item.title,
  authors: item.authors || '',
  year: typeof item.year === 'number' ? item.year : null,
  doi: item.doi || null,
  container_title: item.container_title || '',
  url: item.url,
  abstract: item.abstract || '',
  evidence_level: item.evidence_level || 'metadata',
  retrieved_at: item.retrieved_at || '',
})

export const ids = list => (list || []).map(item => Number(item.id ?? item.book_id)).filter(Number.isInteger)

export const buildImitatePayload = (form, selection) => ({
  topic: form.topic,
  genre: form.genre,
  length: form.length,
  brief: form.brief || '',
  knowledge_note_ids: ids(selection.note),
  evidence_card_ids: ids(selection.evidence),
  report_ids: ids(selection.report),
  local_book_ids: ids(selection.local_literature),
  external_sources: (selection.web || []).filter(webSourceSelectable).map(webSnapshot),
})

// 列表项至少显示标题 + 来源文献/作者或报告范围 + 来源类型。
export const sourceSubtitle = (type, item) => {
  if (type === 'note') {
    return [item.book_title, item.chapter_title, item.page ? `第 ${item.page} 页` : '']
      .filter(Boolean).join(' · ')
  }
  if (type === 'evidence') {
    return [item.book_title, item.page ? `第 ${item.page} 页` : '', `核验：${verificationLabel(item.verification_status)}`]
      .filter(Boolean).join(' · ')
  }
  if (type === 'report') {
    return `报告范围 ${(item.book_ids || []).length} 篇文献`
  }
  if (type === 'local_literature') {
    return [item.authors, item.year, item.journal, item.chunk_count ? `${item.chunk_count} 个分块` : '']
      .filter(Boolean).join(' · ') || '已解析本地文献'
  }
  return [item.authors, item.year, item.container_title, item.doi ? `DOI ${item.doi}` : '']
    .filter(Boolean).join(' · ')
}

export const verificationLabel = status => ({
  verified: '已核验',
  needs_review: '待核验',
  rejected: '已否决',
}[status] || status || '未标注')
