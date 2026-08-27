<template>
  <section class="study-commandbar" :class="{ compact }">
    <div class="study-commandbar__identity">
      <span v-if="eyebrow" class="study-commandbar__eyebrow">{{ eyebrow }}</span>
      <div class="study-commandbar__titleline">
        <h1 v-if="title">{{ title }}</h1>
        <slot name="title" />
        <slot name="status" />
      </div>
      <p v-if="description">{{ description }}</p>
      <slot name="description" />
    </div>
    <div v-if="$slots.default" class="study-commandbar__content"><slot /></div>
    <div v-if="$slots.actions" class="study-commandbar__actions"><slot name="actions" /></div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  eyebrow: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
</script>
