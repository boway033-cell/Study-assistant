<template>
  <article class="study-list-row" :class="{ active, interactive }">
    <slot />
  </article>
</template>

<script setup>
defineProps({
  active: { type: Boolean, default: false },
  interactive: { type: Boolean, default: true },
})
</script>
