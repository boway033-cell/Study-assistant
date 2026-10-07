// 统一处理 SSE 与历史详情；候选选择只补充对象，保留用户的原问题。
export function clarCandidates(payload = {}) {
  const qa = payload.qa || payload
  return Array.isArray(qa.clarification_options)
    ? qa.clarification_options.filter(item => typeof item === 'string' && item.trim()).slice(0, 4)
    : []
}

export function clarifiedQuestion(message, option) {
  const original = String(message.originalQuestion || message.qa?.question || '').trim()
  return original ? `关于${option}：${original}` : String(option).trim()
}

export function citationAuditLabel(audit) {
  if (audit.semantic_status === 'not_applicable') return '本轮为澄清，未调用模型'
  if (!audit.verified) return '引用缺失或编号无效；请逐条核对原文'
  if (audit.support_method === 'lexical_overlap_proxy' && audit.support_rate != null) {
    return `引用编号有效；词面筛查 ${Math.round(audit.support_rate * 100)}%，支撑判断仍需人工复核`
  }
  return '引用编号已核对；主张是否得到原文支持仍需核对'
}
