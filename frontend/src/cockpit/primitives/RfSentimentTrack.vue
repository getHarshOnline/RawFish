<template>
  <div class="rf-sentiment">
    <div class="rf-sentiment__head">
      <span class="rf-meter__label">{{ label }}</span>
      <span class="rf-meter__value">{{ formatted }}</span>
    </div>
    <div class="rf-sentiment__track">
      <span class="rf-sentiment__tick" aria-hidden="true"></span>
      <span class="rf-sentiment__marker" :style="{ insetInlineStart: pctLeft + '%' }"></span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  /* −1 … +1 · monochrome · position encodes the value (board 10) */
  value: { type: Number, default: 0 }
})

const clamped = computed(() => Math.max(-1, Math.min(1, Number(props.value) || 0)))
const pctLeft = computed(() => Math.round(((clamped.value + 1) / 2) * 100))
const formatted = computed(() => `${clamped.value > 0 ? '+' : clamped.value < 0 ? '−' : ''}${Math.abs(clamped.value).toFixed(1)}`)
</script>
