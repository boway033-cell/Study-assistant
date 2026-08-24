<template>
  <el-menu :default-active="$route.path" router :collapse="collapsed" class="menu" @select="$emit('navigate')">
    <el-menu-item-group title="个人知识库">
      <el-menu-item index="/library"><el-icon><Folder /></el-icon><template #title>文献知识库</template></el-menu-item>
      <el-menu-item index="/literature-workbench"><el-icon><Tickets /></el-icon><template #title>文献工作台</template></el-menu-item>
    </el-menu-item-group>
    <el-menu-item-group title="阅读与沉淀">
      <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon><template #title>知识库问答</template></el-menu-item>
      <el-menu-item index="/knowledge"><el-icon><Share /></el-icon><template #title>知识树</template></el-menu-item>
      <el-menu-item index="/graph"><el-icon><Connection /></el-icon><template #title>知识图谱</template></el-menu-item>
      <el-menu-item index="/study"><el-icon><MagicStick /></el-icon><template #title>综合研读</template></el-menu-item>
    </el-menu-item-group>
    <el-sub-menu index="more">
      <template #title><el-icon><Grid /></el-icon><span>更多学习工具</span></template>
      <el-menu-item index="/quiz">刷题自测</el-menu-item>
      <el-menu-item index="/draw">AI 绘图</el-menu-item>
      <el-menu-item index="/plan">学习计划</el-menu-item>
      <el-menu-item index="/stats">学习统计</el-menu-item>
    </el-sub-menu>
    <el-menu-item index="/settings"><el-icon><Setting /></el-icon><template #title>设置</template></el-menu-item>
  </el-menu>
</template>

<script setup>
import { Folder, ChatDotRound, Share, Setting, MagicStick, Connection, Tickets, Grid } from '@element-plus/icons-vue'

defineProps({ collapsed: { type: Boolean, default: false } })
defineEmits(['navigate'])
</script>
