import { createRouter, createWebHistory } from 'vue-router'
// FastAPI 对非 API 路径回退 index.html，因此可使用真实路径并支持协议深链。
const routes = [
  { path: '/', redirect: '/library' },
  { path: '/library', name: 'library', component: () => import('../views/LibraryView.vue'), meta: { title: '文献知识库', context: '导入 · 归档 · 检索' } },
  { path: '/knowledge-health', name: 'knowledge-health', component: () => import('../views/KnowledgeHealthView.vue'), meta: { title: '知识库健康', context: '解析 · 质量 · 来源完整性' } },
  { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue'), meta: { title: '知识库问答', context: '基于个人资料回答' } },
  { path: '/knowledge-hub', name: 'knowledge-hub', component: () => import('../views/KnowledgeHubView.vue'), meta: { title: '知识沉淀', context: '笔记 · 结构 · 关系 · 研读' } },
  { path: '/notes', redirect: to => ({ path: '/knowledge-hub', query: { ...to.query, view: 'notes' } }) },
  { path: '/knowledge', redirect: to => ({ path: '/knowledge-hub', query: { ...to.query, view: 'tree' } }) },
  { path: '/quiz', name: 'quiz', component: () => import('../views/QuizView.vue'), meta: { title: '刷题自测', context: '更多学习工具' } },
  { path: '/stats', name: 'stats', component: () => import('../views/StatsView.vue'), meta: { title: '知识库洞察', context: '质量 · 沉淀 · 来源 · 输出' } },
  { path: '/study', redirect: to => ({ path: '/knowledge-hub', query: { ...to.query, view: 'study' } }) },
  { path: '/graph', redirect: to => ({ path: '/knowledge-hub', query: { ...to.query, view: 'graph' } }) },
  { path: '/draw', name: 'draw', component: () => import('../views/DrawView.vue'), meta: { title: 'AI 绘图', context: '更多学习工具' } },
  { path: '/literature-workbench', name: 'literature-workbench', component: () => import('../views/LiteratureWorkbenchView.vue'), meta: { title: '文献工作台', context: '获取 · 选证 · 输出' } },
  { path: '/writing', name: 'writing', component: () => import('../views/WritingView.vue'), meta: { title: '写作工作台', context: '取材 · 写作 · 审阅 · Word' } },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '设置', context: '模型与数据边界' } },
  { path: '/reader/:bookId', name: 'reader', component: () => import('../views/ReaderView.vue'), meta: { title: '文献阅读', context: '原文 · 精读 · 证据' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach(() => window.scrollTo({ top: 0, behavior: 'auto' }))

export default router
