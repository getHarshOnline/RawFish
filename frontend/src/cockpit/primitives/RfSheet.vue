<template>
  <!-- Overlay sheet (board 06): summoned, filtered to its summoner, trailed,
       reversible. Read-only — no primary actions inside. ← pops one level;
       esc dismisses all. Every level is a URL. -->
  <aside
    ref="sheetRef"
    class="rf-sheet"
    role="dialog"
    aria-modal="false"
    :aria-label="title"
    tabindex="-1"
    @keydown.esc.stop.prevent="$emit('close')"
  >
    <div class="rf-sheet__bar">
      <span class="rf-sheet__badge">{{ badge }}</span>
      <span class="rf-sheet__title">{{ title }}</span>
      <span class="rf-sheet__nav">
        <button v-if="trail.length > 1" type="button" class="rf-chip" @click="$emit('back')">← back</button>
        <button type="button" class="rf-chip" @click="$emit('close')">esc close</button>
      </span>
    </div>
    <div class="rf-sheet__trail">
      <template v-for="(crumb, index) in trail" :key="crumb.hash">
        <span v-if="index > 0" class="rf-sheet__trail-sep">→</span>
        <span :class="['rf-chip', index === trail.length - 1 ? 'rf-chip--selected' : null]">{{ crumb.label }}</span>
      </template>
      <slot name="filters" />
    </div>
    <div class="rf-sheet__body">
      <slot />
    </div>
    <div class="rf-sheet__foot">{{ address }} — summoned, not mounted · ← pops · esc returns to the summoner</div>
  </aside>
</template>

<script setup>
import { onMounted, ref } from 'vue'

defineProps({
  badge: { type: String, required: true },
  title: { type: String, required: true },
  /* [{ hash, label }] — the trail is the only breadcrumb in the product */
  trail: { type: Array, default: () => [] },
  address: { type: String, default: '' }
})

defineEmits(['back', 'close'])

const sheetRef = ref(null)

onMounted(() => {
  sheetRef.value?.focus({ preventScroll: true })
})
</script>
