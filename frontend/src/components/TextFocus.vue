<template>
  <div class="text-focus" :aria-label="caption">
    <div class="text-focus__progress" aria-hidden="true"><span ref="progressEl" /></div>
    <div ref="scrollEl" class="text-focus__scroll" @scroll.passive="updateProgress">
      <div class="text-focus__column">
        <span class="text-focus__caption">{{ caption }}</span>
        <p ref="paragraphEl" class="text-focus__paragraph">
          <span v-for="(char, index) in characters" :key="index" class="text-focus__char">{{ char }}</span>
        </p>
        <p v-if="remainingText" class="text-focus__remaining">{{ remainingText }}</p>
      </div>
    </div>
    <span ref="hintEl" class="text-focus__hint" aria-hidden="true">向下滚动 ↓</span>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps({ text: { type: String, default: '' }, caption: { type: String, default: 'ANNOTATION / 批注原文' } })
const characters = computed(() => Array.from(props.text || '').slice(0, 96))
const remainingText = computed(() => Array.from(props.text || '').slice(96).join(''))
const scrollEl = ref(null)
const paragraphEl = ref(null)
const progressEl = ref(null)
const hintEl = ref(null)

const updateProgress = () => {
  const el = scrollEl.value
  if (!el || !paragraphEl.value) return
  const progress = Math.min(1, el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight))
  const chars = paragraphEl.value.children
  const total = chars.length
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  for (let i = 0; i < total; i++) {
    const lit = Math.max(0, Math.min(1, (progress - i / total) / (1.5 / total)))
    chars[i].style.opacity = reduced ? '1' : String(.15 + .85 * lit)
  }
  if (progressEl.value) progressEl.value.style.transform = `scaleX(${progress})`
  if (hintEl.value) hintEl.value.style.opacity = reduced ? '0' : String(Math.max(0, 1 - progress * 12))
}

onMounted(updateProgress)
watch(() => props.text, async () => { await nextTick(); if (scrollEl.value) scrollEl.value.scrollTop = 0; updateProgress() })
</script>

<style scoped>
.text-focus{position:relative;isolation:isolate;width:100%;border:1px solid #e4e4e7;border-radius:12px;background:#fff;color:#09090b;overflow:hidden}
.text-focus__scroll{height:320px;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:#a1a1aa transparent}
.text-focus__scroll::-webkit-scrollbar{width:5px}
.text-focus__scroll::-webkit-scrollbar-thumb{border-radius:99px;background:#a1a1aa}
.text-focus__column{min-height:800px;padding:28px 22px 40px}
.text-focus__caption{font:600 10px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.14em;color:#71717a;text-transform:uppercase}
.text-focus__paragraph{margin:24px 0 0;font-family:var(--study-font-ui);font-size:18px;font-weight:500;line-height:2;overflow-wrap:anywhere}
.text-focus__remaining{margin:28px 0 0;font-family:var(--study-font-ui);font-size:16px;line-height:1.9;white-space:pre-wrap;overflow-wrap:anywhere}
.text-focus__char{opacity:.15;white-space:pre-wrap;will-change:opacity}
.text-focus__progress{position:absolute;top:0;left:0;right:0;z-index:2;height:2px;background:#e4e4e7}
.text-focus__progress span{display:block;width:100%;height:100%;background:#18181b;transform:scaleX(0);transform-origin:left}
.text-focus__hint{position:absolute;bottom:12px;left:50%;z-index:2;transform:translateX(-50%);padding:5px 10px;border:1px solid #e4e4e7;border-radius:999px;background:rgba(255,255,255,.94);box-shadow:0 2px 8px rgba(24,24,27,.08);color:#52525b;font-size:11px;white-space:nowrap;pointer-events:none}
@media(prefers-reduced-motion:reduce){.text-focus__char{opacity:1}.text-focus__hint{display:none}}
</style>
