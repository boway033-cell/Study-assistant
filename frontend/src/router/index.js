import { createRouter, createWebHashHistory } from 'vue-router'
import LibraryView from '../views/LibraryView.vue'
import ChatView from '../views/ChatView.vue'
import KnowledgeView from '../views/KnowledgeView.vue'
import QuizView from '../views/QuizView.vue'
import StatsView from '../views/StatsView.vue'
import SettingsView from '../views/SettingsView.vue'
import ReaderView from '../views/ReaderView.vue'
import StudyView from '../views/StudyView.vue'

// 使用 hash 模式：构建产物由 FastAPI 静态托管时无需服务端路由配置
// 静态 import 避免动态 import 在 Windows + rollup 下的解析问题
const routes = [
  { path: '/', redirect: '/library' },
  { path: '/library', name: 'library', component: LibraryView, meta: { title: '资料库' } },
  { path: '/chat', name: 'chat', component: ChatView, meta: { title: 'AI 问答' } },
  { path: '/knowledge', name: 'knowledge', component: KnowledgeView, meta: { title: '知识树' } },
  { path: '/quiz', name: 'quiz', component: QuizView, meta: { title: '刷题自测' } },
  { path: '/stats', name: 'stats', component: StatsView, meta: { title: '学习统计' } },
  { path: '/study', name: 'study', component: StudyView, meta: { title: 'AI 研读' } },
  { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '设置' } },
  { path: '/reader/:bookId', name: 'reader', component: ReaderView, meta: { title: 'PDF 阅读器' } },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
