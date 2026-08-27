<template>
  <aside class="study-inspector">
    <header class="study-inspector__head">
      <span>{{ title }}</span>
      <button v-if="closable" type="button" :aria-label="`关闭${title}`" @click="$emit('close')">×</button>
      <slot name="head-actions" />
    </header>
    <div class="study-inspector__body"><slot /></div>
  </aside>
</template>

<script setup>
defineProps({
  title: { type: String, default: '检查器' },
  closable: { type: Boolean, default: false },
})
defineEmits(['close'])
</script>
