<template>
  <component
    :is="as"
    :class="chipClass"
    :type="as === 'button' ? 'button' : undefined"
    :disabled="as === 'button' ? disabled : undefined"
    :title="reason || undefined"
  >
    <span v-if="variant === 'accent' && dot" class="rf-chip__dot" aria-hidden="true"></span>
    <slot />
  </component>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  as: { type: String, default: 'span' },
  /* default | selected | accent | ink | caution | ok | pending */
  variant: { type: String, default: 'default' },
  dot: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  /* complete clause placed at the thing it explains (board 12) */
  reason: { type: String, default: '' }
})

const chipClass = computed(() => [
  'rf-chip',
  props.variant !== 'default' ? `rf-chip--${props.variant}` : null
])
</script>
