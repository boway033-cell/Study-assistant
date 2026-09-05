<template>
  <button
    type="button"
    class="keycap-card"
    :class="[{ 'keycap-compact': compact, 'keycap-flat': !uiPreferences.keycapButtons }, `keycap-${variant}`]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    :style="{ '--key-travel': `${depth}px` }"
    @pointerdown="pressPointer"
    @pointerup="release"
    @pointerleave="release"
    @pointercancel="release"
    @lostpointercapture="release"
    @keydown="pressKeyboard"
    @keyup="releaseKeyboard"
    @blur="release"
    @click="activate"
  >
    <motion.span aria-hidden="true" class="keycap-shadow keycap-shadow-rest" :style="{ opacity: restOpacity, y: faceY }" />
    <motion.span aria-hidden="true" class="keycap-shadow keycap-shadow-contact" :style="{ opacity: contactOpacity, y: faceY }" />
    <span aria-hidden="true" class="keycap-side" />
    <motion.span class="keycap-face" :style="{ y: faceY }">
      <span class="keycap-dish">
        <svg v-if="loading" class="keycap-spinner" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 3a9 9 0 0 1 9 9" /></svg>
        <slot>{{ letter }}</slot>
      </span>
    </motion.span>
    <span v-if="!compact" class="keycap-counter" aria-live="polite">×{{ count }}</span>
  </button>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { motion, useMotionValue, useSpring, useTransform, useReducedMotion } from 'motion-v'
import { uiPreferences } from '../stores/uiPreferences'

const props = defineProps({
  letter: { type: String, default: 'K' },
  compact: { type: Boolean, default: false },
  variant: { type: String, default: 'neutral' },
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['click'])
const depth = computed(() => props.compact ? 3 : 8)
const count = ref(0)
const reduceMotion = useReducedMotion()
const targetY = useMotionValue(0)
const springY = useSpring(targetY, { stiffness: 500, damping: 25 })
// Clamp spring overshoot so the face never moves below the physical side lip.
const faceY = useTransform(() => Math.max(0, Math.min(depth.value, springY.get())))
const restOpacity = useTransform(() => 1 - faceY.get() / depth.value)
const contactOpacity = useTransform(() => faceY.get() / depth.value)
let held = false

function press() {
  if (held || props.disabled || props.loading) return
  held = true
  count.value += 1
  if (uiPreferences.keycapButtons && !reduceMotion.value) targetY.set(depth.value)
}
function pressPointer(event) {
  if (event.button !== 0) return
  press()
}
function release() {
  held = false
  targetY.set(0)
}
function pressKeyboard(event) {
  if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) press()
}
function releaseKeyboard(event) {
  if (event.key === ' ' || event.key === 'Enter') release()
}
function activate(event) {
  if (!props.disabled && !props.loading) emit('click', event)
}
watch(() => [props.disabled, props.loading, uiPreferences.keycapButtons, reduceMotion.value], () => {
  release()
  if (!uiPreferences.keycapButtons || reduceMotion.value) springY.jump(0)
})
</script>

<style scoped>
.keycap-card {
  --key-radius: 16px;
  --key-face: #f4f4f5;
  --key-dish: #fafafa;
  --key-border: #e4e4e7;
  --key-side: #d4d4d8;
  --key-ink: #27272a;
  position: relative;
  display: inline-block;
  width: 128px;
  height: 136px;
  flex: none;
  margin: 0 0 26px;
  padding: 0 0 var(--key-travel);
  border: 0;
  border-radius: var(--key-radius);
  background: transparent;
  color: var(--key-ink);
  font-family: var(--study-font-ui);
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
  vertical-align: middle;
}
.keycap-card:hover { filter: none; }
.keycap-card:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 4px; }
.keycap-card:disabled { cursor: not-allowed; opacity: .5; }
.keycap-side,
.keycap-shadow { position: absolute; inset: 0 0 var(--key-travel); border-radius: var(--key-radius); pointer-events: none; }
.keycap-side { background: var(--key-side); transform: translateY(var(--key-travel)); }
.keycap-shadow-rest { box-shadow: 0 8px 14px rgba(0, 0, 0, .16); }
.keycap-shadow-contact { box-shadow: 0 1px 2px rgba(0, 0, 0, .20); }
.keycap-face {
  position: relative;
  display: flex;
  width: 100%;
  height: 128px;
  padding: 11px;
  border: 1px solid var(--key-border);
  border-radius: var(--key-radius);
  background: var(--key-face);
  pointer-events: none;
}
.keycap-dish {
  display: flex;
  width: 100%;
  height: 100%;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 12px;
  background: var(--key-dish);
  box-shadow: inset 0 3px 10px rgba(0, 0, 0, .10);
  font: 600 52px ui-monospace, 'Cascadia Code', Consolas, monospace;
}
.keycap-counter { position: absolute; top: calc(100% + 10px); left: 0; width: 100%; color: #71717a; font: 11px ui-monospace, Consolas, monospace; text-align: center; }
.keycap-compact { --key-radius: 8px; width: auto; height: 35px; margin: 0; }
.keycap-compact .keycap-face { width: auto; height: 32px; padding: 3px; }
.keycap-compact .keycap-dish { width: auto; padding: 0 9px; border-radius: 5px; box-shadow: inset 0 1px 3px rgba(0, 0, 0, .07); font: 500 12px var(--study-font-ui); white-space: nowrap; }
.keycap-compact .keycap-shadow-rest { box-shadow: 0 3px 7px rgba(0, 0, 0, .10); }
.keycap-warm { --key-face: #f5f0e8; --key-dish: #faf6ef; --key-border: #d4c9b8; --key-side: #c2a285; --key-ink: #6f4721; }
.keycap-primary { --key-face: #8b5a2b; --key-dish: #976234; --key-border: #a67c52; --key-side: #6f4721; --key-ink: #fffaf2; }
.keycap-flat .keycap-side,
.keycap-flat .keycap-shadow { display: none; }
.keycap-flat .keycap-dish { box-shadow: none; }
.keycap-spinner { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 2; animation: keycap-spin 1s linear infinite; }
.keycap-spinner circle { opacity: .2; }
@keyframes keycap-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .keycap-spinner { animation: none; } }
</style>
