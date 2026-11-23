<template>
  <!-- One workbench field — the right control for the shape of the data
       (board 09). Frozen renders the value with no input chrome. -->
  <RfField
    :label="field.label || field.id"
    :meta-left="impact"
    :meta-right="provenance"
    :frozen="frozen"
    :frozen-value="frozenDisplay"
    :frozen-reason="frozenReason"
  >
    <RfSliderField
      v-if="field.control === 'slider_number'"
      :model-value="numericValue"
      :min="numberOr(field.min, 0)"
      :max="numberOr(field.max, 1)"
      :step-size="numberOr(field.step, field.kind === 'integer' ? 1 : 0.01)"
      :integer="field.kind === 'integer'"
      @update:model-value="set($event)"
    />
    <RfNumberField
      v-else-if="field.control === 'numeric'"
      :model-value="numericValue"
      :min="numberOrNull(field.min)"
      :max="numberOrNull(field.max)"
      :step-size="numberOr(field.step, 1)"
      :integer="field.kind === 'integer'"
      @update:model-value="set($event)"
    />
    <RfEnumChips
      v-else-if="field.control === 'select' && (field.options || []).length <= 5"
      :model-value="value"
      :options="enumOptions"
      @update:model-value="set($event)"
    />
    <select
      v-else-if="field.control === 'select'"
      class="rf-input--machine"
      :value="value"
      @change="set($event.target.value)"
    >
      <option v-for="option in field.options || []" :key="option.value" :value="option.value">
        {{ option.label }}
      </option>
    </select>
    <div v-else-if="field.control === 'checkbox_group'" class="rf-checks">
      <label v-for="option in field.options || []" :key="option.value">
        <input
          type="checkbox"
          :checked="arrayValue.includes(option.value)"
          @change="toggle(option.value)"
        >
        {{ option.label }}
      </label>
    </div>
    <div v-else-if="field.control === 'active_hours_grid'" class="rf-hours">
      <button
        v-for="hour in hours"
        :key="hour"
        type="button"
        :aria-pressed="arrayValue.includes(hour) ? 'true' : 'false'"
        @click="toggle(hour)"
      >
        {{ hour }}
      </button>
    </div>
    <div v-else-if="isObjectValue" class="rf-kv">
      <label v-for="[key, entry] in Object.entries(value || {})" :key="key">
        <span>{{ key }}</span>
        <input :value="entry" @change="setObject(key, $event.target.value)">
      </label>
    </div>
    <input
      v-else
      type="text"
      class="rf-input--machine"
      :value="value"
      @change="set($event.target.value)"
    >
  </RfField>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfEnumChips, RfField, RfNumberField, RfSliderField } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const props = defineProps({
  field: { type: Object, required: true },
  frozen: { type: Boolean, default: false },
  frozenReason: { type: String, default: 'frozen · duplicate as draft to edit' }
})

const store = inject(RUN_STORE_KEY)
const { fieldValue, setFieldValue, toggleArrayValue, setObjectValue, impactCopy, fieldProvenance } = store

const hours = Array.from({ length: 24 }, (_, index) => index)

const value = computed(() => fieldValue(props.field))
const numericValue = computed(() => (Number.isFinite(Number(value.value)) ? Number(value.value) : numberOr(props.field.min, 0)))
const arrayValue = computed(() => (Array.isArray(value.value) ? value.value : []))
const isObjectValue = computed(() => (
  props.field.control === 'key_value' ||
  (Boolean(value.value) && typeof value.value === 'object' && !Array.isArray(value.value))
))
const impact = computed(() => impactCopy(props.field) || '')
const provenance = computed(() => fieldProvenance(props.field).join(' · '))
const enumOptions = computed(() => (props.field.options || []).map(option => ({
  id: option.value,
  label: option.label
})))
const frozenDisplay = computed(() => {
  if (Array.isArray(value.value)) return `${value.value.length} entries`
  if (value.value && typeof value.value === 'object') return `${Object.keys(value.value).length} keys`
  return value.value === undefined || value.value === null || value.value === '' ? 'unset' : String(value.value)
})

function set (next) { setFieldValue(props.field, next) }
function toggle (entry) { toggleArrayValue(props.field, entry) }
function setObject (key, entry) { setObjectValue(props.field, key, entry) }
function numberOr (candidate, fallback) {
  return Number.isFinite(Number(candidate)) ? Number(candidate) : fallback
}
function numberOrNull (candidate) {
  return Number.isFinite(Number(candidate)) ? Number(candidate) : null
}
</script>
