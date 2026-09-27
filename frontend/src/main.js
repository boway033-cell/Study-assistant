import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './theme/bailu.css'  // 白露节气 · 蓝白主题（覆盖 Element 变量）
import './theme/magic-card.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'

const app = createApp(App)
document.addEventListener('pointermove', event => {
  const card = event.target.closest?.('.el-card, .agent-card, .chat-card, .kt-card, .mobile-paper-card, .preview-card, .selection-card, .settings-card, .setup-card, .side-card, .source-card, .task-card')
  if (!card) return
  const rect = card.getBoundingClientRect()
  card.style.setProperty('--mouse-x', `${event.clientX - rect.left}px`)
  card.style.setProperty('--mouse-y', `${event.clientY - rect.top}px`)
}, { passive: true })
app.use(ElementPlus, { locale: zhCn })
app.use(router)
// 全局错误兜底：偶发的渲染竞态（页面切换中）不中断应用
app.config.errorHandler = (err) => {
  if (err && typeof err === 'object' && /reading 'id'|reading 'chapter_id'/.test(err.message || '')) {
    // 页面切换中的瞬态竞态，静默忽略
    return
  }
  console.error('[app error]', err)
}
app.mount('#app')
