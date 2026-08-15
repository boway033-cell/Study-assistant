import { createRouter, createWebHashHistory } from 'vue-router'
import LibraryView from '../views/LibraryView.vue'
import ChatView from '../views/ChatView.vue'
import ReviewView from '../views/ReviewView.vue'
import QuizView from '../views/QuizView.vue'
import StatsView from '../views/StatsView.vue'
import SettingsView from '../views/SettingsView.vue'

// 使用 hash 模式：构建产物由 FastAPI 静态托管时无需服务端路由配置
// 静态 import 避免动态 import 在 Windows + rollup 下的解析问题
const routes = [
  { path: '/', redirect: '/library' },
  { path: '/library', name: 'library', component: LibraryView, meta: { title: '资料库' } },
  { path: '/chat', name: 'chat', component: ChatView, meta: { title: 'AI 问答' } },
  { path: '/review', name: 'review', component: ReviewView, meta: { title: '卡片复习' } },
  { path: '/quiz', name: 'quiz', component: QuizView, meta: { title: '刷题自测' } },
  { path: '/stats', name: 'stats', component: StatsView, meta: { title: '学习统计' } },
  { path: '/settings', name: 'settings', component: SettingsView, meta: { title: '设置' } },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
