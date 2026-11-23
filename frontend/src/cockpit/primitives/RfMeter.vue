<template>
  <div :class="['rf-meter', variant === 'budget' ? 'rf-meter--budget' : null, over ? 'rf-meter--over' : null]">
    <div class="rf-meter__head">
      <span class="rf-meter__label">{{ label }}</span>
      <span class="rf-meter__value"><slot name="value">{{ display }}</slot></span>
    </div>
    <div class="rf-meter__track">
      <span class="rf-meter__fill" :style="{ inlineSize: clampedPct + '%' }"></span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  display: { type: [String, Number], default: '' },
  pct: { type: Number, default: 0 },
  /* data (ink fill) | budget (accent fill — budget/primary only, board 10) */
  variant: { type: String, default: 'data' },
  over: { type: Boolean, default: false }
})

const clampedPct = computed(() => Math.max(0, Math.min(100, Math.round(props.pct))))
</script>
