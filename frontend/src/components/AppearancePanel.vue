<script setup lang="ts">
/**
 * 外观设置面板：设计风格（Miuix / Material 3）+ 明暗模式。
 * 在顶栏弹层与「我的」页复用；样式走 token，两种设计下均自适应。
 */
import { useThemeStore } from "@/stores/theme";
import type { DesignId, ModeId } from "@/stores/theme";

const theme = useThemeStore();

const designs: { id: DesignId; label: string; hint?: string }[] = [
  { id: "miuix", label: "Miuix" },
  { id: "md3", label: "Material You" },
];

const modes: { id: ModeId; label: string }[] = [
  { id: "light", label: "浅色" },
  { id: "dark", label: "深色" },
  { id: "system", label: "跟随系统" },
];
</script>

<template>
  <div class="ap">
    <div class="grp">
      <div class="lbl">设计风格</div>
      <div class="seg" role="radiogroup" aria-label="设计风格">
        <button
          v-for="d in designs"
          :key="d.id"
          type="button"
          class="seg-item"
          :class="{ on: theme.design === d.id }"
          @click="theme.setDesign(d.id)"
        >
          {{ d.label }}
        </button>
      </div>
    </div>

    <div class="grp">
      <div class="lbl">外观</div>
      <div class="seg" role="radiogroup" aria-label="外观模式">
        <button
          v-for="m in modes"
          :key="m.id"
          type="button"
          class="seg-item"
          :class="{ on: theme.mode === m.id }"
          @click="theme.setMode(m.id, $event)"
        >
          {{ m.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ap {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.grp {
  min-width: 0;
}
.lbl {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
  margin-bottom: 6px;
}
.seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  background: var(--m-color-surface-container-high);
}
.seg-item {
  flex: 1;
  border: 0;
  background: transparent;
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 13px;
  font-family: inherit;
  color: var(--m-color-on-surface-secondary);
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}
.seg-item:hover:not(.on) {
  background: var(--m-color-surface-container-highest);
}
.seg-item.on {
  background: var(--m-color-secondary-container);
  color: var(--m-color-on-secondary-container);
  font-weight: 600;
}
</style>
