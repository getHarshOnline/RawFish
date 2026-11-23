<template>
  <article class="rf-metric">
    <strong>{{ value }}</strong>
    <span>{{ label }}</span>
  </article>
</template>

<script setup>
defineProps({
  value: { type: [String, Number], required: true },
  label: { type: String, required: true }
})
</script>
