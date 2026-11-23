<template>
  <span :class="['rf-label', tone === 'danger' ? 'rf-label--danger' : null, tone === 'accent' ? 'rf-label--accent' : null]">
    <slot />
  </span>
</template>

<script setup>
defineProps({
  tone: { type: String, default: 'default' }
})
</script>
