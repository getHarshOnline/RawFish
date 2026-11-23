<template>
  <!-- Exact quantity — typeable + square steppers · bounds always visible ·
       steppers clamp at bounds (board 09). -->
  <div class="rf-numberfield">
    <span class="rf-numberfield__box">
      <button type="button" :disabled="disabled || atMin" aria-label="decrement" @click="step(-1)">−</button>
      <input
        type="number"
        :value="modelValue"
        :min="min"
        :max="max"
        :step="stepSize"
        :disabled="disabled"
        @change="commit($event.target.value)"
        @keydown.esc="$event.target.value = String(modelValue)"
      >
      <button type="button" :disabled="disabled || atMax" aria-label="increment" @click="step(1)">+</button>
    </span>
    <span v-if="hasBounds" class="rf-meta">min {{ min }} · max {{ max }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  min: { type: Number, default: null },
  max: { type: Number, default: null },
  stepSize: { type: Number, default: 1 },
  integer: { type: Boolean, default: true },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const hasBounds = computed(() => props.min !== null && props.max !== null)
const atMin = computed(() => props.min !== null && props.modelValue <= props.min)
const atMax = computed(() => props.max !== null && props.modelValue >= props.max)

function clamp (value) {
  let next = props.integer ? Math.round(value) : value
  if (!Number.isFinite(next)) next = props.min ?? 0
  if (props.min !== null) next = Math.max(props.min, next)
  if (props.max !== null) next = Math.min(props.max, next)
  return next
}

function step (direction) {
  emit('update:modelValue', clamp((Number(props.modelValue) || 0) + direction * props.stepSize))
}

/* commit on enter / blur (change), never per keystroke (board 09) */
function commit (raw) {
  emit('update:modelValue', clamp(props.integer ? Number.parseInt(raw, 10) : Number(raw)))
}
</script>
