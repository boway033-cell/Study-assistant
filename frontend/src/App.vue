<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">📚 保研复习助手</div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item index="/library"><el-icon><Folder /></el-icon>资料库</el-menu-item>
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon>AI 问答</el-menu-item>
        <el-menu-item index="/review"><el-icon><Clock /></el-icon>卡片复习</el-menu-item>
        <el-menu-item index="/quiz"><el-icon><EditPen /></el-icon>刷题自测</el-menu-item>
        <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon>学习统计</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title || '' }}</span>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { Folder, ChatDotRound, Clock, EditPen, DataAnalysis, Setting } from '@element-plus/icons-vue'
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }
.layout { height: 100%; }
.aside { background: #f5f7fa; border-right: 1px solid #e4e7ed; }
.logo { font-size: 16px; font-weight: 700; padding: 20px 16px; color: #409eff; }
.menu { border-right: none; }
.header {
  display: flex; align-items: center;
  border-bottom: 1px solid #e4e7ed; background: #fff;
}
.page-title { font-size: 16px; font-weight: 600; }
.main { background: #fff; overflow: auto; }
</style>
