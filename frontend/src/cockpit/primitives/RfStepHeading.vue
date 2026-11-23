<template>
  <!-- num · title · nowrap hint — identical geometry on all 6 steps.
       Findings swaps the hint for its mode switch via the aside slot. -->
  <div class="rf-heading">
    <span class="rf-heading__num">{{ num }}</span>
    <h1>{{ title }}</h1>
    <span v-if="hint && !$slots.aside" class="rf-heading__hint">{{ hint }}</span>
    <span v-if="$slots.aside" class="rf-heading__aside"><slot name="aside" /></span>
  </div>
</template>

<script setup>
defineProps({
  num: { type: String, required: true },
  title: { type: String, required: true },
  hint: { type: String, default: '' }
})
</script>
