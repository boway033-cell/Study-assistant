<template>
  <section class="el-card glow-border-card" :class="{ 'glow-disabled': !uiPreferences.glowBorders }">
    <div class="glow-border-card__surface">
      <header v-if="$slots.header" class="el-card__header"><slot name="header" /></header>
      <div class="el-card__body"><slot /></div>
    </div>
  </section>
</template>

<script setup>
import { uiPreferences } from '../stores/uiPreferences'
</script>

<style>
@property --study-glow-angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

.glow-border-card {
  --glow-border-width: 1.5px;
  --glow-color: #6366f1;
  position: relative;
  isolation: isolate;
  min-width: 0;
  overflow: visible;
  padding: var(--glow-border-width);
  border: 0 !important;
  border-radius: var(--study-radius-md);
  background: var(--study-card-border);
  box-shadow: none !important;
}

.glow-border-card::before,
.glow-border-card::after {
  position: absolute;
  inset: 0;
  padding: var(--glow-border-width);
  border-radius: inherit;
  background: conic-gradient(from var(--study-glow-angle), transparent 0deg, var(--glow-color) 45deg, transparent 90deg, transparent 360deg);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  animation: study-card-orbit 4s linear infinite;
  pointer-events: none;
  content: '';
}

.glow-border-card::before { z-index: 2; }
.glow-border-card::after {
  z-index: -1;
  padding: 0;
  -webkit-mask: none;
  mask: none;
  filter: blur(8px);
  opacity: .4;
}
.glow-border-card:hover::before,
.glow-border-card:hover::after { animation-duration: 2s; }
.glow-border-card__surface {
  position: relative;
  z-index: 1;
  min-width: 0;
  border-radius: calc(var(--study-radius-md) - var(--glow-border-width));
  background: var(--el-bg-color);
}
.glow-border-card__surface > .el-card__header { border-radius: inherit; border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
.glow-disabled::before,
.glow-disabled::after { display: none; animation: none; }

@keyframes study-card-orbit { to { --study-glow-angle: 360deg; } }
@media (prefers-reduced-motion: reduce) {
  .glow-border-card::before,
  .glow-border-card::after { animation: none; --study-glow-angle: 45deg; }
}
</style>
