<template>
  <!-- 2–5 fixed choices · one always selected · an enum typed into a text
       box is a build failure (board 09). Unavailable options dim and carry
       a title reason — never a rewritten label. -->
  <div class="rf-enum" role="radiogroup">
    <button
      v-for="option in options"
      :key="option.id"
      type="button"
      role="radio"
      :aria-checked="option.id === modelValue ? 'true' : 'false'"
      :disabled="disabled || option.disabled"
      :title="option.disabled ? option.reason : undefined"
      @click="$emit('update:modelValue', option.id)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<script setup>
defineProps({
  /* [{ id, label, disabled?, reason? }] */
  options: { type: Array, required: true },
  modelValue: { type: [String, Number], default: '' },
  disabled: { type: Boolean, default: false }
})

defineEmits(['update:modelValue'])
</script>
