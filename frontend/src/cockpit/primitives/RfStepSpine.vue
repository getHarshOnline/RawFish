<template>
  <!-- done | active | reachable | locked · halted.
       Locked = --t4 + title reason. Strikethrough is banned product-wide. -->
  <nav class="rf-spine" aria-label="Run steps">
    <button
      v-for="item in items"
      :key="item.id"
      type="button"
      :class="['rf-spine__step', stateClass(item)]"
      :disabled="item.state === 'locked'"
      :title="item.state === 'locked' ? item.reason : item.label"
      :aria-current="item.state === 'active' || item.state === 'halted' ? 'step' : undefined"
      @click="$emit('go', item)"
    >
      <span class="rf-spine__num">{{ item.num }}</span>
      <span>{{ item.label }}</span>
    </button>
  </nav>
</template>

<script setup>
defineProps({
  /* [{ id, num, label, state: done|active|reachable|locked|halted, reason }] */
  items: { type: Array, required: true }
})

defineEmits(['go'])

function stateClass (item) {
  if (item.state === 'reachable') return null
  return `rf-spine__step--${item.state}`
}
</script>
