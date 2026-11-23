<template>
  <!-- Disabled ⇒ reason REQUIRED, rendered BESIDE the button as a chip —
       never inside the label (boards 09/11, law 07). -->
  <span v-if="disabled && reason" class="rf-btnset">
    <button :class="btnClass" type="button" disabled>
      <slot />
    </button>
    <span class="rf-chip" role="note">{{ reason }}</span>
  </span>
  <button
    v-else
    :class="btnClass"
    :type="type"
    :disabled="disabled"
    :title="disabled && reason ? reason : undefined"
    @click="$emit('click', $event)"
  >
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /* primary | default | quiet | danger */
  role: { type: String, default: 'default' },
  type: { type: String, default: 'button' },
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  reason: { type: String, default: '' }
})

defineEmits(['click'])

const btnClass = computed(() => [
  'rf-btn',
  props.role !== 'default' ? `rf-btn--${props.role}` : null,
  props.block ? 'rf-btn--block' : null
])
</script>
