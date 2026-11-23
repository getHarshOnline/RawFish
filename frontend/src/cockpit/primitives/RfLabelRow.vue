<template>
  <div :class="['rf-labelrow', tone === 'dim' ? 'rf-labelrow--dim' : null, tone === 'danger' ? 'rf-labelrow--danger' : null]">
    <span class="rf-labelrow__label">{{ label }}</span>
    <span class="rf-labelrow__value"><slot>{{ value }}</slot></span>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '' },
  tone: { type: String, default: 'default' }
})
</script>
