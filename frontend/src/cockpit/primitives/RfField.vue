<template>
  <!-- Field grammar (board 09): label above · meta line owns char counts and
       parse state · errors arrive as {loc · msg · code} placed at the field ·
       frozen renders the value with no input chrome + a reason chip. -->
  <label v-if="!frozen" :class="['rf-field', error ? 'rf-field--invalid' : null]">
    <span v-if="label" class="rf-field__label">{{ label }}</span>
    <slot />
    <span v-if="metaLeft || metaRight" class="rf-field__meta">
      <span>{{ metaLeft }}</span>
      <span>{{ metaRight }}</span>
    </span>
    <span v-if="error" class="rf-field__error">{{ error }}</span>
  </label>
  <div v-else class="rf-field">
    <span v-if="label" class="rf-field__label">{{ label }}</span>
    <span class="rf-frozen">
      <span class="rf-frozen__value"><slot name="frozen">{{ frozenValue }}</slot></span>
      <span class="rf-chip" role="note">{{ frozenReason }}</span>
    </span>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, default: '' },
  metaLeft: { type: String, default: '' },
  metaRight: { type: String, default: '' },
  /* rendered as loc · msg · code by the caller */
  error: { type: String, default: '' },
  frozen: { type: Boolean, default: false },
  frozenValue: { type: [String, Number], default: '' },
  frozenReason: { type: String, default: 'frozen · duplicate as draft to edit' }
})
</script>
