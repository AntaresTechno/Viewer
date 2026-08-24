<script setup lang="ts">
/**
 * 密码输入框：带「显示/隐藏」切换。
 *
 * miuix-vue 0.1.x 的 MiuixInput 不支持 type="password"，因此这里
 * 手写同样的 DOM 结构并复用 miuix 的类名（.m-input / .m-input__label /
 * .m-input__field），让 miuix 与 design.css 里两套设计主题的输入框
 * 样式规则（填充底 / md3 外描边 / 聚焦环）自动作用于本组件，
 * 保证与用户名等其它输入框视觉完全一致。
 */
import { computed, ref } from "vue";

const props = defineProps<{
  modelValue: string;
  label?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [v: string] }>();

const show = ref(false);
// 与 MiuixInput 的浮动标签行为一致：有值时标签上浮缩小
const floating = computed(() => !!props.label && props.modelValue.length > 0);

function onInput(e: Event) {
  emit("update:modelValue", (e.target as HTMLInputElement).value);
}
</script>

<template>
  <label class="m-input pw-shell">
    <span class="m-input__content">
      <span
        v-if="label"
        class="m-input__label pw-label"
        :class="{ 'm-input__label--floating': floating }"
        :style="
          floating ? 'transform:translateY(-8px);font-size:10px' : undefined
        "
      >{{ label }}</span>
      <input
        class="m-input__field pw-field"
        :class="{ floated: floating }"
        :type="show ? 'text' : 'password'"
        :value="modelValue"
        autocomplete="current-password"
        @input="onInput"
      />
    </span>
    <button type="button" class="pw-eye" tabindex="-1" @click="show = !show">
      {{ show ? "隐藏" : "显示" }}
    </button>
  </label>
</template>

<style scoped>
/* 浮动标签与字段内边距的过渡（对齐 MiuixInput 的两端状态） */
.pw-label {
  transition:
    transform 0.15s ease,
    font-size 0.15s ease;
}
/* 字段内边距对齐 MiuixInput 的两端状态（未浮动 16/16，浮动后 24/8） */
.pw-field {
  padding-top: 16px;
  padding-bottom: 16px;
  transition: padding 0.15s ease;
}
.pw-field.floated {
  padding-top: 24px;
  padding-bottom: 8px;
}

/* 尾部「显示/隐藏」按钮，形制对齐 .m-input__icon--trailing */
.pw-eye {
  align-self: stretch;
  border: 0;
  background: transparent;
  color: var(--m-color-on-secondary-container);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  padding: 0 16px 0 6px;
}
.pw-eye:hover {
  color: var(--m-color-primary);
}

/* miuix 填充式设计的聚焦反馈；md3 下由 design.css 的
   .m-input 描边覆盖（!important）接管，互不冲突。 */
.pw-shell:focus-within {
  box-shadow: inset 0 0 0 2px var(--m-color-primary);
}
</style>
