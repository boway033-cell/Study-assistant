<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">
        <span class="logo-dew">💧</span>
        <div class="logo-text">
          <span class="logo-title">保研复习助手</span>
          <span class="logo-sub">白露 · 凝学</span>
        </div>
      </div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item index="/library"><el-icon><Folder /></el-icon>资料库</el-menu-item>
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon>AI 问答</el-menu-item>
        <el-menu-item index="/knowledge"><el-icon><Share /></el-icon>知识树</el-menu-item>
        <el-menu-item index="/quiz"><el-icon><EditPen /></el-icon>刷题自测</el-menu-item>
        <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon>学习统计</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>设置</el-menu-item>
      </el-menu>
      <div class="aside-footer">
        <div class="dew-dot" v-for="i in 3" :key="i" :style="{ left: 24 + i * 44 + 'px', animationDelay: i * 0.6 + 's' }"></div>
        <span class="aside-poem">蒹葭苍苍 · 白露为霜</span>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title || '' }}</span>
        <span class="header-slogan">白露三候 · 鸿雁来 · 玄鸟归 · 群鸟养羞</span>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 首次使用引导：未配置 API Key 时提示 -->
    <el-dialog v-model="showKeyGuide" title="欢迎使用保研复习助手 👋" width="520px" :close-on-click-modal="false" append-to-body>
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
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Folder, ChatDotRound, Share, EditPen, DataAnalysis, Setting } from '@element-plus/icons-vue'
import { getSettings } from './api'

const router = useRouter()
const showKeyGuide = ref(false)

const goSettings = () => {
  showKeyGuide.value = false
  router.push('/settings')
}

onMounted(async () => {
  // 首次使用引导：未配置 API Key 时弹窗提示
  try {
    const s = await getSettings()
    if (!s.deepseek_configured) {
      showKeyGuide.value = true
    }
  } catch { /* 后端未启动等场景静默 */ }
})
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
}
.menu .el-menu-item {
  color: var(--el-text-color-regular);
  margin: 2px 8px;
  border-radius: 8px;
}
.menu .el-menu-item.is-active {
  background: #e3eef3;
  color: var(--bailu-accent);
  border-right: none;
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(62, 127, 163, 0.12);
}
.menu .el-menu-item:hover { background: #eef5f8; }

/* —— 侧边栏底部：露珠 + 诗句 —— */
.aside-footer {
  position: relative;
  height: 64px;
  border-top: 1px dashed rgba(62, 127, 163, 0.25);
  display: flex; align-items: center; justify-content: center;
}
.dew-dot {
  position: absolute; bottom: 22px;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: rgba(62, 127, 163, 0.4);
  animation: dewDrop 2.4s ease-in-out infinite;
}
@keyframes dewDrop {
  0%, 100% { transform: translateY(0); opacity: 0.5; }
  50% { transform: translateY(-6px); opacity: 1; }
}
.aside-poem {
  font-size: 11px; color: #9db0ba;
  letter-spacing: 2px;
  font-style: italic;
}

/* —— 页头：白露晨光 —— */
.header {
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  background: var(--bailu-header-bg);
  padding: 0 20px;
}
.page-title { font-size: 17px; font-weight: 600; color: var(--bailu-text-deep); letter-spacing: 1px; }
.header-slogan {
  font-size: 11px; color: #a4b6bf;
  letter-spacing: 2px;
}

.main { background: var(--el-bg-color-page); overflow: auto; }

/* —— 首次使用引导弹窗 —— */
.guide-body { line-height: 1.9; font-size: 14px; color: var(--el-text-color-primary); }
.guide-body p { margin-bottom: 10px; }
.guide-steps { padding-left: 20px; margin-bottom: 10px; }
.guide-tip { color: var(--el-text-color-secondary); font-size: 13px; }
</style>