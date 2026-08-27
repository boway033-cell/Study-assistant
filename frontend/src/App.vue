<template>
  <el-container class="layout" :class="{ 'is-reader-layout': $route.name === 'reader' }">
    <el-aside :width="$route.name === 'reader' || sidebarCollapsed ? '76px' : '224px'" class="aside desktop-aside">
      <div class="logo">
        <span class="logo-dew">💧</span>
        <div v-if="$route.name !== 'reader' && !sidebarCollapsed" class="logo-text">
          <span class="logo-title">Study assistant</span>
          <span class="logo-sub">{{ term.name }} · {{ dateStr }}</span>
        </div>
      </div>
      <AppNavigation :collapsed="$route.name === 'reader' || sidebarCollapsed" />
      <div class="aside-footer">
        <div class="dew-dot" v-for="i in 3" :key="i" :style="{ left: 24 + i * 44 + 'px', animationDelay: i * 0.6 + 's' }"></div>
        <span class="aside-poem">{{ term.name }} · {{ dateStr }}</span>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header" :class="{ 'reader-app-header': $route.name === 'reader' }">
        <div class="header-leading">
          <el-button text circle class="mobile-menu" aria-label="打开导航" @click="mobileNav = true"><el-icon><Menu /></el-icon></el-button>
          <el-button text circle class="collapse-button" aria-label="折叠侧栏" @click="toggleSidebar"><el-icon><component :is="sidebarCollapsed ? Expand : Fold" /></el-icon></el-button>
          <div><span class="page-title">{{ $route.meta.title || '' }}</span><span class="page-context">{{ $route.meta.context || '个人知识库' }}</span></div>
        </div>
        <div class="header-actions">
          <span class="header-slogan">{{ term.name }}三候 · {{ term.hou.join(' · ') }}</span>
          <el-badge :value="activeTaskCount" :hidden="!activeTaskCount" class="task-badge">
            <el-button plain class="task-button" @click="taskDrawerOpen = true"><el-icon><Bell /></el-icon><span>任务</span></el-button>
          </el-badge>
        </div>
      </el-header>
      <el-main class="main" :class="{ 'reader-main': $route.name === 'reader' }">
        <router-view />
      </el-main>
    </el-container>

    <el-drawer v-model="mobileNav" direction="ltr" size="280px" :with-header="false" class="mobile-drawer">
      <div class="mobile-logo"><span>💧</span><div><b>Study assistant</b><small>个人文献知识库</small></div></div>
      <AppNavigation @navigate="mobileNav = false" />
    </el-drawer>
    <GlobalTaskCenter v-model="taskDrawerOpen" />

    <!-- 首次使用引导：未配置 API Key 时提示 -->
    <el-dialog v-model="showKeyGuide" title="可选：启用云端 AI 能力" width="520px" append-to-body @closed="rememberKeyGuide">
      <div class="guide-body">
        <p>本应用的 <b>AI 问答与分析</b> 基于 <b>DeepSeek 云端</b> 大模型；<b>文本解析 / 切块 / 检索等分析全部在本地完成</b>，仅将「提问 + 检索片段」发送到云端。</p>
        <p>使用前需要配置一个 <b>DeepSeek API Key</b>：</p>
        <ol class="guide-steps">
          <li>打开 <a href="https://platform.deepseek.com" target="_blank">platform.deepseek.com</a> 注册并创建 API Key；</li>
          <li>点击下方「去设置」，把 Key 粘贴到 <b>设置 → DeepSeek API Key</b>；</li>
          <li>保存后在 <b>设置</b> 页点击「重新检测」确认连接成功。</li>
        </ol>
        <p class="guide-tip">💡 模型档位：<b>flash</b>（快速，日常问答）/ <b>pro</b>（深度推理，难题分析），可在设置页或问答页随时切换。</p>
      </div>
      <template #footer>
        <el-button @click="showKeyGuide = false">稍后再说</el-button>
        <el-button type="primary" @click="goSettings">去设置</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Menu, Fold, Expand, Bell } from '@element-plus/icons-vue'
import { getSettings } from './api'
import { getSolarTerm } from './utils/solarTerm'
import AppNavigation from './components/AppNavigation.vue'
import GlobalTaskCenter from './components/GlobalTaskCenter.vue'
import { taskCenter, startTaskPolling, stopTaskPolling } from './stores/taskCenter'

const router = useRouter()
const showKeyGuide = ref(false)
const sidebarCollapsed = ref(localStorage.getItem('sidebarCollapsed') === 'true')
const mobileNav = ref(false)
const taskDrawerOpen = ref(false)
const activeTaskCount = computed(() => taskCenter.items.filter((task) => ['pending', 'running', 'cancelling'].includes(task.status)).length)
const term = getSolarTerm()
const dateStr = (() => {
  const d = new Date()
  return d.getFullYear() + '年' + (d.getMonth() + 1) + '月' + d.getDate() + '日'
})()

const goSettings = () => {
  rememberKeyGuide()
  showKeyGuide.value = false
  router.push('/settings')
}

const rememberKeyGuide = () => localStorage.setItem('aiKeyGuideSeen', 'true')

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed.value))
}

onMounted(async () => {
  startTaskPolling()
  // 首次使用引导：未配置 API Key 时弹窗提示
  try {
    const s = await getSettings()
    if (!s.deepseek_configured && localStorage.getItem('aiKeyGuideSeen') !== 'true') {
      showKeyGuide.value = true
    }
  } catch { /* 后端未启动等场景静默 */ }
})
onUnmounted(stopTaskPolling)
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }
.layout { height: 100%; }

/* —— 侧边栏：白露晨雾青白渐变 —— */
.aside {
  background: var(--bailu-bg-gradient);
  border-right: 1px solid var(--el-border-color-light);
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
  height: 100vh;
  transition: width .2s ease;
}
/* 侧边栏底部淡露纹 */
.aside::after {
  content: '';
  position: absolute;
  bottom: -60px; left: -40px;
  width: 260px; height: 260px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(157, 192, 209, 0.16) 0%, transparent 70%);
  pointer-events: none;
}

.logo {
  display: flex; align-items: center; gap: 10px;
  padding: 22px 18px 18px;
}
.logo-dew {
  font-size: 26px;
  filter: drop-shadow(0 0 6px rgba(62, 127, 163, 0.35));
  animation: dewFloat 3s ease-in-out infinite;
}
@keyframes dewFloat {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}
.logo-text { display: flex; flex-direction: column; line-height: 1.2; }
.logo-title {
  font-size: 16px; font-weight: 700;
  color: var(--bailu-text-deep);
  letter-spacing: 1px;
}
.logo-sub {
  font-size: 11px; color: var(--bailu-accent);
  letter-spacing: 3px; margin-top: 3px;
}

.menu {
  background: transparent;
  flex: 1;
  padding-top: 4px;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
  scrollbar-width: thin;
  scrollbar-color: rgba(194, 162, 133, 0.3) transparent;
}
.aside .menu:not(.el-menu--collapse) { width: 100%; }
.menu::-webkit-scrollbar { width: 4px; }
.menu::-webkit-scrollbar-track { background: transparent; }
.menu::-webkit-scrollbar-thumb { background: rgba(194, 162, 133, 0.3); border-radius: 2px; }
.menu .el-menu-item {
  color: rgba(245, 240, 232, 0.75);
  margin: 3px 10px;
  border-radius: 10px;
}
.menu .el-menu-item.is-active {
  background: rgba(185, 138, 88, .2);
  color: #f2d4ad;
  box-shadow: inset 3px 0 0 #b98a58;
  border-right: none;
  font-weight: 600;
}
.menu .el-menu-item:hover { background: rgba(245, 240, 232, 0.1); color: #F5F0E8; }
.menu :deep(.el-menu-item-group__title) {
  font-size: 11px; color: rgba(194, 162, 133, 0.7);
  padding: 8px 10px 2px; letter-spacing: 1px;
  text-transform: uppercase;
}

/* —— 侧边栏底部：露珠 + 诗句 —— */
.aside-footer {
  position: relative;
  height: 48px;
  flex-shrink: 0;
  border-top: 1px dashed rgba(62, 127, 163, 0.25);
  display: flex; align-items: center; justify-content: center;
}
.dew-dot {
  position: absolute; bottom: 22px;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: rgba(194, 162, 133, 0.5);
  animation: dewDrop 2.4s ease-in-out infinite;
}
@keyframes dewDrop {
  0%, 100% { transform: translateY(0); opacity: 0.5; }
  50% { transform: translateY(-6px); opacity: 1; }
}
.aside-poem {
  font-size: 11px; color: rgba(245, 240, 232, 0.5);
  letter-spacing: 2px;
  font-style: italic;
}

/* —— 页头：白露晨光 —— */
.header {
  display: flex; align-items: center; justify-content: space-between;
  height: 48px;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--bailu-header-bg);
  padding: 0 20px;
}
.header-leading,.header-actions{display:flex;align-items:center;gap:10px}.header-leading>div{display:flex;flex-direction:column}.page-context{margin-top:2px;font-size:10px;letter-spacing:1px;color:#8f806e}.collapse-button{color:var(--bailu-text-deep)}.mobile-menu{display:none}.task-button{border-color:rgba(139,90,43,.22);background:rgba(245,240,232,.66);color:#6f4721}.task-button span{margin-left:5px}.mobile-logo{display:flex;align-items:center;gap:10px;padding:14px 10px 22px;color:#f5f0e8}.mobile-logo>span{font-size:24px}.mobile-logo div{display:flex;flex-direction:column}.mobile-logo small{margin-top:3px;color:rgba(245,240,232,.6)}
.page-title { font-size: 17px; font-weight: 600; color: var(--bailu-text-deep); letter-spacing: 1px; }
.reader-app-header { height: 44px; padding-top: 0; padding-bottom: 0; }
.reader-app-header .page-context,
.reader-app-header .header-slogan { display: none; }
.reader-app-header .page-title { font-size: 14px; letter-spacing: .5px; }
.reader-main { padding: 8px 12px 12px; }
.header-slogan {
  font-size: 11px; color: rgba(245, 240, 232, 0.55);
  letter-spacing: 2px;
}

.main { background: var(--el-bg-color-page); overflow: auto; padding: 12px; }

@media (max-width: 820px) {
  .desktop-aside{display:none}.collapse-button,.header-slogan{display:none}.mobile-menu{display:inline-flex}.header{padding:0 10px}.main{padding:8px}.task-badge{margin-right:8px}.task-button{padding:7px 10px}.page-title{font-size:15px}.mobile-drawer .el-drawer__body{padding:0;background:var(--bailu-bg-gradient)}
}
@media (max-width: 1180px) {
  .layout.is-reader-layout .desktop-aside { display:none; }
  .layout.is-reader-layout .collapse-button { display:none; }
  .layout.is-reader-layout .mobile-menu { display:inline-flex; }
}

/* —— Markdown 排版层级（H1 醒目 / 层级分明 / 行高舒适） —— */
.markdown-body { color: #333333; line-height: 1.9; }
.markdown-body h1 {
  font-size: 1.75em; font-weight: 800; color: #8B5A2B;
  margin: 0.9em 0 0.5em; padding-bottom: 0.35em;
  border-bottom: 3px solid #C2A285;
  letter-spacing: 0.01em;
}
.markdown-body h2 {
  font-size: 1.4em; font-weight: 700; color: #5b4a35;
  margin: 0.9em 0 0.45em; padding-bottom: 0.25em;
  border-bottom: 2px solid var(--el-border-color);
}
.markdown-body h3 { font-size: 1.2em; font-weight: 700; color: #44473F; margin: 0.7em 0 0.35em; }
.markdown-body h4 { font-size: 1.05em; font-weight: 700; color: #55564b; margin: 0.6em 0 0.3em; }
.markdown-body h5 { font-size: 1em; font-weight: 600; color: #66685c; margin: 0.5em 0 0.25em; }
.markdown-body p { margin: 0.45em 0; line-height: 1.9; color: #333333; }
.markdown-body ul, .markdown-body ol { padding-left: 1.7em; margin: 0.45em 0; }
.markdown-body li { margin: 0.25em 0; line-height: 1.85; color: #333333; }
.markdown-body li::marker { color: #8B5A2B; }
.markdown-body code { background: #ede5d8; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; color: #6F4721; }
.markdown-body pre { background: #f1ebe0; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid var(--el-border-color-lighter); }
.markdown-body pre code { background: none; padding: 0; color: #333; }
.markdown-body blockquote { border-left: 4px solid #C2A285; padding-left: 14px; color: #6e6e60; margin: 0.6em 0; background: #f7f2e9; padding: 8px 14px; border-radius: 0 6px 6px 0; }
.markdown-body strong { font-weight: 700; color: #4a3a28; }
.markdown-body table { border-collapse: collapse; margin: 0.7em 0; }
.markdown-body th, .markdown-body td { border: 1px solid var(--el-border-color); padding: 6px 12px; }
.markdown-body th { background: #ede5d8; font-weight: 700; }

/* —— 首次使用引导弹窗 —— */
.guide-body { line-height: 1.9; font-size: 14px; color: var(--el-text-color-primary); }
.guide-body p { margin-bottom: 10px; }
.guide-steps { padding-left: 20px; margin-bottom: 10px; }
.guide-tip { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
