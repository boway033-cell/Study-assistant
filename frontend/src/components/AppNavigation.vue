<template>
  <nav class="nav-shell" :class="{ collapsed }" aria-label="产品导航">
    <section v-if="!collapsed" class="journey-guide" aria-live="polite">
      <span>当前路径</span>
      <strong>{{ guide.title }}</strong>
      <p>{{ guide.description }}</p>
    </section>

    <span v-if="!collapsed" class="section-label">核心工作流</span>
    <el-menu
      ref="menuRef"
      :default-active="activeRoute"
      :default-openeds="defaultOpeneds"
      router
      unique-opened
      :collapse="collapsed"
      class="menu"
      @select="$emit('navigate')"
    >
      <el-menu-item index="/library" class="journey-item">
        <el-icon><FolderOpened /></el-icon>
        <template #title><span class="journey-copy"><b><i>01</i> 资料入库</b><small>导入、归档与打开原文</small></span></template>
      </el-menu-item>
      <el-menu-item index="/knowledge-hub" class="journey-item">
        <el-icon><Reading /></el-icon>
        <template #title><span class="journey-copy"><b><i>02</i> 研读与沉淀</b><small>笔记、证据与跨文献分析</small></span></template>
      </el-menu-item>
      <el-menu-item index="/writing" class="journey-item">
        <el-icon><EditPen /></el-icon>
        <template #title><span class="journey-copy"><b><i>03</i> 写作工作台</b><small>从已选知识对象取材</small></span></template>
      </el-menu-item>
      <el-menu-item index="/literature-workbench" class="journey-item">
        <el-icon><Tickets /></el-icon>
        <template #title><span class="journey-copy"><b><i>04</i> PPTX 汇报</b><small>把研究证据组织成演示</small></span></template>
      </el-menu-item>

      <div v-if="!collapsed" class="menu-divider"><span>按需展开</span></div>
      <el-sub-menu index="assist">
        <template #title><el-icon><Compass /></el-icon><span>查证与维护</span></template>
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon><template #title>问知识库</template></el-menu-item>
        <el-menu-item index="/knowledge-health"><el-icon><CircleCheck /></el-icon><template #title>知识库健康</template></el-menu-item>
        <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon><template #title>知识库洞察</template></el-menu-item>
      </el-sub-menu>
      <el-sub-menu index="optional">
        <template #title><el-icon><Grid /></el-icon><span>可选工具与设置</span></template>
        <el-menu-item index="/quiz"><el-icon><Checked /></el-icon><template #title>刷题自测</template></el-menu-item>
        <el-menu-item index="/draw"><el-icon><MagicStick /></el-icon><template #title>AI 绘图</template></el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon><template #title>设置</template></el-menu-item>
      </el-sub-menu>
    </el-menu>
  </nav>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  ChatDotRound, Checked, CircleCheck, Compass, DataAnalysis, EditPen,
  FolderOpened, Grid, MagicStick, Reading, Setting, Tickets,
} from '@element-plus/icons-vue'

defineProps({ collapsed: { type: Boolean, default: false } })
defineEmits(['navigate'])

const route = useRoute()
const menuRef = ref(null)
const activeRoute = computed(() => route.path.startsWith('/reader/') ? '/library' : route.path)
const activeGroup = computed(() => {
  if (['/chat', '/knowledge-health', '/stats'].includes(route.path)) return 'assist'
  if (['/quiz', '/draw', '/settings'].includes(route.path)) return 'optional'
  return ''
})
const defaultOpeneds = computed(() => activeGroup.value ? [activeGroup.value] : [])

const guide = computed(() => {
  if (route.path === '/library' || route.path.startsWith('/reader/')) return { title: '从资料开始', description: '先导入并确认解析就绪，再进入研读。' }
  if (route.path === '/knowledge-hub' || route.path === '/chat') return { title: '把原文变成知识', description: '提取笔记、证据与可回查的研究结论。' }
  if (route.path === '/writing') return { title: '从知识对象写作', description: '选择已有笔记或证据，保留来源链。' }
  if (route.path === '/literature-workbench') return { title: '组织研究汇报', description: '从已选知识对象生成可追溯的 PPTX。' }
  if (route.path === '/knowledge-health' || route.path === '/stats') return { title: '维护知识库质量', description: '先治理解析、来源和引用问题。' }
  return { title: '按需使用辅助能力', description: '核心资料与研究成果仍保存在知识库。' }
})

watch(activeGroup, (group) => {
  if (group) nextTick(() => menuRef.value?.open(group))
})
</script>

<style scoped>
.nav-shell{display:flex;min-height:0;flex:1;flex-direction:column}.journey-guide{margin:2px 12px 9px;padding:12px 13px;border:1px solid rgba(239,215,184,.16);border-radius:11px;background:rgba(13,39,40,.28);color:#f5f0e8}.journey-guide>span,.section-label{color:rgba(210,174,132,.75);font-size:10px;letter-spacing:1.4px}.journey-guide strong{display:block;margin:5px 0 4px;font-family:var(--study-font-display);font-size:15px}.journey-guide p{color:rgba(245,240,232,.6);font-size:11px;line-height:1.55}.section-label{margin:4px 17px 2px}.journey-item{height:58px!important;line-height:normal!important}.journey-copy{display:flex;min-width:0;flex-direction:column;gap:4px}.journey-copy b{font-size:13px;font-weight:600}.journey-copy i{margin-right:3px;color:rgba(211,175,132,.72);font:500 9px var(--study-font-latin);font-style:normal;letter-spacing:.6px}.journey-copy small{overflow:hidden;color:rgba(245,240,232,.48);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.menu-divider{display:flex;align-items:center;gap:8px;margin:10px 16px 3px;color:rgba(210,174,132,.62);font-size:10px;letter-spacing:1.2px}.menu-divider::after{height:1px;flex:1;background:rgba(239,215,184,.12);content:''}.menu :deep(.el-sub-menu__title){height:46px;margin:2px 10px;border-radius:9px;color:rgba(245,240,232,.7)}.menu :deep(.el-sub-menu__title:hover){background:rgba(245,240,232,.08);color:#f5f0e8}.menu :deep(.el-sub-menu .el-menu-item){height:42px;min-width:0;margin-block:1px;padding-left:42px!important;font-size:12px}.menu :deep(.el-sub-menu .el-menu){background:rgba(9,30,31,.12)}.collapsed .menu{padding-top:2px}.collapsed .journey-item{height:50px!important}.collapsed .menu :deep(.el-sub-menu__title){margin-inline:10px}.collapsed .menu :deep(.el-menu-item){margin-inline:10px}
</style>
