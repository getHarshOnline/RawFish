<template>
  <!-- Bounded continuous — the number beside the track is the source of
       truth and always typeable (board 09). Sliders snap to steps. -->
  <div class="rf-rangepair">
    <input
      type="range"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="stepSize"
      :disabled="disabled"
      :style="{ '--pct': pct + '%' }"
      @input="emitValue($event.target.value)"
    >
    <input
      type="number"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="stepSize"
      :disabled="disabled"
      @change="emitValue($event.target.value)"
    >
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 1 },
  stepSize: { type: Number, default: 0.01 },
  integer: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const pct = computed(() => {
  const span = props.max - props.min
  if (span <= 0) return 0
  const raw = ((Number(props.modelValue) - props.min) / span) * 100
  return Math.max(0, Math.min(100, Math.round(raw)))
})

function emitValue (raw) {
  const parsed = props.integer ? Number.parseInt(raw, 10) : Number(raw)
  if (!Number.isFinite(parsed)) return
  emit('update:modelValue', Math.max(props.min, Math.min(props.max, parsed)))
}
</script>
