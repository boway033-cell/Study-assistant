import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 60000 })

// 统一错误提示
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    return Promise.reject(new Error(msg))
  }
)

export default http

// ===== 书籍 =====
export const listBooks = (params) => http.get('/books', { params })
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
export const renameBook = (id, title) => http.patch(`/books/${id}`, { title })
export const searchBooks = (params) => http.get('/search', { params })
export const getTask = (id) => http.get(`/tasks/${id}`)

// ===== 原文定位 =====
export const getChunkOriginal = (bookId, chunkId) => http.get(`/books/${bookId}/chunk/${chunkId}`)
export const getPageText = (bookId, pageNo) => http.get(`/books/${bookId}/page/${pageNo}`)
export const bookFileUrl = (bookId) => `/api/books/${bookId}/file`
export const getBookDocument = (bookId) => http.get(`/books/${bookId}/document`)
export const renameChapter = (id, title) => http.patch(`/chapters/${id}`, { title })

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

// ===== 统计 =====
export const getOverview = () => http.get('/stats/overview')
export const getMastery = (bookId) => http.get('/stats/mastery', { params: { book_id: bookId } })
export const getActivity = (days) => http.get('/stats/activity', { params: { days } })
export const getWeakness = () => http.get('/stats/weakness')

// ===== 知识树 =====
export const getKnowledgeTree = () => http.get('/knowledge/tree')
export const createKnowledgeNode = (data) => http.post('/knowledge/nodes', data)
export const updateKnowledgeNode = (id, data) => http.patch(`/knowledge/nodes/${id}`, data)
export const deleteKnowledgeNode = (id) => http.delete(`/knowledge/nodes/${id}`)
export const moveKnowledgeNode = (id, parentId) => http.post(`/knowledge/nodes/${id}/move`, { parent_id: parentId })
export const getKnowledgeSource = (id) => http.get(`/knowledge/nodes/${id}/source`)
export const getNodeAnnotations = (id) => http.get(`/knowledge/nodes/${id}/annotations`)
export const importKnowledgeChapters = (data) => http.post('/knowledge/import-chapters', data)
export const aiGenerateKnowledge = (data) => http.post('/knowledge/ai-generate', data)
export const expandKnowledgeNode = (nodeId) => http.post('/knowledge/nodes/expand', { node_id: nodeId })
export const batchDeleteKnowledge = (nodeIds) => http.post('/knowledge/batch-delete', { node_ids: nodeIds })
export const reviewKnowledgeNote = (nodeId) => http.post(`/knowledge/nodes/${nodeId}/review-note`)

// ===== PDF 标注 =====
export const listAnnotations = (bookId, params) => http.get(`/books/${bookId}/annotations`, { params })
export const createAnnotation = (bookId, data) => http.post(`/books/${bookId}/annotations`, data)
export const updateAnnotation = (id, data) => http.patch(`/annotations/${id}`, data)
export const deleteAnnotation = (id) => http.delete(`/annotations/${id}`)

// ===== AI 增强（可选，无 Key 时后端返回友好错误）=====
export const aiExplain = (data) => http.post('/ai/explain', data)
export const aiSummarize = (data) => http.post('/ai/summarize', data)
export const aiVision = (data) => http.post('/ai/vision', data)
export const studyOverview = (data) => http.post('/study/overview', data)
export const studyReports = () => http.get('/study/reports')
export const deleteStudyReport = (id) => http.delete(`/study/reports/${id}`)
export const studyTrainStart = (data) => http.post('/study/train/start', data)
export const studyTrainAsk = (data) => http.post('/study/train/ask', data)
export const studyTrainEnd = (data) => http.post('/study/train/end', data)

// ===== 设置 =====
export const getSettings = () => http.get('/settings')
export const updateSettings = (data) => http.put('/settings', data)
export const probeSettings = () => http.get('/settings/probe')

// ===== 知识图谱 =====
export const getGraph = () => http.get('/graph')
export const getConceptSources = (name) => http.get(`/graph/concept/${encodeURIComponent(name)}/sources`)

// ===== 学习计划 =====
export const getPlan = () => http.get('/plan')
export const savePlan = (data) => http.post('/plan', data)
export const planCheckin = (data) => http.post('/plan/checkin', data)

// ===== 数据健康 =====
export const getHealthData = () => http.get('/health/data')
export const getChatEval = () => http.get('/chat/eval')

// ===== AI 绘图 =====
export const drawGenerate = (data) => http.post('/draw/generate', data, { timeout: 120000 })
export const drawModify = (data) => http.post('/draw/modify', data, { timeout: 120000 })
export const drawListSessions = () => http.get('/draw/sessions')
export const drawGetSession = (sid) => http.get(`/draw/sessions/${sid}`)
export const drawDeleteSession = (sid) => http.delete(`/draw/sessions/${sid}`)