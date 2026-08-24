import { createRouter, createWebHashHistory } from 'vue-router'
// 使用 hash 模式：构建产物由 FastAPI 静态托管时无需服务端路由配置
const routes = [
  { path: '/', redirect: '/library' },
  { path: '/library', name: 'library', component: () => import('../views/LibraryView.vue'), meta: { title: '文献知识库', context: '导入 · 归档 · 检索' } },
  { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue'), meta: { title: '知识库问答', context: '基于个人资料回答' } },
  { path: '/knowledge', name: 'knowledge', component: () => import('../views/KnowledgeView.vue'), meta: { title: '知识树', context: '组织与沉淀' } },
  { path: '/quiz', name: 'quiz', component: () => import('../views/QuizView.vue'), meta: { title: '刷题自测', context: '更多学习工具' } },
  { path: '/stats', name: 'stats', component: () => import('../views/StatsView.vue'), meta: { title: '学习统计', context: '更多学习工具' } },
  { path: '/study', name: 'study', component: () => import('../views/StudyView.vue'), meta: { title: '综合研读', context: '跨文献综合' } },
  { path: '/graph', name: 'graph', component: () => import('../views/GraphView.vue'), meta: { title: '知识图谱', context: '概念与来源' } },
  { path: '/plan', name: 'plan', component: () => import('../views/PlanView.vue'), meta: { title: '学习计划', context: '更多学习工具' } },
  { path: '/draw', name: 'draw', component: () => import('../views/DrawView.vue'), meta: { title: 'AI 绘图', context: '更多学习工具' } },
  { path: '/literature-workbench', name: 'literature-workbench', component: () => import('../views/LiteratureWorkbenchView.vue'), meta: { title: '文献工作台', context: '获取 · 选证 · 输出' } },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '设置', context: '模型与数据边界' } },
  { path: '/reader/:bookId', name: 'reader', component: () => import('../views/ReaderView.vue'), meta: { title: '文献阅读', context: '原文 · 精读 · 证据' } },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.afterEach(() => window.scrollTo({ top: 0, behavior: 'auto' }))

export default router
