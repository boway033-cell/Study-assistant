<template>
  <nav class="nav-shell" :class="{ collapsed }" aria-label="产品导航">
    <span v-if="!collapsed" class="section-label">工作区</span>
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
        <template #title><span class="journey-copy"><b><i>01</i> 资料库</b><small>导入、归档、阅读</small></span></template>
      </el-menu-item>
      <el-menu-item index="/knowledge-hub" class="journey-item">
        <el-icon><Reading /></el-icon>
        <template #title><span class="journey-copy"><b><i>02</i> 研读</b><small>理解、问答、证据、报告</small></span></template>
      </el-menu-item>
      <el-menu-item index="/writing" class="journey-item">
        <el-icon><EditPen /></el-icon>
        <template #title><span class="journey-copy"><b><i>03</i> 写作</b><small>成稿、审阅、Word</small></span></template>
      </el-menu-item>
      <el-menu-item index="/literature-workbench" class="journey-item">
        <el-icon><Tickets /></el-icon>
        <template #title><span class="journey-copy"><b><i>04</i> 汇报</b><small>提纲、来源、PPTX</small></span></template>
      </el-menu-item>

      <div v-if="!collapsed" class="menu-divider"><span>管理</span></div>
      <el-sub-menu index="tools">
        <template #title><el-icon><Grid /></el-icon><span>更多工具</span></template>
        <el-menu-item index="/quiz"><el-icon><Checked /></el-icon><template #title>自测</template></el-menu-item>
        <el-menu-item index="/knowledge-health"><el-icon><CircleCheck /></el-icon><template #title>知识库健康</template></el-menu-item>
        <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon><template #title>知识库洞察</template></el-menu-item>
      </el-sub-menu>
      <el-menu-item index="/settings" class="settings-item"><el-icon><Setting /></el-icon><template #title>设置</template></el-menu-item>
    </el-menu>
  </nav>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Checked, CircleCheck, DataAnalysis, EditPen,
  FolderOpened, Grid, Reading, Setting, Tickets,
} from '@element-plus/icons-vue'

defineProps({ collapsed: { type: Boolean, default: false } })
defineEmits(['navigate'])

const route = useRoute()
const menuRef = ref(null)
const activeRoute = computed(() => {
  if (route.path.startsWith('/reader/')) return '/library'
  if (['/sensemaking', '/chat', '/notes', '/knowledge', '/study', '/graph'].includes(route.path)) return '/knowledge-hub'
  return route.path
})
const activeGroup = computed(() => {
  if (['/quiz', '/knowledge-health', '/stats'].includes(route.path)) return 'tools'
  return ''
})
const defaultOpeneds = computed(() => activeGroup.value ? [activeGroup.value] : [])

watch(activeGroup, (group) => {
  if (group) nextTick(() => menuRef.value?.open(group))
})
</script>

<style scoped>
.nav-shell{display:flex;min-height:0;flex:1;flex-direction:column}.section-label{margin:12px 17px 8px;color:rgba(210,174,132,.75);font-size:10px;letter-spacing:1.4px}.journey-item{height:58px!important;line-height:normal!important}.journey-copy{display:flex;min-width:0;flex-direction:column;gap:4px}.journey-copy b{font-size:13px;font-weight:600}.journey-copy i{margin-right:3px;color:rgba(211,175,132,.72);font:500 9px var(--study-font-latin);font-style:normal;letter-spacing:.6px}.journey-copy small{overflow:hidden;color:rgba(245,240,232,.48);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.menu-divider{display:flex;align-items:center;gap:8px;margin:10px 16px 3px;color:rgba(210,174,132,.62);font-size:10px;letter-spacing:1.2px}.menu-divider::after{height:1px;flex:1;background:rgba(239,215,184,.12);content:''}.menu :deep(.el-sub-menu__title){height:46px;margin:2px 10px;border-radius:9px;color:rgba(245,240,232,.7)}.menu :deep(.el-sub-menu__title:hover){background:rgba(245,240,232,.08);color:#f5f0e8}.menu :deep(.el-sub-menu .el-menu-item){height:42px;min-width:0;margin-block:1px;padding-left:42px!important;font-size:12px}.menu :deep(.el-sub-menu .el-menu){background:rgba(9,30,31,.12)}.menu :deep(.settings-item){height:46px;margin:2px 10px;border-radius:9px;color:rgba(245,240,232,.7)}.collapsed .menu{padding-top:2px}.collapsed .journey-item{height:50px!important}.collapsed .menu :deep(.el-sub-menu__title){margin-inline:10px}.collapsed .menu :deep(.el-menu-item){margin-inline:10px}
</style>
