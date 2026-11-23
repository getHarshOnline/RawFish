<template>
  <section :class="['rf-panel', hairline ? 'rf-panel--hairline' : null]">
    <header v-if="title || $slots.meta" class="rf-panel__head">
      <span class="rf-label">{{ title }}</span>
      <span v-if="$slots.meta" class="rf-meta"><slot name="meta" /></span>
    </header>
    <div :class="['rf-panel__body', flush ? 'rf-panel__body--flush' : null]">
      <slot />
    </div>
    <footer v-if="$slots.foot" class="rf-panel__foot">
      <slot name="foot" />
    </footer>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  hairline: { type: Boolean, default: false },
  flush: { type: Boolean, default: false }
})
</script>
