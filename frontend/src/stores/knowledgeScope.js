import { computed, ref } from 'vue'
import { listBooks } from '../api'

const knowledgeBooks = ref([])
const knowledgeScopeLoading = ref(false)
const stored = localStorage.getItem('studyKnowledgeScope')
let initial = []
try { initial = JSON.parse(stored || '[]') } catch { initial = [] }
const knowledgeBookIds = ref(Array.isArray(initial) ? initial.filter(Number.isInteger) : [])

const knowledgeScopeLabel = computed(() => {
  if (!knowledgeBookIds.value.length) return '尚未选择研究资料'
  const names = knowledgeBookIds.value.map(id => knowledgeBooks.value.find(book => book.id === id)?.title).filter(Boolean)
  if (names.length <= 2) return names.join('、')
  return `${names.slice(0, 2).join('、')} 等 ${names.length} 本`
})

const setKnowledgeScope = (ids) => {
  const normalized = [...new Set((ids || []).map(Number).filter(Number.isInteger))]
  const unchanged = normalized.length === knowledgeBookIds.value.length && normalized.every((id, index) => id === knowledgeBookIds.value[index])
  if (!unchanged) knowledgeBookIds.value = normalized
  localStorage.setItem('studyKnowledgeScope', JSON.stringify(knowledgeBookIds.value))
}

const loadKnowledgeBooks = async () => {
  if (knowledgeBooks.value.length || knowledgeScopeLoading.value) return
  knowledgeScopeLoading.value = true
  try {
    const response = await listBooks({ page_size: 100 })
    knowledgeBooks.value = response.items.filter(book => book.status === 'ready')
    const valid = new Set(knowledgeBooks.value.map(book => book.id))
    setKnowledgeScope(knowledgeBookIds.value.filter(id => valid.has(id)))
  } finally { knowledgeScopeLoading.value = false }
}

export { knowledgeBooks, knowledgeBookIds, knowledgeScopeLabel, knowledgeScopeLoading, setKnowledgeScope, loadKnowledgeBooks }
