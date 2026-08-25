<script setup lang="ts">
/**
 * 外观设置面板：设计风格（Miuix / Material 3 Expressive）+ 明暗模式。
 * 在顶栏弹层与「我的」页复用；样式走 token，两种设计下均自适应。
 *
 * 分段控件采用 Apple 式滑动拇指（sliding thumb）：选中背景是一整枚
 * 滑块，切换时从旧选项弹到新选项，而不是各自淡入淡出 —— 空间连续。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useThemeStore } from "@/stores/theme";
import type { DesignId, ModeId } from "@/stores/theme";

const theme = useThemeStore();

const designs: { id: DesignId; label: string; hint?: string }[] = [
  { id: "miuix", label: "Miuix" },
  { id: "md3e", label: "Material 3 Expressive" },
];

const modes: { id: ModeId; label: string }[] = [
  { id: "light", label: "浅色" },
  { id: "dark", label: "深色" },
  { id: "system", label: "跟随系统" },
];

/* ---- 滑块测量（两组各一份） ---- */
function makeThumb() {
  const box = ref({ x: 0, w: 0 });
  /** 首次定位完成前禁用过渡，避免滑块初始飞入 */
  const ready = ref(false);
  const els = new Map<string, HTMLElement>();
  let host: HTMLElement | null = null;
  function setHost(e: unknown) {
    if (e instanceof HTMLElement) host = e;
  }
  function setItem(value: string, e: unknown) {
    if (e instanceof HTMLElement) els.set(value, e);
  }
  function move(active: string) {
    const el = els.get(active);
    if (!el) return;
    // offsetLeft 相对最近的定位祖先（.seg 为 position:relative）
    box.value = { x: el.offsetLeft, w: el.offsetWidth };
  }
  return { box, ready, setHost, setItem, move };
}

const {
  box: designBox,
  ready: designReady,
  setHost: onDesignHost,
  setItem: onDesignItem,
  move: moveDesign,
} = makeThumb();
const {
  box: modeBox,
  ready: modeReady,
  setHost: onModeHost,
  setItem: onModeItem,
  move: moveMode,
} = makeThumb();

function moveAll() {
  moveDesign(theme.design);
  moveMode(theme.mode);
}

watch([() => theme.design, () => theme.mode], () => void nextTick(moveAll));

let measureRaf = 0;
function scheduleMove() {
  cancelAnimationFrame(measureRaf);
  measureRaf = requestAnimationFrame(moveAll);
}

onMounted(() => {
  scheduleMove();
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      designReady.value = true;
      modeReady.value = true;
    }),
  );
  window.addEventListener("resize", scheduleMove);
});
onBeforeUnmount(() => {
  cancelAnimationFrame(measureRaf);
  window.removeEventListener("resize", scheduleMove);
});
</script>

<template>
  <div class="ap">
    <div class="grp">
      <div class="lbl">设计风格</div>
      <div class="seg" :ref="onDesignHost" role="radiogroup" aria-label="设计风格">
        <span
          class="seg-thumb"
          :class="{ anim: designReady }"
          :style="{ transform: `translateX(${designBox.x}px)`, width: `${designBox.w}px` }"
          aria-hidden="true"
        ></span>
        <button
          v-for="d in designs"
          :key="d.id"
          type="button"
          role="radio"
          :aria-checked="theme.design === d.id"
          class="seg-item"
          :class="{ on: theme.design === d.id }"
          :ref="(el) => onDesignItem(d.id, el)"
          @click="theme.setDesign(d.id)"
        >
          {{ d.label }}
        </button>
      </div>
    </div>

    <div class="grp">
      <div class="lbl">外观</div>
      <div class="seg" :ref="onModeHost" role="radiogroup" aria-label="外观模式">
        <span
          class="seg-thumb"
          :class="{ anim: modeReady }"
          :style="{ transform: `translateX(${modeBox.x}px)`, width: `${modeBox.w}px` }"
          aria-hidden="true"
        ></span>
        <button
          v-for="m in modes"
          :key="m.id"
          type="button"
          role="radio"
          :aria-checked="theme.mode === m.id"
          class="seg-item"
          :class="{ on: theme.mode === m.id }"
          :ref="(el) => onModeItem(m.id, el)"
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
  position: relative;
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  background: var(--m-color-surface-container-high);
}
.seg-thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 0;
  border-radius: 999px;
  background: var(--m-color-surface);
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.12),
    0 0 0 1px color-mix(in srgb, var(--m-color-outline) 35%, transparent);
  pointer-events: none;
  will-change: transform, width;
}
.seg-thumb.anim {
  transition:
    transform 0.5s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1)),
    width 0.5s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
}
@media (prefers-reduced-motion: reduce) {
  .seg-thumb.anim {
    transition: none;
  }
}
.seg-item {
  position: relative;
  z-index: 1;
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
}
@media (prefers-reduced-motion: no-preference) {
  .seg-item {
    transition:
      color 0.15s ease-out,
      transform 0.35s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
  }
  .seg-item:active {
    transform: scale(0.96);
  }
}
.seg-item.on {
  color: var(--m-color-on-surface);
  font-weight: 600;
}
</style>
