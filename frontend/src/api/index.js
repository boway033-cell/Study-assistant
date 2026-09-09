import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 60000 })
const repeatedParams = { indexes: null }

// 统一错误提示
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const detail = err.response?.data?.detail
    const msg = Array.isArray(detail)
      ? detail.map(item => item?.msg || String(item)).join('；')
      : typeof detail === 'object' && detail
        ? detail.msg || JSON.stringify(detail)
        : detail || err.message || '请求失败'
    return Promise.reject(new Error(msg))
  }
)

export default http

export const subscribeTask = (taskId, onUpdate, options = {}) => new Promise((resolve, reject) => {
  let source = null
  let pollTimer = null
  let settled = false
  let polling = false
  const startedAt = Date.now()
  const maxWaitMs = options.maxWaitMs ?? 12 * 60 * 60 * 1000
  const terminal = task => ['done', 'failed', 'cancelled'].includes(task?.status)
  const cleanup = () => {
    source?.close()
    source = null
    clearTimeout(pollTimer)
    options.signal?.removeEventListener('abort', abort)
  }
  const finish = (task) => {
    if (settled) return
    settled = true
    cleanup()
    resolve(task)
  }
  const fail = (error) => {
    if (settled) return
    settled = true
    cleanup()
    reject(error)
  }
  const publish = (task) => {
    onUpdate?.(task)
    if (terminal(task)) finish(task)
  }
  const poll = async () => {
    if (settled || options.signal?.aborted) return
    if (Date.now() - startedAt > maxWaitMs) return fail(new Error('任务运行时间过长，请在任务中心检查状态'))
    try {
      publish(await http.get(`/tasks/${encodeURIComponent(taskId)}`))
    } catch {
      // 本地服务重启或短暂繁忙时继续重连；任务状态已持久化，不立刻判失败。
    }
    if (!settled) pollTimer = setTimeout(poll, 1800)
  }
  const startPolling = () => {
    if (settled || polling) return
    polling = true
    source?.close()
    source = null
    poll()
  }
  const abort = () => fail(new DOMException('已停止跟踪任务', 'AbortError'))
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) return abort()

  source = new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events`)
  source.onmessage = event => {
    try { publish(JSON.parse(event.data)) } catch { startPolling() }
  }
  source.addEventListener('timeout', startPolling)
  source.onerror = startPolling
})

// ===== 书籍 =====
export const listBooks = (params) => http.get('/books', { params, paramsSerializer: repeatedParams })
export const reorderBooks = (bookIds, shelfId = null) => http.put('/books/order', { book_ids: bookIds, shelf_id: shelfId })
export const getKnowledgeBaseHealth = () => http.get('/books/health')
export const repairKnowledgeBaseHealth = (issueIds) => http.post('/books/health/repair', { issue_ids: issueIds })
export const getBook = (id) => http.get(`/books/${id}`)
export const uploadBook = (file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post('/books/upload', form, { timeout: 120000 })
}
export const uploadBookBatch = (files) => {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  return http.post('/books/upload-batch', form, { timeout: 600000 })
}
export const deleteBook = (id) => http.delete(`/books/${id}`)
export const reparseBook = (id) => http.post(`/books/${id}/reparse`)
export const renameBook = (id, title) => http.patch(`/books/${id}`, { title })
export const searchBooks = (params) => http.get('/search', { params })
export const getTask = (id) => http.get(`/tasks/${id}`)
export const listTasks = (params = {}) => http.get('/tasks', { params })
export const cancelTask = (id) => http.post(`/tasks/${id}/cancel`)
export const retryTask = (id) => http.post(`/tasks/${id}/retry`)

// ===== 原文定位 =====
export const getChunkOriginal = (bookId, chunkId) => http.get(`/books/${bookId}/chunk/${chunkId}`)
export const getPageText = (bookId, pageNo) => http.get(`/books/${bookId}/page/${pageNo}`)
export const bookFileUrl = (bookId) => `/api/books/${bookId}/file`
export const renderedBookFileUrl = (bookId) => `/api/books/${bookId}/rendered-file`
export const getBookDocument = (bookId) => http.get(`/books/${bookId}/document`)
export const renameChapter = (id, title) => http.patch(`/chapters/${id}`, { title })
export const getTocReview = (bookId) => http.get(`/books/${bookId}/toc-review`)
export const rebuildBookToc = (bookId) => http.post(`/books/${bookId}/toc-rebuild`)
export const autoRepairToc = (bookId, apply = false) => http.post(`/books/${bookId}/toc-auto-repair`, { apply })
export const replaceBookToc = (bookId, items, note) => http.put(`/books/${bookId}/toc`, { items, note })
export const listTocRevisions = (bookId) => http.get(`/books/${bookId}/toc-revisions`)
export const restoreTocRevision = (bookId, revisionId) => http.post(`/books/${bookId}/toc-revisions/${revisionId}/restore`)
export const getArchiveProfile = (bookId) => http.get(`/books/${bookId}/archive`)
export const updateArchiveProfile = (bookId, data) => http.patch(`/books/${bookId}/archive`, data)
export const getSourceMap = (bookId) => http.get(`/books/${bookId}/source-map`)

// ===== 问答 =====
export const chatStream = async (body, onEvent) => {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error('请求失败')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 事件以空行分隔
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      const lines = block.split('\n')
      let event = 'message'
      const dataLines = []
      for (const line of lines) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length) {
        try { onEvent(event, JSON.parse(dataLines.join('\n'))) } catch { /* ignore */ }
      }
    }
  }
}
export const chatHistory = (params) => http.get('/chat/history', { params })
export const getChat = (id) => http.get(`/chat/${id}`)
export const deleteChat = (id) => http.delete(`/chat/${id}`)

// ===== 题目 =====
export const listQuizzes = (params) => http.get('/quizzes', { params })
export const attemptQuiz = (id, answer) => http.post(`/quizzes/${id}/attempt`, { user_answer: answer })
export const selfGrade = (id, correct) => http.post(`/quizzes/${id}/self-grade`, { is_correct: correct })
export const wrongQuizzes = (params) => http.get('/quizzes/wrong', { params })
export const importQuizzes = (data) => http.post('/quizzes/batch-import', data)
export const generateQuizzes = (bookId, data) => http.post(`/books/${bookId}/generate-quizzes`, data || {})
export const clearBookQuizzes = (bookId) => http.delete(`/books/${bookId}/quizzes`)
export const deepAnalyze = (bookId) => http.post(`/books/${bookId}/deep-analyze`)
export const getBookDeep = (bookId) => http.get(`/books/${bookId}/deep`)
export const classifyBook = (bookId) => http.post(`/books/${bookId}/classify`)
export const classifyAllBooks = () => http.post('/books/classify-all')
export const setBookCategory = (bookId, category) => http.patch(`/books/${bookId}/category`, { category })

// ===== 标签 =====
export const listTags = () => http.get('/tags')
export const createTag = (data) => http.post('/tags', data)
export const getBookTags = (bookId) => http.get(`/books/${bookId}/tags`)
export const addBookTags = (bookId, data) => http.post(`/books/${bookId}/tags`, data)
export const removeBookTag = (bookId, tagId) => http.delete(`/books/${bookId}/tags/${tagId}`)

// ===== 虚拟书架 =====
export const listShelves = () => http.get('/shelves')
export const createShelf = (data) => http.post('/shelves', data)
export const updateShelf = (id, data) => http.patch(`/shelves/${id}`, data)
export const deleteShelf = (id) => http.delete(`/shelves/${id}`)
export const putShelfBooks = (id, bookIds, mode = 'add') => http.put(`/shelves/${id}/books`, { book_ids: bookIds, mode })
export const removeShelfBook = (id, bookId) => http.delete(`/shelves/${id}/books/${bookId}`)

// ===== 统计 =====
export const getKnowledgeBaseInsights = (days = 30) => http.get('/stats/knowledge-base', { params: { days } })

// ===== 知识树 =====
export const getKnowledgeTree = (bookIds = []) => http.get('/knowledge/tree', {
  params: bookIds.length ? { book_ids: bookIds } : {}, paramsSerializer: repeatedParams,
})
export const createKnowledgeNode = (data) => http.post('/knowledge/nodes', data)
export const updateKnowledgeNode = (id, data) => http.patch(`/knowledge/nodes/${id}`, data)
export const deleteKnowledgeNode = (id) => http.delete(`/knowledge/nodes/${id}`)
export const moveKnowledgeNode = (id, parentId) => http.post(`/knowledge/nodes/${id}/move`, { parent_id: parentId })
export const getKnowledgeSource = (id) => http.get(`/knowledge/nodes/${id}/source`)
export const getNodeAnnotations = (id) => http.get(`/knowledge/nodes/${id}/annotations`)
export const importKnowledgeChapters = (data) => http.post('/knowledge/import-chapters', data)
export const aiGenerateKnowledge = (data) => http.post('/knowledge/ai-generate', data)
export const expandKnowledgeNode = (nodeId) => http.post('/knowledge/nodes/expand', { node_id: nodeId })
export const applyKnowledgeSuggestions = (nodeId, suggestions) => http.post(`/knowledge/nodes/${nodeId}/apply-suggestions`, { suggestions })
export const batchDeleteKnowledge = (nodeIds) => http.post('/knowledge/batch-delete', { node_ids: nodeIds })
export const reviewKnowledgeNote = (nodeId) => http.post(`/knowledge/nodes/${nodeId}/review-note`)
export const listKnowledgeNotes = (bookIds, q = '') => http.get('/knowledge/notes', {
  params: { book_ids: bookIds, q: q || undefined }, paramsSerializer: repeatedParams,
})
export const listKnowledgeRecords = (params = {}) => http.get('/knowledge/records', { params, paramsSerializer: repeatedParams })
export const promoteAnnotation = (annotationId) => http.post(`/knowledge/annotations/${annotationId}/promote`)
export const createKnowledgeNote = (data) => http.post('/knowledge/notes', data)
export const getKnowledgeNote = (id) => http.get(`/knowledge/notes/${id}`)
export const updateKnowledgeNote = (id, data) => http.patch(`/knowledge/notes/${id}`, data)
export const deleteKnowledgeNote = (id) => http.delete(`/knowledge/notes/${id}`)
export const addKnowledgeNoteToTree = (id, parentId = null) => http.post(`/knowledge/notes/${id}/add-to-tree`, null, { params: { parent_id: parentId } })
export const createEvidenceCard = (data) => http.post('/knowledge/evidence-cards', data)
export const getEvidenceCard = (id) => http.get(`/knowledge/evidence-cards/${id}`)
export const updateEvidenceCard = (id, data) => http.patch(`/knowledge/evidence-cards/${id}`, data)
export const deleteEvidenceCard = (id) => http.delete(`/knowledge/evidence-cards/${id}`)

// ===== PDF 标注 =====
export const listAnnotations = (bookId, params) => http.get(`/books/${bookId}/annotations`, { params })
export const createAnnotation = (bookId, data) => http.post(`/books/${bookId}/annotations`, data)
export const updateAnnotation = (id, data) => http.patch(`/annotations/${id}`, data)
export const deleteAnnotation = (id) => http.delete(`/annotations/${id}`)
export const repairAnnotation = (id) => http.post(`/annotations/${id}/repair`)
export const auditAnnotations = (bookId) => http.get(`/books/${bookId}/annotations/audit`)
export const getPdfTextLayer = (bookId, page, generate = true) => http.get(`/books/${bookId}/pdf-text-layer/${page}`, { params: { generate } })

// ===== AI 增强（可选，无 Key 时后端返回友好错误）=====
export const aiExplain = (data) => http.post('/ai/explain', data)
export const aiSummarize = (data) => http.post('/ai/summarize', data)
export const aiVision = (data) => http.post('/ai/vision', data)
export const studyOverview = (data) => http.post('/study/overview', data)
export const studyReports = (page = 1, pageSize = 20) => http.get('/study/reports', { params: { page, page_size: pageSize } })
export const updateStudyReportClaims = (id, claims) => http.patch(`/study/reports/${id}/claims`, { claims })
export const depositStudyReport = (id, data = {}) => http.post(`/study/reports/${id}/deposit`, data)
export const getStudyReport = (id) => http.get(`/study/reports/${id}`)
export const deleteStudyReport = (id) => http.delete(`/study/reports/${id}`)
export const studyTrainStart = (data) => http.post('/study/train/start', data)
export const studyTrainAsk = (data) => http.post('/study/train/ask', data)
export const studyTrainEnd = (data) => http.post('/study/train/end', data)

// ===== 设置 =====
export const getSettings = () => http.get('/settings')
export const updateSettings = (data) => http.put('/settings', data)
export const probeSettings = () => http.get('/settings/probe')
export const listCompatibleProviders = () => http.get('/settings/providers')
export const saveCompatibleProvider = (data) => http.post('/settings/providers', data)
export const deleteCompatibleProvider = (id) => http.delete(`/settings/providers/${id}`)
export const probeCompatibleProvider = (id) => http.post(`/settings/providers/${id}/probe`)
export const updateProviderRouting = (data) => http.put('/settings/providers/routing', data)
export const getProviderUsage = () => http.get('/settings/providers/usage')
export const listProviderModels = (id) => http.get(`/settings/providers/${id}/models`)
export const discoverProviderModels = (data) => http.post('/settings/providers/models/discover', data)
export const getStorageUsage = () => http.get('/settings/storage')
export const getCapacityStatus = () => http.get('/settings/capacity')
export const cleanupStorage = (categories) => http.post('/settings/storage/cleanup', { categories })

// ===== 文献汇报与合法全文获取 =====
export const generatePresentation = (data) => http.post('/presentations/generate', data)
export const createPresentationOutline = (data) => http.post('/presentations/outline', data)
export const updatePresentationOutline = (id, slides) => http.patch(`/presentations/${id}/outline`, { slides })
export const renderPresentation = (id, data) => http.post(`/presentations/${id}/render`, data)
export const listPresentations = (bookId, params = {}) => http.get('/presentations', { params: { ...params, ...(bookId ? { book_id: bookId } : {}) } })
export const getPresentation = (id) => http.get(`/presentations/${id}`)
export const presentationDownloadUrl = (id) => `/api/presentations/${id}/download`
export const presentationPreviewUrl = (id, slideNo) => `/api/presentations/${id}/preview/${slideNo}`
// ===== 写作实验室 =====
export const listWritingProfiles = () => http.get('/writing/profiles')
export const getWritingProfile = (id) => http.get(`/writing/profiles/${id}`)
export const createWritingProfile = (data) => http.post('/writing/profiles', data)
export const refineWritingProfile = (id, data) => http.post(`/writing/profiles/${id}/refine`, data)
export const deleteWritingProfile = (id) => http.delete(`/writing/profiles/${id}`)
export const imitateWriting = (id, data) => http.post(`/writing/profiles/${id}/imitate`, data, { timeout: 360000 })
export const createLiteratureReview = (data) => http.post('/writing/literature-review', data, { timeout: 600000 })
export const cleanAiToneText = (data) => http.post('/writing/clean-text', data, { timeout: 360000 })
export const cleanAiToneDocx = (file, profileId) => { const form=new FormData(); form.append('file',file); if(profileId) form.append('profile_id',profileId); return http.post('/writing/clean-docx',form,{timeout:600000}) }
export const listWritingOutputs = (params = {}) => http.get('/writing/outputs', { params })
export const getWritingOutput = (id) => http.get(`/writing/outputs/${id}`)
export const updateWritingOutput = (id, data) => http.patch(`/writing/outputs/${id}`, data)
export const reviewWritingOutput = (id, acceptedIndexes) => http.post(`/writing/outputs/${id}/review`, { accepted_indexes: acceptedIndexes })
export const writingOutputDownloadUrl = (id) => `/api/writing/outputs/${id}/download`
export const getLiteratureConfig = () => http.get('/literature/config')
export const updateLiteratureConfig = (data) => http.put('/literature/config', data)
export const resolveLiterature = (data) => http.post('/literature/resolve', data, { timeout: 90000 })
export const importOpenAccess = (data) => http.post('/literature/import', data, { timeout: 180000 })
export const openLibraryHandoff = (data) => http.post('/literature/library-handoff', data)
export const openBrowserHandoff = (data) => http.post('/literature/browser-handoff', data)
export const listLiteratureAttempts = () => http.get('/literature/attempts')
export const listLiteratureResources = (bookId) => http.get(`/literature/books/${bookId}/resources`)
export const createLiteratureResource = (bookId, data) => http.post(`/literature/books/${bookId}/resources`, data)
export const updateLiteratureResource = (id, data) => http.patch(`/literature/resources/${id}`, data)
export const deleteLiteratureResource = (id) => http.delete(`/literature/resources/${id}`)

// ===== 知识图谱 =====
export const getGraph = (bookIds) => http.get('/graph', {
  params: { book_ids: bookIds }, paramsSerializer: repeatedParams,
})
export const getConceptSources = (name, bookIds) => http.get(`/graph/concept/${encodeURIComponent(name)}/sources`, {
  params: { book_ids: bookIds }, paramsSerializer: repeatedParams,
})


// ===== 数据健康 =====
export const getHealthData = () => http.get('/health/data')
export const getChatEval = () => http.get('/chat/eval')

// ===== AI 绘图 =====
export const drawGenerate = (data) => http.post('/draw/generate', data, { timeout: 120000 })
export const drawModify = (data) => http.post('/draw/modify', data, { timeout: 120000 })
export const drawListSessions = () => http.get('/draw/sessions')
export const drawGetSession = (sid) => http.get(`/draw/sessions/${sid}`)
export const drawDeleteSession = (sid) => http.delete(`/draw/sessions/${sid}`)
