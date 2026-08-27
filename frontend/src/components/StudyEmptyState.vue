<template>
  <div class="study-empty" :class="{ compact }" role="status">
    <span v-if="eyebrow" class="study-empty__eyebrow">{{ eyebrow }}</span>
    <b>{{ title }}</b>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.actions" class="study-empty__actions"><slot name="actions" /></div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
</script>
