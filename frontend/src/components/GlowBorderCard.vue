<template>
  <section class="el-card glow-border-card" :class="{ 'glow-disabled': !uiPreferences.glowBorders }" @pointermove="moveGlow">
    <div class="glow-border-card__surface">
      <header v-if="$slots.header" class="el-card__header"><slot name="header" /></header>
      <div class="el-card__body"><slot /></div>
    </div>
  </section>
</template>

<script setup>
import { uiPreferences } from '../stores/uiPreferences'

const moveGlow = event => {
  const el = event.currentTarget
  const rect = el.getBoundingClientRect()
  el.style.setProperty('--mouse-x', `${event.clientX - rect.left}px`)
  el.style.setProperty('--mouse-y', `${event.clientY - rect.top}px`)
}
</script>

<style>
.glow-border-card {
  --mouse-x: 50%;
  --mouse-y: 0px;
  position: relative;
  isolation: isolate;
  min-width: 0;
  padding: 1px;
  border: 0 !important;
  border-radius: 12px;
  background: radial-gradient(240px circle at var(--mouse-x) var(--mouse-y), rgba(161,161,170,.55), transparent 70%), #e4e4e7;
  box-shadow: none !important;
}
.glow-border-card__surface {
  position: relative;
  min-width: 0;
  overflow: hidden;
  border-radius: 11px;
  background: #fff;
}
.glow-border-card__surface::before {
  position: absolute;
  inset: 0;
  z-index: 0;
  border-radius: inherit;
  background: radial-gradient(280px circle at var(--mouse-x) var(--mouse-y), rgba(161,161,170,.14), transparent 65%);
  pointer-events: none;
  content: '';
}
.glow-border-card__surface > * { position: relative; z-index: 1; }
.glow-border-card__surface > .el-card__header { border-bottom: 1px solid #e4e4e7; }
.glow-disabled { background: #e4e4e7; }
.glow-disabled .glow-border-card__surface::before { display: none; }
</style>
