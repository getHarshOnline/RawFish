<template>
  <!-- Z4 — machine truth only. 34px status line; the log exists only while
       a run streams (or when summoned). No prose, no actions (board 03). -->
  <section class="rf-console" aria-label="System console">
    <div class="rf-console__line">
      <span class="rf-console__sys">system</span>
      <span :class="['rf-console__dot', dot === 'live' ? 'rf-console__dot--live' : null, dot === 'danger' ? 'rf-console__dot--danger' : null]"></span>
      <span class="rf-console__text">{{ line }}</span>
      <button
        v-if="logs.length"
        type="button"
        class="rf-console__toggle"
        :aria-expanded="open"
        @click="$emit('toggle')"
      >
        {{ open ? 'hide ▾' : 'show ▸' }}
      </button>
    </div>
    <div v-if="open && logs.length" class="rf-console__log">
      <div v-for="log in logs" :key="log.id" class="rf-console__row">
        <span class="rf-console__time">{{ log.time }}</span>
        <span :class="['rf-console__tag', log.kind === 'accent' ? 'rf-console__tag--accent' : null, log.kind === 'danger' ? 'rf-console__tag--danger' : null]">{{ log.tag }}</span>
        <span class="rf-console__msg">{{ log.msg }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
defineProps({
  line: { type: String, required: true },
  /* idle | live | danger */
  dot: { type: String, default: 'idle' },
  open: { type: Boolean, default: false },
  /* [{ id, time, tag, msg, kind: default|accent|danger }] */
  logs: { type: Array, default: () => [] }
})

defineEmits(['toggle'])
</script>
