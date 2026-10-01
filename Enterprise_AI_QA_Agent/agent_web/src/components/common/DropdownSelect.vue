<script setup lang="ts">
import { computed, h } from "vue";
import { NDropdown, type DropdownProps } from "naive-ui";

type DropdownValue = string | number;

interface DropdownSelectOption {
  label: string;
  value: DropdownValue;
  disabled?: boolean;
}

const props = withDefaults(defineProps<{
  modelValue: DropdownValue | null;
  options: DropdownSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  clearable?: boolean;
  buttonClass?: string;
  menuClass?: string;
  placement?: "top-start" | "top" | "top-end" | "bottom-start" | "bottom" | "bottom-end";
}>(), {
  placeholder: "",
  disabled: false,
  clearable: false,
  buttonClass: "",
  menuClass: "",
  placement: "bottom-start",
});

const emit = defineEmits<{
  "update:modelValue": [value: DropdownValue | null];
}>();

const selectedOption = computed(() =>
  props.options.find((option) => option.value === props.modelValue) ?? null,
);

const displayLabel = computed(() => selectedOption.value?.label || props.placeholder);
const hasSelection = computed(() => selectedOption.value !== null);
const menuProps = computed(() => () => ({
  class: ["app-dropdown-menu", props.menuClass].filter(Boolean).join(" "),
}));

const dropdownOptions = computed(() =>
  props.options.map((option) => ({
    key: option.value,
    disabled: option.disabled,
    label: option.label,
  })),
);

const renderOptionLabel: NonNullable<DropdownProps["renderLabel"]> = (option) => {
  return h("span", { class: "app-dropdown-option-label" }, [
    h("span", { class: "app-dropdown-option-text" }, String(option.label || "")),
    option.key === props.modelValue
      ? h("i", { class: "fa-solid fa-check app-dropdown-option-check" })
      : null,
  ]);
};

function handleSelect(key: string | number) {
  emit("update:modelValue", key);
}

function clearValue() {
  emit("update:modelValue", null);
}
</script>

<template>
  <n-dropdown
    trigger="click"
    :placement="placement"
    :options="dropdownOptions"
    :disabled="disabled"
    :menu-props="menuProps"
    :render-label="renderOptionLabel"
    @select="handleSelect"
  >
    <button
      type="button"
      class="dropdown-select-button"
      :class="[buttonClass, { 'is-placeholder': !hasSelection }]"
      :disabled="disabled"
      :title="displayLabel"
    >
      <span class="dropdown-select-label">{{ displayLabel }}</span>
      <span
        v-if="clearable && hasSelection && !disabled"
        class="dropdown-select-clear"
        role="button"
        tabindex="0"
        aria-label="Clear selection"
        @click.stop="clearValue"
        @keydown.enter.stop.prevent="clearValue"
        @keydown.space.stop.prevent="clearValue"
      >
        x
      </span>
      <i class="fa-solid fa-chevron-down dropdown-select-caret"></i>
    </button>
  </n-dropdown>
</template>
